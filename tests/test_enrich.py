"""Tests for Screener.in enrichment parsing."""

from bs4 import BeautifulSoup

from screener.enrich import parse_credit_ratings, parse_shareholding


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_shareholding_trends_follow_oldest_to_newest_quarters():
    soup = _soup("""
        <section id="shareholding"><table>
          <tr><th></th><th>Jun 2025</th><th>Sep 2025</th><th>Dec 2025</th>
              <th>Mar 2026</th><th>Jun 2026</th></tr>
          <tr><td>Promoters</td><td>48%</td><td>49%</td><td>50%</td><td>51%</td><td>52%</td></tr>
          <tr><td>FIIs</td><td>22.6%</td><td>21.9%</td><td>21.1%</td><td>20.2%</td><td>19.0%</td></tr>
          <tr><td>DIIs</td><td>16%</td><td>17%</td><td>18%</td><td>19%</td><td>20%</td></tr>
          <tr><td>Public</td><td>13.4%</td><td>12.1%</td><td>10.9%</td><td>9.8%</td><td>9%</td></tr>
        </table></section>
    """)

    result = parse_shareholding(soup)

    assert result["quarters"][-1] == "Jun 2026"
    assert result["trends"] == {
        "promoter": "increasing",
        "fii": "decreasing",
        "dii": "increasing",
    }


def test_shareholding_missing_table_returns_none():
    assert parse_shareholding(_soup("<html></html>")) is None


def test_credit_ratings_parses_provider_and_crisil_action():
    result = parse_credit_ratings(_soup("""
        <div class="company-credit-ratings">
          <a href="https://crisil.example/Acme_July_1_2026_RR_123.html">
            <div class="smaller">1 Jul 2026 from CRISIL</div>
          </a>
          <a href="https://www.icra.in/Rationale/ShowRationaleReport/?Id=1">
            <div class="smaller">30 Jun 2026 from ICRA</div>
          </a>
        </div>
    """))

    assert result["has_ratings"] is True
    assert result["latest_action"] == "Reaffirmed"
    assert result["agencies"] == ["CRISIL", "ICRA"]

