import os
from pathlib import Path
from enum import Enum
from dotenv import load_dotenv

from .models import Severity
from .errors import LLMAuthError

load_dotenv()


class Provider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Config:
    """全局配置，从环境变量读取，提供默认值"""

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
        if cls.PROVIDER == Provider.ANTHROPIC and not cls.ANTHROPIC_API_KEY:
            raise LLMAuthError(
                "未配置 ANTHROPIC_API_KEY。请在 .env 文件或环境变量中设置。"
            )
        if cls.PROVIDER == Provider.OPENAI and not cls.OPENAI_API_KEY:
            raise LLMAuthError(
                "未配置 OPENAI_API_KEY。请在 .env 文件或环境变量中设置。"
            )
