"""screener.fetch._edgar_ua -- the refresh-data skill claimed a $SEC_EDGAR_CONTACT
env var was required and the script "exits with an error" without it. Neither
was true: the real (only) mechanism was a ~/.screener_edgar_email dotfile, and
a missing contact just silently degrades to a generic user-agent, no error.
This adds real env var support (checked first -- more conventional/discoverable,
e.g. for CI) while keeping the dotfile as a fallback.
"""

from screener import fetch as fetch_mod


def test_env_var_takes_priority_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_EDGAR_CONTACT", "env@example.com")
    monkeypatch.setattr(fetch_mod, "EDGAR_CONTACT_FILE", tmp_path / "unused_dotfile.txt")
    (tmp_path / "unused_dotfile.txt").write_text("dotfile@example.com")

    assert fetch_mod._edgar_ua() == "sp500-screener-bot (env@example.com)"


def test_dotfile_used_when_env_var_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    dotfile = tmp_path / "contact.txt"
    dotfile.write_text("dotfile@example.com\n")
    monkeypatch.setattr(fetch_mod, "EDGAR_CONTACT_FILE", dotfile)

    assert fetch_mod._edgar_ua() == "sp500-screener-bot (dotfile@example.com)"


def test_generic_fallback_when_neither_is_set(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
    monkeypatch.setattr(fetch_mod, "EDGAR_CONTACT_FILE", tmp_path / "does_not_exist.txt")

    assert fetch_mod._edgar_ua() == fetch_mod.YFINANCE_USER_AGENT
