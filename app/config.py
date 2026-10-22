"""全局配置。路径均可用环境变量覆盖，便于本地与 Docker 运行。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, str(default))).resolve()


DATA_DIR = _path("EXPENSE_DATA", BASE_DIR / "data")
SEED_DIR = _path("EXPENSE_SEED", BASE_DIR / "seed")
UPLOAD_DIR = DATA_DIR / "uploads"
PAGE_DIR = DATA_DIR / "pages"
DB_PATH = DATA_DIR / "expense.db"

for _d in (DATA_DIR, UPLOAD_DIR, PAGE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SECRET = os.environ.get("EXPENSE_SECRET", "expense-agent-demo-secret")
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "7"))
SEED_ON_START = os.environ.get("SEED_ON_START", "1") == "1"
OCR_DPI = int(os.environ.get("OCR_DPI", "170"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "30"))

# 可选：OpenAI 兼容接口。未配置时使用规则 + 模板化解释。
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_ENABLED = bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))

CN_TZ = timezone(timedelta(hours=8))

COMPANY = "厦门商集网络科技有限责任公司"
COMPANY_TAX_ID = "91350200MA2YQ8XK3B"

ROLE_LABELS = {"admin": "平台管理员", "approver": "财务审批人", "employee": "报销申请人"}
ROLE_DESC = {
    "admin": "配置岗位、政策与权限，查看全部数据",
    "approver": "处理待审批任务，可修改或驳回 AI 结论",
    "employee": "提交报销单，查看本人任务进度",
}

# 城市住宿限额（元/晚）
CITY_HOTEL_LIMIT = {
    "北京": 700, "上海": 700, "深圳": 650, "广州": 600, "杭州": 550,
    "南京": 500, "成都": 480, "武汉": 450, "厦门": 450, "其他": 380,
}
MEAL_LIMIT_PER_DAY = 100
LOCAL_TRANSPORT_LIMIT_PER_DAY = 80
ENTERTAIN_THRESHOLD = 2000

# AI 自动通过阈值
AUTO_PASS_CONFIDENCE = 0.90
REVIEW_CONFIDENCE = 0.65
