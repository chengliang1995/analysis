"""Project-wide paths (repository root = parent of quantpy package)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
REPORT_DIR = OUTPUT_DIR / "daily_reports"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
CACHE_DIR = PROJECT_ROOT / "cache"
SIM_REVIEW_DIR = OUTPUT_DIR / "sim_reviews"
MIDTERM_OUTPUT_DIR = OUTPUT_DIR / "midterm"
REAL_REVIEW_DIR = OUTPUT_DIR / "real_review"
AI_LEARNING_DIR = OUTPUT_DIR / "ai_learning"
LOG_DIR = PROJECT_ROOT / "logs"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# 按日落盘的日志/报告保留天数（含今天共保留 N 个自然日）
RETENTION_DAYS = 3

PORTFOLIO_CONFIG_FILE = DATA_DIR / "portfolio_config.json"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
TRADES_FILE = DATA_DIR / "trades.json"
SIM_STATE_FILE = DATA_DIR / "sim_state.json"
MIDTERM_TRACKER_FILE = DATA_DIR / "midterm_pick_tracker.json"
TRIPLE_VOLUME_WATCHLIST_FILE = DATA_DIR / "triple_volume_watchlist.json"
SCHEDULER_STATUS_FILE = DATA_DIR / "scheduler_status.json"
SCHEDULER_PHASES_FILE = SCRIPTS_DIR / "phases.json"

# 落盘契约（选股产物）
# - 超短 → output/ultra_short_YYYYMMDD.csv
# - 中线 → output/midterm/midterm_*.json
# - 三倍量 → output/midterm/triple_volume_*.json
# - 观察池 → data/triple_volume_watchlist.json
# - 中线跟进 → data/midterm_pick_tracker.json
# - 定时状态 → data/scheduler_status.json
