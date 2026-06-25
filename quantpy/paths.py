"""Project-wide paths (repository root = parent of quantpy package)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
REPORT_DIR = OUTPUT_DIR / "daily_reports"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
CACHE_DIR = PROJECT_ROOT / "cache"
SIM_REVIEW_DIR = OUTPUT_DIR / "sim_reviews"
MIDTERM_OUTPUT_DIR = OUTPUT_DIR / "midterm"
REAL_REVIEW_DIR = OUTPUT_DIR / "real_review"
AI_LEARNING_DIR = OUTPUT_DIR / "ai_learning"

PORTFOLIO_CONFIG_FILE = DATA_DIR / "portfolio_config.json"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
TRADES_FILE = DATA_DIR / "trades.json"
SIM_STATE_FILE = DATA_DIR / "sim_state.json"
