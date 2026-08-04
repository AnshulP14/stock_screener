#!/usr/bin/env python3
"""Navigate long S&P 10-K filings without reading them whole.

Build the durable per-filing index (normalized .txt + .index.json) once, then use
the outline -> grep -> read loop to drill down.

    # one-time (or after a re-fetch): build indexes
    python scripts/filings.py index --all
    python scripts/filings.py index --symbols AAPL GOOGL

    # navigate
    python scripts/filings.py filings AAPL              # which years are on disk
    python scripts/filings.py outline AAPL              # latest filing's section map
    python scripts/filings.py outline AAPL --fy 2024
    python scripts/filings.py grep AAPL "tariff|China"  # search
    python scripts/filings.py read AAPL 15              # read page 15
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

from screener import html_filings as hf
from screener.config import SNP_ANNUAL_REPORTS_DIR


def _iter_htm(symbols: list[str] | None) -> list[Path]:
    base = Path(SNP_ANNUAL_REPORTS_DIR)
    if symbols:
        out: list[Path] = []
        for s in symbols:
            out.extend(hf.list_filings(s.upper(), base))
        return out
    return sorted(base.glob("*/*_10K_*.htm"))


def cmd_index(args) -> None:
    htms = _iter_htm(args.symbols)
    if not htms:
        print("No .htm filings found.", file=sys.stderr)
        sys.exit(1)
    built, failed, empty = 0, [], []
    for htm in tqdm(htms, desc="Indexing filings"):
        try:
            idx = hf.build_index(htm)
            if not idx["sections"]:
                empty.append(htm.name)
            else:
                built += 1
        except Exception as exc:                          # keep going
            failed.append((htm.name, str(exc)))
        if len(failed) > 20 and len(empty) > 20:
            break
    _emit({"indexed": built, "empty_toc": empty[:20],
           "failed": [{"file": n, "error": e[:80]} for n, e in failed[:20]]})


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_filings(args) -> None:
    _emit(hf.filings(args.symbol, base_dir=Path(SNP_ANNUAL_REPORTS_DIR)))


def cmd_outline(args) -> None:
    _emit(hf.outline(args.symbol, args.fy, base_dir=Path(SNP_ANNUAL_REPORTS_DIR)))


def cmd_grep(args) -> None:
    _emit(hf.grep(args.symbol, args.pattern, args.fy,
                  offset=args.offset, limit=args.limit, context=args.context,
                  page_min=args.start_page, page_max=args.end_page,
                  base_dir=Path(SNP_ANNUAL_REPORTS_DIR)))


def cmd_read(args) -> None:
    _emit(hf.read_page(args.symbol, args.page, args.fy,
                       base_dir=Path(SNP_ANNUAL_REPORTS_DIR)))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="build normalized text + section index")
    pi.add_argument("--symbols", nargs="+", help="limit to these tickers")
    pi.add_argument("--all", action="store_true", help="index every filing on disk")
    pi.set_defaults(func=cmd_index)

    pf = sub.add_parser("filings", help="which years are on disk")
    pf.add_argument("symbol")
    pf.set_defaults(func=cmd_filings)

    po = sub.add_parser("outline", help="sections this report actually has")
    po.add_argument("symbol")
    po.add_argument("--fy", type=int)
    po.set_defaults(func=cmd_outline)

    pg = sub.add_parser("grep", help="search, with page-addressed hits")
    pg.add_argument("symbol")
    pg.add_argument("pattern")
    pg.add_argument("--fy", type=int)
    pg.add_argument("--offset", type=int, default=0)
    pg.add_argument("--limit", type=int, default=10)
    pg.add_argument("--start-page", type=int, help="only search from this page")
    pg.add_argument("--end-page", type=int, help="only search up to this page")
    pg.add_argument("--context", type=int, default=140)
    pg.set_defaults(func=cmd_grep)

    pr = sub.add_parser("read", help="read exactly one page")
    pr.add_argument("symbol")
    pr.add_argument("page", type=int)
    pr.add_argument("--fy", type=int)
    pr.set_defaults(func=cmd_read)

    args = p.parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
