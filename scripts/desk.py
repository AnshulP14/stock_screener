#!/usr/bin/env python3
"""Advisory desk entry point. No broker execution."""

from screener.desk_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
