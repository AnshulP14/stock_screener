"""markets/nse.py's dry-run path referenced RATE_LIMIT_DELAY without importing
it, so `--market nse --dry-run` raised NameError on every invocation.
Regression test for that fix — must not touch the network or data/."""

from screener.markets import nse


def test_dry_run_does_not_crash():
    result = nse.run(mode="full", symbols=["RELIANCE", "TCS"], dry_run=True, workers=3)
    assert result == {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": result["elapsed"]}
