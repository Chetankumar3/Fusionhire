/**
 * Resume Parser — Part A (Node / pdf.js).
 *
 * Uses pdf.js (the same PDF engine OpenResume's parser is built on) to read each
 * PDF in `data_sources/resumes/` positionally — recovering word spacing that
 * naive text extraction loses — then emits an intermediate RAW JSON per resume
 * to `parsers/resume_parser/raw_json/`. Part B (Python) consumes that JSON,
 * applies the shared normalizers, and writes the final parsed record.
 *
 * Part A intentionally stays "dumb": it reconstructs lines, segments sections by
 * heading, and pulls hyperlink URLs (with their anchor label) from PDF link
 * annotations. All field *semantics* and normalization happen in Part B.
 *
 * NOTE (see README): a faithful end-to-end vendor of OpenResume's TS extraction
 * pipeline was descoped under the time budget; we use its underlying pdf.js
 * positional engine for clean text + a heading segmenter, which is the reliable,
 * load-bearing part for this resume set.
 *
 * Run:  npm run parse        (from parsers/resume_parser/)
 */

import { promises as fs } from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RESUMES_DIR = path.resolve(__dirname, "../../data_sources/resumes");
const OUT_DIR = path.resolve(__dirname, "raw_json");

const LINE_Y_TOLERANCE = 4; // units; items within this y delta share a line

/** Read one PDF into positioned text items + link annotations. */
async function readPdf(filePath) {
  const data = new Uint8Array(await fs.readFile(filePath));
  const doc = await getDocument({ data, useSystemFonts: true }).promise;
  const items = [];
  const links = [];

  for (let p = 1; p <= doc.numPages; p++) {
    const page = await doc.getPage(p);
    const content = await page.getTextContent();
    const pageItems = [];
    for (const it of content.items) {
      if (!("str" in it)) continue;
      const x = it.transform[4];
      const y = it.transform[5];
      const item = { str: it.str, x, y, w: it.width, h: it.height, page: p };
      items.push(item);
      pageItems.push(item);
    }

    // Link annotations carry the real URLs; pair each with its anchor text.
    const annotations = await page.getAnnotations();
    for (const a of annotations) {
      const url = a.url || a.unsafeUrl;
      if (!url || !a.rect) continue;
      const [ax1, ay1, ax2, ay2] = a.rect;
      const cx = (ax1 + ax2) / 2;
      const cy = (ay1 + ay2) / 2;
      // Anchor text = items whose center lies inside the annotation rect.
      const anchorItems = pageItems.filter((t) => {
        const tx = t.x + t.w / 2;
        const ty = t.y + t.h / 2;
        return tx >= ax1 - 1 && tx <= ax2 + 1 && ty >= ay1 - 1 && ty <= ay2 + 1;
      });
      const anchor = anchorItems.map((t) => t.str).join("").trim();
      links.push({ url, anchor });
    }
  }
  return { items, links };
}

/** Group items into visual lines (top-to-bottom), reconstructing spacing. */
function groupIntoLines(items) {
  const sorted = [...items].sort((a, b) => (b.y - a.y) || (a.x - b.x));
  const lines = [];
  let current = null;

  for (const it of sorted) {
    if (!current || Math.abs(it.y - current.y) > LINE_Y_TOLERANCE) {
      current = { y: it.y, items: [] };
      lines.push(current);
    }
    current.items.push(it);
  }

  return lines.map((line) => {
    const ordered = line.items.sort((a, b) => a.x - b.x);
    let text = "";
    let prev = null;
    for (const it of ordered) {
      if (prev) {
        const gap = it.x - (prev.x + prev.w);
        const needsSpace =
          gap > 1.2 &&
          !text.endsWith(" ") &&
          !it.str.startsWith(" ");
        if (needsSpace) text += " ";
      }
      text += it.str;
      prev = it;
    }
    return { y: line.y, text: text.replace(/\s+/g, " ").trim() };
  }).filter((l) => l.text.length > 0);
}

/** A line is a heading if it is short and overwhelmingly uppercase. */
function isHeading(text) {
  const letters = text.replace(/[^A-Za-z]/g, "");
  if (letters.length < 2 || text.length > 45) return false;
  const upper = (text.match(/[A-Z]/g) || []).length;
  return upper / letters.length >= 0.6;
}

/** Split lines into { HEADING: [lines] }, with pre-heading lines under HEADER. */
function segmentSections(lines) {
  const sections = {};
  let currentKey = "HEADER";
  sections[currentKey] = [];
  for (const line of lines) {
    if (isHeading(line.text)) {
      currentKey = line.text.toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (!sections[currentKey]) sections[currentKey] = [];
    } else {
      sections[currentKey].push(line.text);
    }
  }
  return sections;
}

async function parseOne(filePath) {
  const { items, links } = await readPdf(filePath);
  const lines = groupIntoLines(items);
  const sections = segmentSections(lines);
  return {
    source_file: path.basename(filePath),
    lines: lines.map((l) => l.text),
    sections,
    links, // [{ url, anchor }]
    raw_text: lines.map((l) => l.text).join("\n"),
  };
}

async function main() {
  await fs.mkdir(OUT_DIR, { recursive: true });
  let entries = [];
  try {
    entries = await fs.readdir(RESUMES_DIR);
  } catch {
    console.error(`[partA] resumes dir not found: ${RESUMES_DIR}`);
    return;
  }
  const pdfs = entries.filter((f) => f.toLowerCase().endsWith(".pdf"));
  if (pdfs.length === 0) {
    console.log("[partA] no PDFs found");
    return;
  }

  for (const pdf of pdfs) {
    const filePath = path.join(RESUMES_DIR, pdf);
    try {
      const result = await parseOne(filePath);
      const outName = `${path.basename(pdf, path.extname(pdf))}.json`;
      const outPath = path.join(OUT_DIR, outName);
      await fs.writeFile(outPath, JSON.stringify(result, null, 2), "utf-8");
      console.log(`[partA] wrote ${outPath}`);
    } catch (err) {
      // Degrade gracefully: one bad PDF must not abort the rest.
      console.error(`[partA] failed on ${pdf}: ${err.message}`);
    }
  }
}

main();
