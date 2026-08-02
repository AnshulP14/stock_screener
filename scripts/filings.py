#!/usr/bin/env python3
"""Navigate long 10-K filings without reading them whole.

Build the durable per-filing index (normalized .txt + .index.json) once, then use
the outline -> grep -> read loop to drill down.

    # one-time (or after a re-fetch): build indexes, no re-download
    python scripts/filings.py index --all
    python scripts/filings.py index --symbols AAPL GOOGL

    # navigate
    python scripts/filings.py outline AAPL                 # latest filing's section map
    python scripts/filings.py outline AAPL --fy 2024
    python scripts/filings.py read AAPL 1A --fy 2024        # windowed section read
    python scripts/filings.py read AAPL 1A --offset 6000
    python scripts/filings.py grep AAPL "tariff|China" --item 1A
    python scripts/filings.py compare AAPL 1A               # Item 1A size across years
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

from screener import filings
from screener.config import SNP_ANNUAL_REPORTS_DIR


def _iter_htm(symbols: list[str] | None) -> list[Path]:
    base = Path(SNP_ANNUAL_REPORTS_DIR)
    if symbols:
        out: list[Path] = []
        for s in symbols:
            out.extend(filings.list_filings(s.upper(), base))
        return out
    return sorted(base.glob("*/*_10K_*.htm"))


def cmd_index(args) -> None:
    htms = _iter_htm(args.symbols)
    if not htms:
        print("No .htm filings found.", file=sys.stderr)
        sys.exit(1)
    built, failed = 0, []
    for htm in tqdm(htms, desc="Indexing filings"):
        try:
            idx = filings.build_index(htm)
            if not idx["sections"]:
                failed.append((htm.name, "no sections detected"))
            else:
                built += 1
        except Exception as e:  # noqa: BLE001
            failed.append((htm.name, str(e)))
    print(f"\nIndexed {built}/{len(htms)} filings.")
    if failed:
        print(f"{len(failed)} problems:")
        for name, why in failed[:20]:
            print(f"  {name}: {why}")


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_outline(args) -> None:
    _emit(filings.outline(args.symbol, args.fy))


def cmd_read(args) -> None:
    _emit(filings.read_section(args.symbol, args.item, fy=args.fy,
                               offset=args.offset, limit=args.limit))


def cmd_grep(args) -> None:
    _emit(filings.grep(args.symbol, args.pattern, fy=args.fy,
                       item_id=args.item, context=args.context))


def cmd_compare(args) -> None:
    _emit(filings.compare_section(args.symbol, args.item))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="build normalized text + section index")
    pi.add_argument("--symbols", nargs="+", help="limit to these tickers (default: all)")
    pi.add_argument("--all", action="store_true", help="index every filing on disk")
    pi.set_defaults(func=cmd_index)

    po = sub.add_parser("outline", help="section map with word counts")
    po.add_argument("symbol")
    po.add_argument("--fy", type=int)
    po.set_defaults(func=cmd_outline)

    pr = sub.add_parser("read", help="windowed read of one item")
    pr.add_argument("symbol")
    pr.add_argument("item", help="item id, e.g. 1A, 7")
    pr.add_argument("--fy", type=int)
    pr.add_argument("--offset", type=int, default=0)
    pr.add_argument("--limit", type=int, default=6000)
    pr.set_defaults(func=cmd_read)

    pg = sub.add_parser("grep", help="regex search returning addressed hits")
    pg.add_argument("symbol")
    pg.add_argument("pattern")
    pg.add_argument("--fy", type=int)
    pg.add_argument("--item", help="restrict to one item id")
    pg.add_argument("--context", type=int, default=160)
    pg.set_defaults(func=cmd_grep)

    pc = sub.add_parser("compare", help="one item's size across years")
    pc.add_argument("symbol")
    pc.add_argument("item")
    pc.set_defaults(func=cmd_compare)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
