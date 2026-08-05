"""Tests for fetch retries and partial batch success."""

from screener import runner


class _Limiter:
    base_interval = 0.0
    interval = 0.0

    def __init__(self):
        self.penalties = 0
        self.rewards = 0

    def acquire(self):
        pass

    def penalize(self):
        self.penalties += 1
        return 0.5

    def reward(self):
        self.rewards += 1


def test_rate_limited_fetch_retries_then_saves(monkeypatch):
    responses = iter([{"error": "HTTP 429"}, {"error": None, "value": 7}])
    limiter = _Limiter()
    monkeypatch.setattr(runner.time, "sleep", lambda delay: None)
    monkeypatch.setattr(runner.random, "uniform", lambda start, end: 0)

    report = runner.run_fetch_pipeline(
        ["AAA"], lambda symbol: next(responses), lambda symbol, raw: raw["value"],
        workers=1, limiter=limiter, max_retries=1,
    )

    assert report.saved == [7]
    assert report.failed == []
    assert limiter.penalties == limiter.rewards == 1


def test_fetch_and_save_failures_do_not_abort_other_symbols():
    def fetch(symbol):
        if symbol == "FETCH_FAIL":
            raise RuntimeError("upstream down")
        return {"error": None}

    def save(symbol, raw):
        if symbol == "SAVE_FAIL":
            raise ValueError("bad transform")
        return symbol

    report = runner.run_fetch_pipeline(
        ["OK", "FETCH_FAIL", "SAVE_FAIL"], fetch, save,
        workers=1, limiter=_Limiter(), max_retries=0,
    )

    assert report.saved == ["OK"]
    assert sorted(report.failed) == [
        ("FETCH_FAIL", "upstream down"),
        ("SAVE_FAIL", "save failed: bad transform"),
    ]


def test_failure_log_is_rewritten(tmp_path):
    path = tmp_path / "failed.txt"
    runner.write_failure_log(path, [("AAA", "timeout")])
    assert path.read_text() == "AAA: timeout\n"
    runner.write_failure_log(path, [])
    assert path.read_text() == ""

