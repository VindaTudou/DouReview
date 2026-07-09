import os
from pathlib import Path
from enum import Enum
from dotenv import load_dotenv

from .models import Severity
from .errors import LLMAuthError

# 配置只从 DouReview 包目录的 .env 加载
_package_dir = Path(__file__).parent
_package_env = _package_dir / ".env"
_example_env = _package_dir / ".env.example"

if _package_env.exists():
    load_dotenv(dotenv_path=_package_env, override=True)
# 注意：.env 不会被包含在 pipx 安装包中（避免泄露 API Key）。
# 用户安装后需复制 .env.example 为 .env 并填入自己的配置。


class Provider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Config:
    """全局配置，从包目录 .env 读取，提供默认值"""

    PROVIDER: Provider = Provider(os.getenv("DOUREVIEW_PROVIDER", "anthropic"))
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    MODEL: str = os.getenv("DOUREVIEW_MODEL", "claude-sonnet-4-5-20250914")
    SEVERITY: Severity = Severity(os.getenv("DOUREVIEW_SEVERITY", "normal"))
    MAX_DIFF_LINES: int = int(os.getenv("DOUREVIEW_MAX_DIFF_LINES", "1000"))
    OUTPUT_DIR: Path = Path(os.getenv("DOUREVIEW_OUTPUT_DIR", "doureview"))
    QUIET: bool = os.getenv("DOUREVIEW_QUIET", "").lower() in ("true", "1")

    @classmethod
    def validate(cls) -> None:
        """启动时校验必要配置"""
        _pkg = Path(__file__).parent
        if cls.PROVIDER == Provider.ANTHROPIC:
            if not cls.ANTHROPIC_API_KEY:
                raise LLMAuthError(
                    "未配置 ANTHROPIC_API_KEY。\n"
                    f"1. 复制 {_pkg / '.env.example'} 为 {_pkg / '.env'}\n"
                    f"2. 在 {_pkg / '.env'} 中写入 ANTHROPIC_API_KEY=sk-ant-xxx"
                )
        if cls.PROVIDER == Provider.OPENAI:
            if not cls.OPENAI_API_KEY:
                raise LLMAuthError(
                    "未配置 OPENAI_API_KEY。\n"
                    f"1. 复制 {_pkg / '.env.example'} 为 {_pkg / '.env'}\n"
                    f"2. 在 {_pkg / '.env'} 中写入 OPENAI_API_KEY=sk-xxx"
                )