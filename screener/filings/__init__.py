"""Filing navigation: NSE PDF annual reports + S&P 10-K HTML filings.

Exports the same public API as before:
  - pdf_filings / PDF_BACKEND for NSE
  - html_filings / HTML_BACKEND for S&P
"""

from . import pdf_filings, html_filings
from .backend import FilingBackend, norm_heading, ALPHA, MAX_TITLE, MIN_ALPHA
