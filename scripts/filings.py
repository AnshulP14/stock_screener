#!/usr/bin/env python3
"""Navigate long annual report filings without reading them whole — S&P500
10-Ks (HTML, SEC EDGAR) or NSE annual reports (PDF, screener.in).

Build the durable per-filing index (normalized .txt + .index.json) once, then use
the outline -> grep -> read loop to drill down. `--market` goes before the subcommand.

    # one-time (or after a re-fetch): build indexes
    python scripts/filings.py --market snp index --all
    python scripts/filings.py --market snp index --symbols AAPL GOOGL
    python scripts/filings.py --market nse index --symbols RELIANCE TCS

    # navigate
    python scripts/filings.py --market snp filings AAPL              # which years are on disk
    python scripts/filings.py --market snp outline AAPL              # latest filing's section map
    python scripts/filings.py --market snp outline AAPL --fy 2024
    python scripts/filings.py --market snp grep AAPL "tariff|China"  # search
    python scripts/filings.py --market snp read AAPL 15              # read page 15

    python scripts/filings.py --market nse filings RELIANCE
    python scripts/filings.py --market nse outline RELIANCE --fy 2024
    python scripts/filings.py --market nse grep RELIANCE "capex|expansion"
    python scripts/filings.py --market nse read RELIANCE 47
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

from screener import html_filings as hf
from screener.filings import pdf_filings as pf
from screener.config import SNP_ANNUAL_REPORTS_DIR


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ── S&P500: HTML 10-Ks (screener.html_filings) ──────────────────

def _snp_index(args) -> None:
    base = Path(SNP_ANNUAL_REPORTS_DIR)
    if args.symbols:
        htms = [p for s in args.symbols for p in hf.list_filings(s.upper(), base)]
    else:
        htms = sorted(base.glob("*/*_10K_*.htm"))
    if not htms:
        sys.exit("No .htm filings found.")

    built, failed, empty = 0, [], []
    for htm in tqdm(htms, desc="Indexing filings"):
        try:
            idx = hf.build_index(htm)
            if idx["sections"]:
                built += 1
            else:
                empty.append(htm.name)
        except Exception as exc:  # keep going, report at the end
            failed.append((htm.name, str(exc)))
        if len(failed) > 20 and len(empty) > 20:
            break
    _emit({"indexed": built, "empty_toc": empty[:20],
           "failed": [{"file": n, "error": e[:80]} for n, e in failed[:20]]})




# ── NSE: PDF annual reports (screener.pdf_filings) ──────────────

def _nse_index(args) -> None:
    if args.symbols:
        pdfs = [p for s in args.symbols for p in sorted(pf.REPORTS_DIR.glob(f"{s}/{s}_AR_*.pdf"))]
    else:
        pdfs = sorted(pf.REPORTS_DIR.glob("*/*_AR_*.pdf"))
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
        except Exception as exc:  # keep going, report at the end
            failed.append({"file": pdf.name, "error": str(exc)[:80]})
        if k % 50 == 0:
            print(f"  ...{k}/{len(pdfs)}", file=sys.stderr, flush=True)
    _emit({"indexed": done, "failed": len(failed),
           "no_text_layer": empty[:20], "errors": failed[:10]})




def _dispatch(args, market: str):
    """Call the right filings function for this market and subcommand."""
    base = Path(SNP_ANNUAL_REPORTS_DIR)
    cmd = args.cmd
    if market == "snp":
        if cmd == "index":       return _snp_index(args)
        if cmd == "filings":     return hf.filings(args.symbol, base_dir=base)
        if cmd == "outline":     return hf.outline(args.symbol, args.fy, base_dir=base)
        if cmd == "grep":        return hf.grep(args.symbol, args.pattern, args.fy,
                                                offset=args.offset, limit=args.limit,
                                                context=args.context, start_page=args.start_page,
                                                end_page=args.end_page, base_dir=base)
        if cmd == "read":        return hf.read_page(args.symbol, args.page, args.fy, base_dir=base)
    else:
        if cmd == "index":       _nse_index(args)
        if cmd == "filings":     return pf.filings(args.symbol)
        if cmd == "outline":     return pf.outline(args.symbol, args.fy)
        if cmd == "grep":        return pf.grep(args.symbol, args.pattern, args.fy,
                                                offset=args.offset, limit=args.limit,
                                                context=args.context,
                                                start_page=args.start_page,
                                                end_page=args.end_page)
        if cmd == "read":        return pf.read_page(args.symbol, args.page, args.fy)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--market", choices=["nse", "snp"], required=True, help="Which market")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="build normalized text + section index")
    pi.add_argument("--symbols", nargs="+", help="limit to these tickers")
    pi.add_argument("--all", action="store_true", help="index every filing on disk")

    pfl = sub.add_parser("filings", help="which years are on disk")
    pfl.add_argument("symbol")

    po = sub.add_parser("outline", help="sections this report actually has")
    po.add_argument("symbol")
    po.add_argument("--fy", type=int)

    pg = sub.add_parser("grep", help="search, with page-addressed hits")
    pg.add_argument("symbol")
    pg.add_argument("pattern")
    pg.add_argument("--fy", type=int)
    pg.add_argument("--offset", type=int, default=0)
    pg.add_argument("--limit", type=int, default=10)
    pg.add_argument("--start-page", type=int, help="only search from this page")
    pg.add_argument("--end-page", type=int, help="only search up to this page")
    pg.add_argument("--context", type=int, default=140)

    pr = sub.add_parser("read", help="read exactly one page")
    pr.add_argument("symbol")
    pr.add_argument("page", type=int)
    pr.add_argument("--fy", type=int)

    args = p.parse_args()
    if args.cmd == "index" and not args.all and not args.symbols:
        sys.exit("pass --symbols or --all")

    try:
        result = _dispatch(args, args.market)
        if result is not None:
            _emit(result)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
