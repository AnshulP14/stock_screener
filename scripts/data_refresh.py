#!/usr/bin/env python3
"""
Unified data refresh — thin wrapper over screener.cli.

Usage:
    python scripts/data_refresh.py
    python scripts/data_refresh.py --market nse --mode full
    python scripts/data_refresh.py --market us --mode full
    python scripts/data_refresh.py --market nse --symbols RELIANCE TCS
    python scripts/data_refresh.py --market us --dry-run
"""

from screener.cli import main

if __name__ == "__main__":
    main()
