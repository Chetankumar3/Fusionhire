import os
import sys

# Put the project root on sys.path so tests can import paths/parsers/merge_engine/projector.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
