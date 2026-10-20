"""Sleeper Agent - Autonomous Fantasy Football Assistant."""

import sys
from pathlib import Path

# Ensure src root is in python path
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

__version__ = "0.1.0"
