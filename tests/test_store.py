"""screener.store — CompanyStore atomic file I/O."""

import json
from pathlib import Path

from screener.store import CompanyStore


def test_save_and_load(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("AAPL", {"symbol": "AAPL", "sector": "Technology"})
    assert store.load("AAPL") == {"symbol": "AAPL", "sector": "Technology"}


def test_delete(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("AAPL", {"symbol": "AAPL"})
    store.delete("AAPL")
    assert store.symbols() == []


def test_delete_missing_noop(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.delete("NOEXIST")  # should not raise


def test_symbols_sorted(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("ZOO", {})
    store.save("AAPL", {})
    store.save("MSFT", {})
    assert store.symbols() == ["AAPL", "MSFT", "ZOO"]


def test_iter_all(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("AAPL", {"symbol": "AAPL"})
    store.save("MSFT", {"symbol": "MSFT"})
    items = list(store.iter_all())
    assert len(items) == 2
    paths, data = zip(*items)
    assert [p.stem for p in paths] == ["AAPL", "MSFT"]
    assert [d["symbol"] for d in data] == ["AAPL", "MSFT"]


def test_iter_all_skips_bad_json(tmp_path: Path):
    (tmp_path / "BROKEN.json").write_text("{not valid")
    store = CompanyStore(tmp_path)
    assert list(store.iter_all()) == []


def test_update(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("AAPL", {"symbol": "AAPL", "sector": "Tech"})
    store.update("AAPL", lambda d: {**d, "sector": "Technology"})
    assert store.load("AAPL")["sector"] == "Technology"


def test_atomic_write_no_tmp_left(tmp_path: Path):
    store = CompanyStore(tmp_path)
    store.save("AAPL", {"symbol": "AAPL"})
    assert list(tmp_path.glob("*.tmp")) == []
