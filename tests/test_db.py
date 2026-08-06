"""Tests for DuckDB rebuild behavior."""

import json

import duckdb

from screener import db as db_mod
from screener.summary import COLUMN_DESCRIPTIONS, METRIC_COLUMNS, PERCENTILE_COLUMNS, TEXT_COLUMNS


def test_drop_market_tables_removes_existing_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "BUILD_DB_DB_PATH", db_path)

    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE snp AS SELECT 1 AS x")
    con.execute("CREATE TABLE snp_companies AS SELECT 1 AS x")
    con.execute("CREATE TABLE snp_industry_stats AS SELECT 1 AS x")
    con.close()

    db_mod.drop_market_tables("snp")

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert tables == set()


def test_drop_market_tables_is_idempotent_when_absent(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "BUILD_DB_DB_PATH", db_path)

    db_mod.drop_market_tables("snp")  # must not raise


def test_query_describe_adds_flat_column_descriptions(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "BUILD_DB_DB_PATH", db_path)
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE nse AS SELECT 1.0 AS trailing_pe")
    con.close()

    db_mod.query("DESCRIBE nse")

    output = capsys.readouterr().out
    assert "description" in output
    assert "Positive current price divided by trailing earnings per share." in output


def test_every_phase_3c_flat_column_has_a_description():
    columns = set(TEXT_COLUMNS) | set(METRIC_COLUMNS) | set(PERCENTILE_COLUMNS.values())
    columns |= {"fundamentals_fy", "industry_peer_count"}

    assert columns <= COLUMN_DESCRIPTIONS.keys()


def test_rebuild_market_db_creates_the_complete_query_surface(tmp_path, monkeypatch):
    db_path = tmp_path / "screener.db"
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    companies_dir.mkdir()
    indices_dir.mkdir()
    (companies_dir / "AAPL.json").write_text(json.dumps({
        "symbol": "AAPL",
        "currency": "USD",
        "current_snapshot": {"size": {"market_cap": 3e12}},
    }))
    (indices_dir / "screening_summary.json").write_text(json.dumps({
        "companies": [{"symbol": "AAPL", "currency": "USD", "market_cap": 3e12}],
    }))
    (indices_dir / "industry_stats.json").write_text(json.dumps({
        "Software": {"company_count": 1, "metrics": {}},
    }))
    manifest_updates = []
    monkeypatch.setattr(db_mod, "BUILD_DB_DB_PATH", db_path)
    monkeypatch.setattr(
        db_mod, "update_manifest",
        lambda market, entry, **kwargs: manifest_updates.append((market, entry, kwargs)),
    )

    result = db_mod.rebuild_market_db(
        market="snp", companies_dir=companies_dir, indices_dir=indices_dir,
    )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        counts = {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("snp", "snp_companies", "snp_industry_stats")
        }
    finally:
        con.close()
    assert counts == {"snp": 1, "snp_companies": 1, "snp_industry_stats": 1}
    assert result["tables"] == counts
    assert manifest_updates[0][0] == "snp"
    assert manifest_updates[0][2] == {"touch_generated_at": False}
