#!/usr/bin/env python3
"""
Convert htm annual reports → DocLang XML (.dclng.xml) for AI agent consumption.

Uses Docling's SimplePipeline (declarative HTML backend — zero OCR, zero ML models)
to produce DocLang, one of docling's structured export formats.
Parallelized via ProcessPoolExecutor — each worker process builds ONE
DocumentConverter (pool initializer) and reuses it across every file the pool
hands it, so per-file overhead stays at just the conversion itself. Files that
already have a .dclng.xml are skipped. Progress (rate + ETA) is logged after
every file, so a backgrounded run's log can be tailed/grepped at any point.

Output: data/raw/snp/annual_reports/{SYMBOL}/{SYMBOL}_10K_{YEAR}.dclng.xml
(placed alongside the source .htm file in each symbol directory)

Usage:
    python scripts/convert_htm_to_dclng.py                # All htm files (SNP)
    python scripts/convert_htm_to_dclng.py --symbol AAPL   # Only AAPL
    python scripts/convert_htm_to_dclng.py --dry-run       # Preview only
    python scripts/convert_htm_to_dclng.py --workers 32    # More/less workers
"""

import signal
import sys
import time
from pathlib import Path

import argparse
import logging
import multiprocessing as mp

from concurrent.futures import ProcessPoolExecutor, as_completed

from docling.document_converter import DocumentConverter
from screener.config import RAW_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_converter: DocumentConverter | None = None


def _init_worker() -> None:
    """ProcessPoolExecutor initializer — build one converter per worker process, reused for every file it handles."""
    global _converter
    _converter = DocumentConverter()


def find_htm_files(symbol_filter: str | None = None) -> list[str]:
    """Find all .htm files under data/raw/snp/annual_reports/. Returns list of str paths."""
    base = RAW_DIR / "snp" / "annual_reports"
    files = sorted(base.rglob("*.htm"))
    if symbol_filter:
        files = [f for f in files if f.parts[-2] == symbol_filter.upper()]
    return [str(f) for f in files]


def _out_path(htm_path: str) -> Path:
    return Path(htm_path).with_suffix(".dclng.xml")


def _convert_one(htm_path: str) -> tuple[str, str | None]:
    """Convert a single .htm file to .dclng.xml using this worker's converter. Returns (path, error_or_none)."""
    try:
        result = _converter.convert(htm_path)
        xml_content = result.document.export_to_doclang()
        _out_path(htm_path).write_text(xml_content, encoding="utf-8")
        return htm_path, None
    except Exception as e:
        return htm_path, str(e)


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


_executor_ref = None  # shared with signal handler


def _shutdown_handler(signum, frame):
    """SIGINT/SIGTERM handler — shut down executor so workers don't become orphans."""
    logger.info(f"Received signal {signum}, shutting down workers...")
    if _executor_ref is not None:
        _executor_ref.shutdown(wait=False, cancel_futures=True)
    sys.exit(128 + signum)


def main():
    global _executor_ref
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    parser = argparse.ArgumentParser(description="Convert htm annual reports → DocLang XML")
    parser.add_argument("--symbol", type=str, help="Only convert this symbol's reports")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--workers", type=int, default=mp.cpu_count(),
                        help=f"Number of parallel workers (default: {mp.cpu_count()})")
    args = parser.parse_args()

    files = find_htm_files(args.symbol)
    if not files:
        print("No .htm files found.")
        return

    to_convert = [f for f in files if not _out_path(f).exists()]
    skipped = len(files) - len(to_convert)

    print(f"Found {len(files)} .htm file(s), {skipped} already converted (skipped), "
          f"{len(to_convert)} to convert, using {args.workers} workers")

    if args.dry_run:
        for f in to_convert:
            print(f"  {Path(f).name} → {_out_path(f).name}")
        return

    if not to_convert:
        print("Nothing to do.")
        return

    total = len(to_convert)
    start = time.time()
    success = 0
    failed = 0
    errors: list[str] = []

    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as executor:
        _executor_ref = executor
        futures = {executor.submit(_convert_one, f): f for f in to_convert}
        for i, future in enumerate(as_completed(futures), start=1):
            path, err = future.result()
            if err is None:
                success += 1
            else:
                failed += 1
                errors.append(f"{Path(path).name}: {err}")

            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else float("inf")
            logger.info(
                f"[{i}/{total}] {Path(path).name} "
                f"({'ok' if err is None else 'FAILED'}) — "
                f"{rate:.2f} files/s, elapsed {_format_duration(elapsed)}, "
                f"ETA {_format_duration(eta) if eta != float('inf') else '?'}"
            )

    elapsed = time.time() - start
    print(f"\nDone in {_format_duration(elapsed)} ({total} files, {args.workers} workers)")
    print(f"  Success: {success}, Failed: {failed}, Skipped: {skipped}")
    if errors:
        for e in errors[:10]:
            print(f"  ERROR: {e}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more errors")
    print(f"  Rate: {total/elapsed:.2f} files/s" if elapsed > 0 else "")


if __name__ == "__main__":
    main()
