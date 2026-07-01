import os
import sys

# The pipeline packages live under backend/. Put it on sys.path so tests can
# import paths/parsers/merge_engine/projector regardless of where pytest runs.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
sys.path.insert(0, _ROOT)
