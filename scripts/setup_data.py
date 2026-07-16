#!/usr/bin/env python3
"""Download and install the screener data set (data/companies + data/indices).

The data is not in git — it lives in a zip on Google Drive. Run this once after cloning:

    python scripts/setup_data.py

URL resolution order: --url > $STOCK_SCREENING_DATA_URL > DATA_URL below.
"""

import argparse
import io
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

import requests

# Paste the Google Drive share link here (or set $STOCK_SCREENING_DATA_URL).
DATA_URL = ""

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DIRS = ("data/companies", "data/indices")
# Importing stock_screening reads this file at module level, so it must exist.
SENTINEL = "data/indices/by_industry.json"


def to_direct_url(url: str) -> str:
    """Turn a Google Drive share link into a direct-download URL. Other URLs pass through."""
    match = re.search(r"/file/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    if "drive.google.com" in url and match:
        return f"https://drive.usercontent.google.com/download?id={match.group(1)}&export=download"
    return url


def download(url: str) -> bytes:
    session = requests.Session()
    response = session.get(to_direct_url(url), stream=True, timeout=120)
    response.raise_for_status()

    # Drive interrupts large downloads with an HTML "can't scan for viruses" page.
    if "text/html" in response.headers.get("Content-Type", ""):
        body = response.text
        token = re.search(r'name="confirm" value="([^"]+)"', body)
        file_id = re.search(r'name="id" value="([^"]+)"', body)
        if not (token and file_id):
            raise RuntimeError(
                "Drive returned an HTML page instead of the zip. Is the link shared as "
                "'Anyone with the link'?"
            )
        response = session.get(
            "https://drive.usercontent.google.com/download",
            params={"id": file_id.group(1), "export": "download", "confirm": token.group(1)},
            stream=True,
            timeout=120,
        )
        response.raise_for_status()

    total = int(response.headers.get("Content-Length") or 0)
    chunks, seen = [], 0
    for chunk in response.iter_content(1 << 16):
        chunks.append(chunk)
        seen += len(chunk)
        if total:
            print(f"\r  {seen / 1e6:.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print(f"\r  downloaded {seen / 1e6:.1f} MB" + " " * 20)
    return b"".join(chunks)


def extract(payload: bytes, force: bool) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise RuntimeError("Downloaded file is not a zip — check the URL is a direct file link.")

    names = archive.namelist()
    if not any(n.startswith("data/") for n in names):
        raise RuntimeError(f"Zip does not contain a data/ directory (top level: {names[:3]})")

    for name in names:
        # Refuse absolute paths and ../ escapes.
        target = (PROJECT_ROOT / name).resolve()
        if not str(target).startswith(str(PROJECT_ROOT)):
            raise RuntimeError(f"Refusing to extract outside the project: {name}")

    for rel in REQUIRED_DIRS:
        existing = PROJECT_ROOT / rel
        if existing.exists() and force:
            shutil.rmtree(existing)

    archive.extractall(PROJECT_ROOT)
    print(f"  extracted {sum(1 for n in names if n.endswith('.json'))} JSON files")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Zip URL (Drive share link or direct)")
    parser.add_argument("--force", action="store_true", help="Replace existing data dirs")
    args = parser.parse_args()

    url = args.url or os.environ.get("STOCK_SCREENING_DATA_URL") or DATA_URL
    if not url:
        print(
            "No data URL configured.\n"
            "  Pass --url <link>, set $STOCK_SCREENING_DATA_URL, or fill in DATA_URL in this file.",
            file=sys.stderr,
        )
        return 2

    if (PROJECT_ROOT / SENTINEL).exists() and not args.force:
        print(f"Data already present ({SENTINEL}). Use --force to re-download.")
        return 0

    print(f"Fetching data from {url}")
    try:
        extract(download(url), args.force)
    except (requests.RequestException, RuntimeError) as e:
        print(f"\nFailed: {e}", file=sys.stderr)
        return 1

    missing = [d for d in REQUIRED_DIRS if not (PROJECT_ROOT / d).is_dir()]
    if missing or not (PROJECT_ROOT / SENTINEL).exists():
        print(f"\nFailed: expected {missing or SENTINEL} after extraction.", file=sys.stderr)
        return 1

    for rel in REQUIRED_DIRS:
        print(f"  {rel}: {len(list((PROJECT_ROOT / rel).glob('*.json')))} files")
    print("Data ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
