"""Constants and paths for the stock screener."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = ROOT / "data" / "raw"

# Curated data paths
COMPANIES_DIR = DATA_DIR / "nse" / "companies"
INDICES_DIR = DATA_DIR / "nse" / "indices"
SNP_COMPANIES_DIR = DATA_DIR / "snp" / "companies"
SNP_INDICES_DIR = DATA_DIR / "snp" / "indices"

BUILD_DB_DB_PATH = ROOT / "data" / "screener.db"
MANIFEST_PATH = ROOT / "data" / "manifest.json"

# NSE
NSE500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

# S&P 500
WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
EDGAR_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
EDGAR_CACHE_DIR = RAW_DIR / "snp" / "edgar_cache"

# Email/contact for SEC EDGAR
EDGAR_CONTACT_FILE = Path.home() / ".screener_edgar_email"

# User agents
EDGAR_USER_AGENT = "sp500-screener-bot"
YFINANCE_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
SCREENER_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Screener.in URLs
SCREENER_BASE_URL = "https://www.screener.in/company"
SCREENER_PDF_URL = "https://www.screener.in"

# Defaults
MAX_WORKERS = 1  # one yfinance fetch in flight at a time (see fetch._YFINANCE_LOCK)
RATE_LIMIT_DELAY = 1.0  # min seconds between ticker fetches (global, all threads)
EDGAR_RATE_LIMIT = 0.12  # ~8 req/sec, under SEC's 10 req/s limit

# Per-ticker network timeout. yfinance has no default: without this a single
# stalled connection can hang a run for 15+ minutes.
FETCH_TIMEOUT = 30  # seconds

# Retry/backoff for throttled or transient fetch failures
FETCH_MAX_RETRIES = 3
FETCH_RETRY_BASE_DELAY = 2.0  # seconds; doubles each attempt
RATE_LIMIT_MAX_PENALTY = 8.0  # max seconds added to the interval when throttled
RATE_LIMIT_RECOVERY_STREAK = 20  # clean fetches before the penalty decays

# Failure logs (retry these with --symbols)
NSE_FAILED_TICKERS = RAW_DIR / "nse" / "failed_tickers.txt"
SNP_FAILED_TICKERS = RAW_DIR / "snp" / "failed_tickers.txt"

# Staleness thresholds
CREDIT_RATINGS_STALE_DAYS = 45
