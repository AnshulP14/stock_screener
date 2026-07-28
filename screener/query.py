"""DuckDB SQL query utility against data/screener.db — the logic behind scripts/query.py."""

import sys

import duckdb

from .config import BUILD_DB_DB_PATH


def query(sql: str, csv: bool = False, market: str | None = None) -> None:
    """Execute SQL against the screener database and print results."""
    conn = duckdb.connect(str(BUILD_DB_DB_PATH), read_only=True)

    # Add prefix for unqualified tables
    if market:
        sql = sql.replace("FROM nse", f"FROM {market}")
        sql = sql.replace("FROM snp", f"FROM {market}")

    try:
        result = conn.execute(sql).fetchdf()
        if csv:
            result.to_csv(sys.stdout, index=False)
        else:
            print(result.to_string(index=False))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
