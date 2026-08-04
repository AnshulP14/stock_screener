#!/usr/bin/env python3
"""Navigate NSE annual report PDFs.

Build the per-report index once (page-addressed sections + normalized text), then drive
the loop: filings -> outline -> grep -> read one page at a time.

Sections are whatever the document calls them -- run `outline` first to see the real
titles before asking for one.

Usage:
    python scripts/nse_filings.py index --symbols RELIANCE TCS
    python scripts/nse_filings.py index --all
    python scripts/nse_filings.py filings RELIANCE
    python scripts/nse_filings.py outline RELIANCE [--fy 2024]
    python scripts/nse_filings.py grep RELIANCE "capex|expansion" [--offset 10]
    python scripts/nse_filings.py grep RELIANCE "capex" --start-page 40 --end-page 55
    python scripts/nse_filings.py read RELIANCE 47 [--fy 2024]
"""

import argparse
import json
import sys

from screener import pdf_filings as pf


def _emit(obj):
    json.dump(obj, sys.stdout, indent=1, ensure_ascii=False)
    print()


def _index(args):
    if args.all:
        pdfs = sorted(pf.REPORTS_DIR.glob("*/*_AR_*.pdf"))
    else:
        pdfs = [p for s in args.symbols
                for p in sorted(pf.REPORTS_DIR.glob(f"{s}/{s}_AR_*.pdf"))]
    if not pdfs:
        sys.exit("no matching PDFs found")

    done, failed, empty = 0, [], []
    for k, pdf in enumerate(pdfs, 1):
        try:
            idx = pf.build_index(pdf)
            done += 1
            # a long report with no headings has no text layer -- it is a scan
            if idx["section_count"] <= 1 and idx["pages"] > 20:
                empty.append(f"{idx['symbol']} FY{idx['fy']} ({idx['pages']}p)")
        except Exception as exc:                       # keep going; report at the end
            failed.append({"file": pdf.name, "error": str(exc)[:80]})
        if k % 50 == 0:
            print(f"  ...{k}/{len(pdfs)}", file=sys.stderr, flush=True)
    _emit({"indexed": done, "failed": len(failed),
           "no_text_layer": empty[:20], "errors": failed[:10]})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="build .txt + .index.json next to each PDF")
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("filings", help="which years are on disk")
    p.add_argument("symbol")

    p = sub.add_parser("outline", help="sections this report actually has")
    p.add_argument("symbol")
    p.add_argument("--fy", type=int)

    p = sub.add_parser("grep", help="search, with page-addressed hits")
    p.add_argument("symbol")
    p.add_argument("pattern")
    p.add_argument("--fy", type=int)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--start-page", type=int, help="only search from this page")
    p.add_argument("--end-page", type=int, help="only search up to this page")

    p = sub.add_parser("read", help="read exactly one page")
    p.add_argument("symbol")
    p.add_argument("page", type=int)
    p.add_argument("--fy", type=int)

    args = ap.parse_args()
    try:
        if args.cmd == "index":
            if not args.all and not args.symbols:
                sys.exit("pass --symbols or --all")
            _index(args)
        elif args.cmd == "filings":
            _emit(pf.filings(args.symbol))
        elif args.cmd == "outline":
            _emit(pf.outline(args.symbol, args.fy))
        elif args.cmd == "grep":
            _emit(pf.grep(args.symbol, args.pattern, args.fy,
                          offset=args.offset, limit=args.limit,
                          start_page=args.start_page, end_page=args.end_page))
        elif args.cmd == "read":
            _emit(pf.read_page(args.symbol, args.page, args.fy))
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
