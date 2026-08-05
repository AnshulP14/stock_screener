"""screener.store — atomic per-company JSON file I/O."""

from pathlib import Path

from screener.index import delete_company, iter_companies, list_symbols, load_company, save_company


def test_save_and_load(tmp_path: Path):
    save_company(tmp_path, "AAPL", {"symbol": "AAPL", "sector": "Technology"})
    assert load_company(tmp_path, "AAPL") == {"symbol": "AAPL", "sector": "Technology"}


def test_delete(tmp_path: Path):
    save_company(tmp_path, "AAPL", {"symbol": "AAPL"})
    delete_company(tmp_path, "AAPL")
    assert list_symbols(tmp_path) == []


def test_delete_missing_noop(tmp_path: Path):
    delete_company(tmp_path, "NOEXIST")  # should not raise


def test_symbols_sorted(tmp_path: Path):
    save_company(tmp_path, "ZOO", {})
    save_company(tmp_path, "AAPL", {})
    save_company(tmp_path, "MSFT", {})
    assert list_symbols(tmp_path) == ["AAPL", "MSFT", "ZOO"]


def test_iter_all(tmp_path: Path):
    save_company(tmp_path, "AAPL", {"symbol": "AAPL"})
    save_company(tmp_path, "MSFT", {"symbol": "MSFT"})
    items = list(iter_companies(tmp_path))
    assert len(items) == 2
    paths, data = zip(*items)
    assert [p.stem for p in paths] == ["AAPL", "MSFT"]
    assert [d["symbol"] for d in data] == ["AAPL", "MSFT"]


def test_iter_all_skips_bad_json(tmp_path: Path):
    (tmp_path / "BROKEN.json").write_text("{not valid")
    assert list(iter_companies(tmp_path)) == []


def test_atomic_write_no_tmp_left(tmp_path: Path):
    save_company(tmp_path, "AAPL", {"symbol": "AAPL"})
    assert list(tmp_path.glob("*.tmp")) == []
