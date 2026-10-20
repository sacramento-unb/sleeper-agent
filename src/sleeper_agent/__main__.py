"""Main module entrypoint for python -m sleeper_agent."""

import sys
from pathlib import Path

# Ensure src root is in python path
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from sleeper_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
