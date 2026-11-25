"""Wrapper alias for scripts/run_sage_ablations.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path if needed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_sage_ablations import main

if __name__ == "__main__":
    sys.exit(main())
