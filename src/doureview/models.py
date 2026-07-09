from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


# ═══════════════════════════════════════════════
# Diff 阶段
# ═══════════════════════════════════════════════

class DiffMode(Enum):
    UNSTAGED = "unstaged"
    STAGED = "staged"
    COMMITTED = "committed"


class ChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    BINARY = "binary"


@dataclass
class DiffLine:
    """diff 中的单独一行"""
    type: str               # '+', '-', ' '（上下文行）
    content: str            # 行内容（去掉前缀的原始文本）
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class Hunk:
    """一个 diff hunk，包含若干行变更"""
    header: str             # 如 "@@ -10,6 +10,8 @@"
    lines: list[DiffLine]


@dataclass
class FileChange:
    """单个文件的变更信息"""
    path: str
    old_path: str | None               # rename 场景下的旧路径
    change_type: ChangeType
    hunks: list[Hunk]
    lines_added: int
    lines_deleted: int


@dataclass
class DiffResult:
    """DiffParser 的完整输出"""
    files: list[FileChange]
    total_lines_added: int
    total_lines_deleted: int
    is_truncated: bool                 # diff 超限被截断时为 True
    mode: DiffMode
    base: str | None                   # committed 模式下的 base ref
    head: str | None                   # committed 模式下的 head ref


# ═══════════════════════════════════════════════
# Prompt 阶段
# ═══════════════════════════════════════════════

class Severity(Enum):
    STRICT = "strict"
    NORMAL = "normal"
    RELAXED = "relaxed"


@dataclass
class Prompt:
    """LLM 调用所需的完整 prompt"""
    system: str
    user: str


# ═══════════════════════════════════════════════
# Report 阶段
# ═══════════════════════════════════════════════

class IssueSeverity(str, Enum):
    """报告中问题的严重程度"""
    CRITICAL = "Critical"
    WARNING = "Warning"
    SUGGESTION = "Suggestion"


@dataclass
class ReviewIssue:
    """一条审查发现"""
    severity: IssueSeverity
    file_path: str
    line_number: int | None   # None = 全局性问题（如架构建议）
    title: str                # 简短标题，如 "nil pointer dereference"
    description: str          # 问题详细描述
    suggestion: str           # 修复建议（可含代码片段）


@dataclass
class ReviewReport:
    """审查报告的完整结构化数据"""
    timestamp: datetime
    base: str | None
    head: str | None
    files_changed: int
    lines_added: int
    lines_deleted: int
    is_truncated: bool
    issues: list[ReviewIssue] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING)

    @property
    def suggestion_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.SUGGESTION)
