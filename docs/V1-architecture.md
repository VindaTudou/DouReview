# DouReview — 代码架构与接口设计

V1 采用**纯 SDK + 可替换接口**策略。每个模块是一个类，只通过明确的接口与外界交互。V2 切 LangChain 时只需替换实现，接口不变。

---

## 1. 目录结构

```
DouReview/
├── src/
│   └── doureview/
│       ├── __init__.py
│       ├── cli.py              # CLI 入口 (typer)
│       ├── config.py           # 配置管理（环境变量 + .env）
│       ├── models.py           # 共享数据模型（Pydantic/dataclass）
│       ├── errors.py           # 自定义异常
│       ├── diff_parser.py      # 模块1: DiffParser
│       ├── prompt_builder.py   # 模块2: PromptBuilder
│       ├── llm_client.py       # 模块3: LLMClient
│       ├── report_generator.py # 模块4: ReportGenerator
│       └── pipeline.py         # 编排层 —— 把四个模块串起来
├── tests/
│   ├── conftest.py
│   ├── test_diff_parser.py
│   ├── test_prompt_builder.py
│   ├── test_llm_client.py
│   ├── test_report_generator.py
│   └── test_pipeline.py
├── docs/
│   ├── design.md               # 产品级设计文档
│   └── architecture.md         # 本文档 —— 代码级架构
├── pyproject.toml
├── .env.example
└── README.md
```

**原则**：
- `pipeline.py` 是唯一的编排层，`cli.py` 只做参数解析然后调 pipeline。
- 模块之间不互相 import，只通过 `models.py` 中的数据结构通信。
- 每个模块的构造函数接收依赖，不做隐式的全局状态。

---

## 2. 依赖关系图

```
cli.py
  │
  ▼
pipeline.py ──────────────────────────────────────┐
  │                                                │
  ├──▶ DiffParser       ──▶ DiffResult             │
  │                                    │            │
  ├──▶ PromptBuilder    ◀─────────────┘            │
  │         │                                       │
  │         ▼  Prompt(system, user)                 │
  ├──▶ LLMClient        ──▶ str (raw response)     │
  │                                    │            │
  ├──▶ ReportGenerator  ◀─────────────┤            │
  │         │                          │            │
  │         ▼  Path (report file)      │            │
  └────────────────────────────────────┘            │
                                                    │
  数据模型层:  models.py  ◀─────────────────────────┘
  异常层:      errors.py
  配置层:      config.py
```

关键约束：数据流是单向的。每个箭头都是纯数据，不传对象引用。

---

## 3. 数据模型 (`models.py`)

所有模块之间的通信语言。用 dataclass 而非 Pydantic BaseModel —— V1 不需要序列化/验证的额外开销。

```python
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


# ── Diff 阶段 ──────────────────────────────────

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
    type: str             # '+', '-', ' '（上下文行）
    content: str          # 行内容（去掉前缀的原始文本）
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class Hunk:
    """一个 diff hunk，包含若干行变更"""
    header: str           # 如 "@@ -10,6 +10,8 @@"
    lines: list[DiffLine]


@dataclass
class FileChange:
    """单个文件的变更信息"""
    path: str
    old_path: str | None            # rename 场景下的旧路径
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
    is_truncated: bool              # diff 超限被截断时为 True
    mode: DiffMode
    base: str | None                # commit range 模式下的 base ref
    head: str | None                # commit range 模式下的 head ref

# 设计决策：DiffResult 目前不包含 commits 列表。
# V2 做 PR 集成时可加一个独立字段 commits: list[CommitChange]。
# 它来自 git log（独立数据源），与 files（来自 git diff base head）并行，
# 互不干扰，不需要中间层抽象。


# ── Prompt 阶段 ────────────────────────────────

class Severity(Enum):
    STRICT = "strict"
    NORMAL = "normal"
    RELAXED = "relaxed"


@dataclass
class Prompt:
    """LLM 调用所需的完整 prompt"""
    system: str
    user: str


# ── Report 阶段 ────────────────────────────────

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
    line_number: int | None         # None = 全局性问题（如架构建议）
    title: str                      # 简短标题，如 "nil pointer dereference"
    description: str                # 问题详细描述
    suggestion: str                 # 修复建议（可含代码片段）


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
```

---

## 4. 自定义异常 (`errors.py`)

每种错误场景一个异常类型，让调用方可以精确捕获和处理。

```python
class DouReviewError(Exception):
    """所有 DouReview 异常的基类"""
    pass


class NotAGitRepoError(DouReviewError):
    """当前目录不是 git 仓库"""
    pass


class EmptyDiffError(DouReviewError):
    """diff 为空，没有变更内容"""
    pass


class DiffTooLargeError(DouReviewError):
    """diff 超过行数上限，已被截断（非致命，仅告警）"""
    pass


class LLMError(DouReviewError):
    """LLM 调用失败（超时、网络等）"""
    pass


class LLMAuthError(LLMError):
    """API Key 无效或未配置"""
    pass


class LLMRateLimitError(LLMError):
    """API 速率限制"""
    pass


class ReportParseError(DouReviewError):
    """LLM 响应无法解析为结构化报告"""
    pass
```

---

## 5. 模块接口设计

### 5.1 DiffParser (`diff_parser.py`)

**职责**：执行 `git diff` 并解析为结构化的 `DiffResult`。

```python
class DiffParser:
    """
    解析 git diff 输出。

    用法:
        parser = DiffParser(repo_path=Path.cwd(), max_lines=1000)
        result = parser.parse(DiffMode.STAGED)
        # 或
        result = parser.parse(DiffMode.COMMIT_RANGE, base="main", head="HEAD")
    """

    def __init__(self, repo_path: Path = Path.cwd(), max_lines: int = 1000) -> None:
        """
        Args:
            repo_path: git 仓库路径，默认当前目录
            max_lines: diff 行数上限，超过则截断并标记 is_truncated=True
        """
        ...

    def parse(
        self,
        mode: DiffMode,
        base: str | None = None,
        head: str | None = None,
    ) -> DiffResult:
        """
        执行 git diff 并返回结构化结果。

        Args:
            mode: unstaged / staged / commit_range
            base: commit_range 模式的起点（如 "main"）
            head: commit_range 模式的终点（如 "HEAD"）

        Returns:
            DiffResult

        Raises:
            NotAGitRepoError: 非 git 仓库
            EmptyDiffError: 无变更
        """
        ...

    # ── 内部方法（私有，但文档中列出以便理解实现） ──

    def _build_command(self, mode: DiffMode, base: str | None, head: str | None) -> list[str]:
        """根据模式拼接 git diff 参数"""
        ...

    def _run_git(self, cmd: list[str]) -> str:
        """执行 git 命令，返回 stdout"""
        ...

    def _parse_output(self, raw: str) -> DiffResult:
        """解析 unified diff 格式的原始输出"""
        ...
```

**设计要点**：
- `max_lines` 是硬上限，只统计 `+` 和 `-` 行
- 二进制文件在 `git diff --numstat` 显示为 `-`，通过 `ChangeType.BINARY` 标记，不出现在 hunks 中
- `DiffResult.mode` 回传原始的解析模式，让后续模块知道上下文

---

### 5.2 PromptBuilder (`prompt_builder.py`)

**职责**：将 `DiffResult` 转换为 LLM 可消费的 `Prompt`。

```python
class PromptBuilder:
    """
    构建 LLM prompt。

    用法:
        builder = PromptBuilder(severity=Severity.NORMAL)
        prompt = builder.build(diff_result)
    """

    def __init__(self, severity: Severity = Severity.NORMAL) -> None:
        """
        Args:
            severity: 审查严格程度，影响 prompt 中的审查标准措辞
        """
        ...

    def build(self, diff: DiffResult) -> Prompt:
        """
        将 diff 数据注入 prompt 模板。
        
        Args:
            diff: DiffParser 的输出

        Returns:
            Prompt(system=..., user=...)
        """
        ...

    # ── 内部方法 ──

    @staticmethod
    def _system_prompt(severity: Severity) -> str:
        """构建 system prompt —— 定义角色和审查标准"""
        ...

    @staticmethod
    def _user_prompt(diff: DiffResult) -> str:
        """
        构建 user prompt —— 包含 diff 内容和输出格式要求。

        输出格式约束通过自然语言描述，要求 LLM 按以下结构输出:
        ```
        ## Critical
        ### `file:line` — title
        **问题**: ...
        **建议**: ...
        ```
        """
        ...

    @staticmethod
    def _format_diff(diff: DiffResult) -> str:
        """将 DiffResult 格式化为 LLM prompt 中的文本块"""
        ...
```

**设计要点**：
- System prompt 和 user prompt 分开构造，便于未来单独调整
- `_format_diff` 将结构化的 `DiffResult` 回退为类似 unified diff 的文本格式（因为这恰好是 LLM 最擅长阅读的格式）
- 若 `diff.is_truncated` 为 True，在 prompt 中插入截断告警
- `_user_prompt` 中的输出格式约束是关键 —— 它决定了 ReportGenerator 能否正确解析。必须写清楚输出结构、禁止无关废话、禁止 review 改动之外的内容

---

### 5.3 LLMClient (`llm_client.py`)

**职责**：封装 LLM API 调用，处理重试和错误。

```python
class LLMClient:
    """
    LLM 调用客户端。支持 Anthropic 和 OpenAI 两种提供商。

    用法:
        # Anthropic
        client = LLMClient(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-5-20250914")
        # OpenAI
        client = LLMClient(provider="openai", api_key="sk-...", model="gpt-4o")
        # 或 OpenAI 兼容接口（如 Ollama、DeepSeek 等）
        client = LLMClient(provider="openai", api_key="ollama", base_url="http://localhost:11434/v1", model="llama3")

        response = client.invoke(prompt)
        for chunk in client.stream(prompt):
            print(chunk, end="", flush=True)
    """

    def __init__(
        self,
        provider: str = "anthropic",
        api_key: str | None = None,
        model: str = "claude-sonnet-4-5-20250914",
        base_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        """
        Args:
            provider: "anthropic" | "openai"
            api_key: API Key。None 时从环境变量读取
            model: 模型 ID
            base_url: 仅 OpenAI 模式，自定义 API 地址（兼容接口用）
            max_retries: 最大重试次数
            timeout: 单次请求超时秒数
        """
        ...

    def invoke(self, prompt: Prompt) -> str:
        """
        调用 LLM，返回完整响应文本。内部根据 provider 路由到对应 SDK。
        """
        ...

    def stream(self, prompt: Prompt) -> Iterator[str]:
        """
        流式调用 LLM，逐个输出文本块。
        """
        ...

    # ── 内部方法 ──

    def _create_anthropic_message(self, prompt: Prompt) -> dict:
        """将 Prompt 转换成 Anthropic Messages API 格式"""
        ...

    def _create_openai_message(self, prompt: Prompt) -> list[dict]:
        """将 Prompt 转换成 OpenAI Chat Completions API 格式"""
        ...

    def _should_retry(self, error: Exception, attempt: int) -> bool:
        """判断是否应重试（超时/限流 → 重试；鉴权失败 → 不重试）"""
        ...
```

**设计要点**：
- 通过 `provider` 参数切换 SDK，对外接口（`invoke` / `stream`）完全不变
- `_create_anthropic_message` 和 `_create_openai_message` 负责把统一的 `Prompt` 结构翻译为各自 API 的消息格式
- OpenAI 模式下 `base_url` 默认为 `https://api.openai.com/v1`，设置后可以对接任何兼容接口（Ollama、DeepSeek、本地模型等）
- 两个提供商使用相同的重试策略
```

**设计要点**：
- `api_key` 也可以为 None，此时从环境变量读取 —— 允许调用方显式传参或依赖环境
- `model` 默认值用具体的模型 ID 而非别名，避免歧义
- 重试使用指数退避：1s → 2s → 4s
- `invoke` (阻塞) 和 `stream` (流式) 两个方法满足不同场景。CLI 用 `stream`，测试用 `invoke`
- 不在此模块中处理 prompt 截断逻辑 —— 那是 `PromptBuilder` 的职责。此模块只管"发请求，拿响应"

---

### 5.4 ReportGenerator (`report_generator.py`)

**职责**：将 LLM 原始响应解析为 `ReviewReport`，并写入 Markdown 文件。

```python
class ReportGenerator:
    """
    解析 LLM 响应并生成报告文件。

    用法:
        generator = ReportGenerator(output_dir=Path("doureview"))
        report = generator.parse(response, diff_result)
        path = generator.write(report)
        # 或一步完成
        path = generator.generate(response, diff_result)
    """

    def __init__(self, output_dir: Path = Path("doureview")) -> None:
        """
        Args:
            output_dir: 报告输出目录，自动创建
        """
        ...

    def parse(self, response: str, diff: DiffResult) -> ReviewReport:
        """
        从 LLM 响应中解析结构化审查报告。

        解析策略：
        1. 尝试匹配 Markdown 标题模式（## Critical / ## Warning / ## Suggestion）
        2. 提取每个 section 下的 ### `file:line` — title 条目
        3. 提取 **问题** / **建议** 段落

        Args:
            response: LLM 原始响应文本
            diff: 原始 diff 数据（用于填充报告元信息）

        Returns:
            ReviewReport

        Raises:
            ReportParseError: 无法解析时抛出，调用方应降级为原始响应写入
        """
        ...

    def write(self, report: ReviewReport) -> Path:
        """
        将 ReviewReport 写入 Markdown 文件。

        文件路径: {output_dir}/review-{timestamp}.md

        Returns:
            写入的文件路径
        """
        ...

    def generate(self, response: str, diff: DiffResult) -> Path:
        """
        一步完成 parse + write。

        若 parse 抛出 ReportParseError，降级为写入原始 response。

        Returns:
            写入的文件路径
        """
        ...
```

**设计要点**：
- `parse` 和 `write` 分离，便于单独测试解析逻辑
- `generate` 是便捷方法，内置降级策略（解析失败 → 原始响应）
- 解析器不是正则暴力匹配，而是基于 LLM 在 prompt 中被约束的输出格式 —— 双向约定。如果 LLM 不遵守格式，它出错了而不是解析器出错了
- 报告文件名包含时间戳，确保不会覆盖历史报告

---

### 5.5 Pipeline (`pipeline.py`)

**职责**：编排四个模块，对外暴露唯一的 `run()` 方法。

```python
class Pipeline:
    """
    编排审查流程。

    默认审查工作区所有改动（git diff HEAD），不分暂存/未暂存。
    指定 base/head 时审查指定范围的改动。

    用法:
        pipeline = Pipeline(
            diff_parser=DiffParser(),
            prompt_builder=PromptBuilder(severity=Severity.NORMAL),
            llm_client=LLMClient(),
            report_generator=ReportGenerator(),
        )
        # 默认：审查所有未提交的改动
        report_path = pipeline.run()
        # 指定范围
        report_path = pipeline.run(base="main", head="feature/xxx")
    """

    def __init__(
        self,
        diff_parser: DiffParser,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        report_generator: ReportGenerator,
    ) -> None:
        """依赖注入 —— 四个模块全部从外部传入"""
        ...

    def run(
        self,
        base: str | None = None,
        head: str | None = None,
        stream: bool = True,
    ) -> Path:
        """
        执行完整的审查流程。

        默认模式（base 和 head 均为 None）：
          git diff HEAD → 审查工作区所有未提交的改动
        指定范围模式：
          git diff base head → 审查两个引用之间的改动

        1. DiffParser.parse(...) → DiffResult
        2. PromptBuilder.build(DiffResult) → Prompt
        3. LLMClient.invoke/stream(Prompt) → str
        4. ReportGenerator.generate(str, DiffResult) → Path

        Args:
            base: 可选，diff 范围的起点（如 "main"）
            head: 可选，diff 范围的终点（如 "HEAD"）
            stream: True 时流式输出到终端

        Returns:
            报告文件路径

        Raises:
            NotAGitRepoError: 非 git 仓库
            EmptyDiffError: 无变更
            LLMAuthError: API Key 无效
        """
        ...
```

**设计要点**：
- `Pipeline` 不创建任何模块实例，全部通过构造函数注入。这意味着：
  - 测试时可以注入 mock，不依赖真实 git/API
  - V2 时只需替换构造函数参数，不需要改 `run()`
- `run()` 是唯一的公开方法，封装了完整的四阶段流程
- `stream=True` 时进度实时输出到终端，`stream=False` 时静默执行（测试或脚本场景）

---

## 6. CLI 入口 (`cli.py`)

CLI 只暴露审查范围相关的参数。API Key、模型、输出目录等一次性配置全部走 `.env`。

```bash
dourevew                              # 审查所有未提交的改动（最常用）
dourevew --base main --head HEAD      # 审查从 main 到当前 HEAD 的改动
```

```python
import typer
from pathlib import Path
from doureview.pipeline import Pipeline
from doureview.diff_parser import DiffParser
from doureview.prompt_builder import PromptBuilder, Severity
from doureview.llm_client import LLMClient
from doureview.report_generator import ReportGenerator
from doureview.config import Config

app = typer.Typer()

@app.command()
def review(
    base: str = typer.Option(None, "--base", "-b", help="diff 范围的起点（如 main）"),
    head: str = typer.Option(None, "--head", "-H", help="diff 范围的终点（如 HEAD）"),
):
    """DouReview —— AI 代码审查工具"""
    Config.validate()

    # 根据 .env 中 DOUREVIEW_PROVIDER 自动选择 API Key 和提供商
    if Config.PROVIDER == Provider.ANTHROPIC:
        llm_client = LLMClient(provider="anthropic", api_key=Config.ANTHROPIC_API_KEY, model=Config.MODEL)
    else:
        llm_client = LLMClient(
            provider="openai",
            api_key=Config.OPENAI_API_KEY,
            base_url=Config.OPENAI_BASE_URL,
            model=Config.MODEL,
        )

    pipeline = Pipeline(
        diff_parser=DiffParser(max_lines=Config.MAX_DIFF_LINES),
        prompt_builder=PromptBuilder(severity=Config.SEVERITY),
        llm_client=llm_client,
        report_generator=ReportGenerator(output_dir=Config.OUTPUT_DIR),
    )
    try:
        report_path = pipeline.run(base=base, head=head, stream=not Config.QUIET)
        typer.echo(f"\n✅ Review report: {report_path}")
    except Exception as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1)


def main():
    app()
```

---

## 7. 配置管理 (`config.py`)

V1 最简方案：环境变量 + `.env` 文件。

```python
import os
from pathlib import Path
from dotenv import load_dotenv

# 自动加载项目根目录的 .env
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
    LOG_LEVEL: str = os.getenv("DOUREVIEW_LOG_LEVEL", "INFO")

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
```

**设计要点**：
- `Config.validate()` 在 CLI 启动时调用，提前发现配置问题
- 默认值直接写死在代码中（Python 标准做法），而非依赖 `.env.example`
- `.env.example` 仅作为文档存在，列出所有可配置项

---

## 8. 错误处理流程

```
pipeline.run()
  │
  ├─ DiffParser.parse()
  │   ├─ NotAGitRepoError    → 打印错误，exit(1)
  │   └─ EmptyDiffError      → 打印 "无变更"，exit(0)
  │
  ├─ PromptBuilder.build()
  │   └─ (无致命错误，只有截断告警)
  │
  ├─ LLMClient.invoke()
  │   ├─ LLMAuthError        → 打印 "检查 API Key"，exit(1)
  │   ├─ LLMRateLimitError   → 自动重试，耗尽后打印错误，exit(1)
  │   └─ LLMError            → 自动重试，耗尽后打印错误，exit(1)
  │
  └─ ReportGenerator.generate()
      └─ ReportParseError    → 降级：写入原始响应，打印警告，继续
```

---

## 9. 测试策略

| 测试层级 | 测试对象 | 策略 |
|---------|---------|------|
| 单元测试 | `DiffParser._parse_output` | 用固定的 unified diff 文本作为输入，断言 `DiffResult` 结构 |
| 单元测试 | `PromptBuilder.build` | 用构造的 `DiffResult` 作为输入，断言 `Prompt` 包含关键字段 |
| 单元测试 | `ReportGenerator.parse` | 用固定的 LLM 响应文本作为输入，断言 `ReviewReport` 结构 |
| 单元测试 | `ReportGenerator.parse` 异常 | 用格式异常的文本，断言抛出 `ReportParseError` |
| 集成测试 | `LLMClient.invoke` | 用测试 API Key 真实调用 LLM，验证往返正常（可选：用 mock） |
| 端到端测试 | `Pipeline.run` | 在一个临时 git 仓库中创建文件、commit、修改，然后跑完整流程 |

**mock 边界**：
- `LLMClient` 是唯一需要 mock 的模块 —— 避免测试依赖外部 API
- `DiffParser` 不 mock —— 它的输入是真实的 git 命令，测试时在临时仓库中操作
- `Pipeline.run()` 测试使用 mock 的 `LLMClient` + 真实的其他三个模块

---

## 10. pyproject.toml 骨架

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "dourevew"
version = "0.1.0"
description = "AI-powered code review agent for personal developers"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.30.0",
    "typer>=0.12.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
dourevew = "dourevew.cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
]
```

---

## 11. 从 V1 到 V2 的迁移路径

V2 想启用 LangChain 时，最小改动方案：

```python
# V2: 只替换 LLMClient 和 PromptBuilder 的实现

class LangChainLLMClient:
    """用 LangChain 重新实现 LLMClient 的接口"""
    def __init__(self, ...): ...
    def invoke(self, prompt: Prompt) -> str: ...
    def stream(self, prompt: Prompt) -> Iterator[str]: ...

class LangChainPromptBuilder:
    """用 ChatPromptTemplate 重新实现 PromptBuilder 的接口"""
    def __init__(self, ...): ...
    def build(self, diff: DiffResult) -> Prompt: ...

# Pipeline 一行不改
pipeline = Pipeline(
    diff_parser=DiffParser(),        # 不动
    prompt_builder=LangChainPromptBuilder(),  # 换
    llm_client=LangChainLLMClient(),          # 换
    report_generator=ReportGenerator(),       # 不动
)
```

`Pipeline`、`DiffParser`、`ReportGenerator` 完全不受影响 —— 这就是可替换接口的价值。