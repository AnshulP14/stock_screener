"""_rebuild_db was 95 lines with zero callers — the only code that dropped
stale tables. Phase 0 deleted it and moved that behavior into
drop_market_tables, called from screener.db.rebuild whenever a market's
curated JSON no longer exists on disk."""

import duckdb

from screener import db as db_mod


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
