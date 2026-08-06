"""Tests for source-native bank regulatory downloads."""

import io
import os
import zipfile

import pytest

from screener import regulatory


class _Response:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _ffiec_zip():
    columns = sorted(regulatory.FFIEC_REQUIRED_COLUMNS)
    values = {
        "RSSD9001": "1039502", "RSSD9999": "20241231", "BHCA7205": "18.5",
        "BHCAP793": "15.6", "BHCK1403": "1", "BHCK1407": "2", "BHCK2122": "100",
    }
    content = ("^".join(columns) + "\n" + "^".join(values[c] for c in columns) + "\n").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("BHCF20241231.txt", content)
    return out.getvalue()


def test_nse_filter_selects_latest_revision_and_five_years():
    rows = []
    for year in range(2019, 2026):
        rows.append({
            "qe_Date": f"31-Mar-{year}", "consolidated": "Standalone",
            "audited": "Audited", "xbrl": f"https://old/{year}.xml",
            "broadcast_Date": f"01-Apr-{year} 10:00:00", "seq_Id": str(year * 10),
        })
    rows.extend([
        {
            "qe_Date": "31-Mar-2025", "consolidated": "Standalone",
            "audited": "Audited", "xbrl": "https://revised/2025.xml",
            "revised_Date": "02-May-2025 10:00:00", "seq_Id": "20251",
        },
        {
            "qe_Date": "31-Mar-2025", "consolidated": "Consolidated",
            "audited": "Audited", "xbrl": "https://bad/consolidated.xml",
        },
        {
            "qe_Date": "31-Mar-2025", "consolidated": "Standalone",
            "audited": "Unaudited", "xbrl": "https://bad/unaudited.xml",
        },
        {
            "qe_Date": "30-Jun-2025", "consolidated": "Standalone",
            "audited": "Audited", "xbrl": "https://bad/quarter.xml",
        },
    ])

    selected = regulatory.select_nse_bank_filings({"data": {"content": rows}})

    assert selected == [
        (2025, "https://revised/2025.xml"),
        (2024, "https://old/2024.xml"),
        (2023, "https://old/2023.xml"),
        (2022, "https://old/2022.xml"),
        (2021, "https://old/2021.xml"),
    ]


def test_nse_download_reuses_older_year_and_refreshes_only_stale_latest(tmp_path):
    root = tmp_path / "bank_xbrl"
    symbol_dir = root / "HDFCBANK"
    symbol_dir.mkdir(parents=True)
    (symbol_dir / "2024.xml").write_text("<cached />")
    payload = {"data": [
        {"qe_Date": "31-Mar-2025", "consolidated": "Standalone", "audited": "Audited",
         "xbrl": "https://xbrl/2025.xml"},
        {"qe_Date": "31-Mar-2024", "consolidated": "Standalone", "audited": "Audited",
         "xbrl": "https://xbrl/2024.xml"},
    ]}
    calls = []

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if url == regulatory.NSE_FILINGS_API:
                return _Response(payload=payload)
            if url.startswith("https://xbrl/"):
                return _Response(content=b"<?xml version='1.0'?><xbrl />")
            return _Response()

    paths = regulatory.download_nse_bank_filings(
        "HDFCBANK", days_old=7, cache_root=root, session=Session(),
    )

    assert paths == [symbol_dir / "2025.xml", symbol_dir / "2024.xml"]
    assert "https://xbrl/2025.xml" in calls
    assert "https://xbrl/2024.xml" not in calls


def test_nse_invalid_xml_never_replaces_valid_cache(tmp_path):
    root = tmp_path / "bank_xbrl"
    path = root / "BANK" / "2025.xml"
    path.parent.mkdir(parents=True)
    path.write_text("<valid />")
    os.utime(path, (0, 0))
    payload = {"data": [{
        "qe_Date": "31-Mar-2025", "consolidated": "Standalone", "audited": "Audited",
        "xbrl": "https://xbrl/2025.xml",
    }]}

    class Session:
        def get(self, url, **kwargs):
            if url == regulatory.NSE_FILINGS_API:
                return _Response(payload=payload)
            if url.startswith("https://xbrl/"):
                return _Response(content=b"not xml")
            return _Response()

    with pytest.raises(ValueError, match="not XML"):
        regulatory.download_nse_bank_filings(
            "BANK", days_old=7, cache_root=root, session=Session(),
        )
    assert path.read_text() == "<valid />"


def test_ffiec_validates_headers_caches_each_year_once_and_finds_rssd(tmp_path):
    content = _ffiec_zip()
    calls = []

    class Session:
        def get(self, url, **kwargs):
            calls.append(kwargs["params"]["zipfilename"])
            return _Response(content=content)

    paths, errors = regulatory.download_ffiec_years(
        [2024, 2023], days_old=7, cache_dir=tmp_path, session=Session(),
    )
    reused, second_errors = regulatory.download_ffiec_years(
        [2024, 2023], days_old=7, cache_dir=tmp_path, session=Session(),
    )

    assert errors == second_errors == []
    assert paths == reused
    assert calls == ["BHCF20241231.ZIP", "BHCF20231231.ZIP"]
    assert regulatory.ffiec_rssd_ids(paths[2024]) == {1039502}


def test_ffiec_invalid_zip_does_not_replace_valid_cache(tmp_path):
    path = tmp_path / "BHCF20241231.zip"
    valid = _ffiec_zip()
    path.write_bytes(valid)
    os.utime(path, (0, 0))

    class Session:
        def get(self, url, **kwargs):
            return _Response(content=b"not a zip")

    paths, errors = regulatory.download_ffiec_years(
        [2024], days_old=7, cache_dir=tmp_path, session=Session(),
    )

    assert paths == {}
    assert errors[0][0] == 2024
    assert path.read_bytes() == valid


def test_parse_nse_bank_history_reads_current_context_and_profile_metrics(tmp_path):
    symbol_dir = tmp_path / "HDFCBANK"
    symbol_dir.mkdir()
    (symbol_dir / "2025.xml").write_text("""<?xml version="1.0"?>
      <xbrl xmlns:bank="https://example.test/bank">
        <bank:PercentageOfGrossNpa contextRef="OneD">0.0133</bank:PercentageOfGrossNpa>
        <bank:PercentageOfGrossNpa contextRef="FourD">0.0999</bank:PercentageOfGrossNpa>
        <bank:PercentageOfNpa contextRef="OneD">0.0041</bank:PercentageOfNpa>
        <bank:CET1Ratio contextRef="OneD">0.1955</bank:CET1Ratio>
        <bank:Advances contextRef="OneI">1000</bank:Advances>
        <bank:Deposits contextRef="OneI">800</bank:Deposits>
      </xbrl>
    """)

    history = regulatory.parse_nse_bank_history("HDFCBANK", cache_root=tmp_path)

    assert history == {2025: {
        "nonperforming_loans_ratio": 0.0133,
        "net_npa_ratio": 0.0041,
        "cet1_ratio": 0.1955,
        "loans": 1000.0,
        "deposits": 800.0,
    }}


def test_parse_ffiec_history_selects_rssd_and_normalizes_percent_units(tmp_path):
    (tmp_path / "BHCF20241231.zip").write_bytes(_ffiec_zip())

    history = regulatory.parse_ffiec_history(1039502, cache_dir=tmp_path)

    assert history == {2024: {
        "nonperforming_loans_ratio": 0.03,
        "cet1_ratio": 0.156,
        "loans": 100.0,
        "deposits": None,
    }}
    assert regulatory.parse_ffiec_history(9999999, cache_dir=tmp_path) == {}
