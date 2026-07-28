"""screener/ is a real installable package now — no sys.path hacks needed to
import it, and every wrapper script's target module must resolve cleanly."""


def test_screener_package_imports():
    import screener  # noqa: F401


def test_market_pipelines_import():
    from screener.markets import nse, snp  # noqa: F401


def test_cli_entry_points_import():
    from screener.cli import main  # noqa: F401
    from screener.db import rebuild  # noqa: F401
    from screener.query import query  # noqa: F401
