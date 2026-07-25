#!/usr/bin/env python3
"""
Unified data refresh — thin wrapper over cli.py.

Preserves original CLI interface for backward compatibility.

Usage:
    python scripts/data_refresh.py
    python scripts/data_refresh.py --market nse --mode full
    python scripts/data_refresh.py --market us --mode full
    python scripts/data_refresh.py --market nse --symbols RELIANCE TCS
    python scripts/data_refresh.py --market us --dry-run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main

if __name__ == "__main__":
    main()
