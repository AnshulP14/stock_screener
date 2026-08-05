#!/usr/bin/env python3
"""
Unified data refresh — thin wrapper over screener.cli.

Usage:
    python scripts/data_refresh.py
    python scripts/data_refresh.py --market nse --mode full-sync
    python scripts/data_refresh.py --market snp --mode full-sync
    python scripts/data_refresh.py --market nse --symbols RELIANCE TCS
    python scripts/data_refresh.py --market nse --mode quick-sync --skip-reports
"""

from screener.cli import main

if __name__ == "__main__":
    main()
