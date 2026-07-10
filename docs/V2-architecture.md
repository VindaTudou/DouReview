# DouReview V2 — 代码架构与接口设计

V2 将审查流程从单轮 prompt 升级为 **Agentic Code Review**——LLM 在审查过程中可以自主调用工具读取项目文档和源码，理解上下文后再给出建议。

---

## 1. 目录结构

```
DouReview/
├── src/
│   └── doureview/
│       ├── __init__.py
│       ├── cli.py              # CLI 入口 (typer)
│       ├── config.py           # 配置管理（环境变量 + .env）
│       ├── models.py           # 共享数据模型（dataclass）
│       ├── errors.py           # 自定义异常
│       ├── diff_parser.py      # 模块1: DiffParser
│       ├── prompt_builder.py   # 模块2: PromptBuilder（V2: 增加工具使用指引）
│       ├── llm_client.py       # 模块3: LLMClient（V2: 新增 chat()）
│       ├── tools.py            # 新增：工具定义 + 路由执行
│       ├── code_explorer.py    # 新增：read_symbol（Python AST）
│       ├── report_generator.py # 模块4: ReportGenerator
│       └── pipeline.py         # 编排层（V2: 新增 Agent 循环）
├── tests/
│   ├── conftest.py
│   ├── test_diff_parser.py
│   ├── test_prompt_builder.py
│   ├── test_llm_client.py
│   ├── test_report_generator.py
│   ├── test_pipeline.py
│   ├── test_code_explorer.py
│   └── test_tools.py
├── docs/
│   ├── design.md               # V1 产品设计
│   ├── V1-design.md
│   ├── architecture.md          # V1 架构文档
│   ├── V2-design.md             # V2 产品设计
│   └── V2-architecture.md       # 本文档 —— V2 代码级架构
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
pipeline.py ─────────────────────────────────────────────┐
  │                                                       │
  ├──▶ DiffParser         ──▶ DiffResult                  │
  │                                      │                 │
  ├──▶ PromptBuilder      ◀─────────────┘                 │
  │         │                                              │
  │         ▼  Prompt(system + 工具指引, user + diff)      │
  │                                                       │
  ├──▶ LLMClient.chat()   ◀────────── Prompt + Tools      │
  │         │                                              │
  │         │  Agent 循环 (while max_turns)               │
  │         │  ├── LLM 返回 text → 完成                   │
  │         │  └── LLM 返回 tool_calls →                  │
  │         │       ├── tools.execute()                    │
  │         │       │   ├── read_file → code_explorer     │
  │         │       │   ├── read_symbol → code_explorer   │
  │         │       │   ├── search → git grep             │
  │         │       │   └── list_dir → filesystem          │
  │         │       └── 结果回传 LLM → 继续循环           │
  │         │                                              │
  │         ▼  str (最终审查结果)                          │
  ├──▶ ReportGenerator    ──▶ Path (report file)           │
  │                                                       │
  └───────────────────────────────────────────────────────┘

  数据模型层:  models.py  ◀─────────────────────────────┘
  异常层:      errors.py
  配置层:      config.py
  工具层:      tools.py + code_explorer.py
```

关键约束：
- 数据流是单向的。每个箭头都是纯数据，不传对象引用。
- LLM 通过工具（箭头回环）可以获取任意文件的完整上下文，这是 V2 相比 V1 的核心变化。
- Agent 循环由 `pipeline.py` 发起，但实际的 LLM 对话管理在 `llm_client.chat()` 中完成。

---

## 3. 数据模型 (`models.py`)

V2 新增的模型。V1 已有模型（DiffResult、Prompt、ReviewReport 等）保持不变。

```python
from dataclasses import dataclass, field


# ── Agent / Tool 阶段（V2 新增）──────────────────

@dataclass
class ToolCall:
    """LLM 请求调用一个工具"""
    id: str                     # tool_use 的唯一 ID，用于回传 tool_result
    name: str                   # 工具名称，如 "read_file"
    arguments: dict[str, object]  # 工具参数，如 {"path": "src/main.py"}


@dataclass
class ToolResult:
    """工具执行结果，回传给 LLM"""
    tool_call_id: str           # 对应 ToolCall.id
    name: str                   # 工具名称
    content: str                # 执行结果文本


@dataclass
class ToolDefinition:
    """工具定义（中性格式，同时支持 Anthropic 和 OpenAI）"""
    name: str
    description: str
    parameters: dict            # JSON Schema 对象
```

**设计决策**：
- `ToolCall` 和 `ToolResult` 是中性的中间格式。`LLMClient` 内部负责将 Anthropic 的 `tool_use` content block 和 OpenAI 的 `tool_calls` 数组统一转换为此格式，Agent 循环只看到中性格式。
- `ToolDefinition.parameters` 是标准 JSON Schema。Anthropic 和 OpenAI 的字段名略有不同（`input_schema` vs `parameters`），在 `_build_request` 的 provider 分支中分别转换，不在模型中引入两种格式。

---

## 4. 新增模块接口设计

### 4.1 CodeExplorer (`code_explorer.py`)

**职责**：提供代码级别的探索能力——按符号名提取 Python 函数/类源码。

```python
class CodeExplorer:
    """
    代码探索器。第一期仅支持 Python。

    用法:
        explorer = CodeExplorer()
        src = explorer.read_symbol("src/main.py", "parse_config")
        # 返回函数 parse_config 的完整源码
    """

    def read_symbol(self, file_path: str, symbol_name: str, cwd: Path) -> str:
        """
        读取 Python 文件中指定符号（函数或类）的源码。

        使用 ast 标准库解析。

        Args:
            file_path: 相对于项目根目录的文件路径
            symbol_name: 函数名或类名
            cwd: 项目根目录

        Returns:
            成功时返回格式化的源码：
                def parse_config(path: str) -> Config:
                    """读取配置文件"""
                    ...
            失败时返回错误描述，如：
                "错误：文件 src/main.py 不存在"
                "错误：symbol parse_config 未找到"
        """
        ...

    # ── 内部方法 ──

    def _find_function(self, tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """在 AST 中查找函数定义（含异步函数）"""
        ...

    def _find_class(self, tree: ast.Module, name: str) -> ast.ClassDef | None:
        """在 AST 中查找类定义"""
        ...

    def _get_source_lines(self, node: ast.AST, lines: list[str]) -> str:
        """从源码行数组中提取 AST 节点对应的源码文本"""
        ...
```

**设计要点**：
- `read_symbol` 返回**完整源码**（包括 docstring 和函数体），不是摘要或签名。LLM 需要看到完整实现才能做有效审查。
- 错误返回是自然语言字符串，不是抛异常。因为工具执行结果要直接回传给 LLM，错误信息要让 LLM 能理解并调整后续行为。
- 只支持 Python，`ast` 标准库，零新增依赖。

---

### 4.2 Tools (`tools.py`)

**职责**：工具注册、定义生成、请求路由执行。

```python
class ToolRegistry:
    """
    工具注册表 —— 管理所有可用工具的定义和执行路由。

    用法:
        registry = ToolRegistry(cwd=Path.cwd())
        registry.register_all()  # 注册内置工具

        # 获取工具定义（发给 LLM）
        definitions = registry.definitions()

        # 执行工具调用
        result = registry.execute(tool_call)
    """

    def __init__(self, cwd: Path) -> None:
        """
        Args:
            cwd: 目标项目根目录。工具的文件操作均相对于此路径。
        """
        ...

    def register_all(self) -> None:
        """注册所有内置工具：read_file, read_symbol, search, list_dir"""
        ...

    def definitions(self) -> list[ToolDefinition]:
        """返回所有已注册工具的定义列表，可直接传给 LLMClient"""
        ...

    def execute(self, call: ToolCall) -> ToolResult:
        """
        根据工具名路由到对应执行器。

        Raises:
            KeyError: 工具名未注册
        """
        ...

    # ── 工具执行器（每个工具一个方法） ──

    def _tool_read_file(self, path: str) -> str: ...
    def _tool_read_symbol(self, file: str, symbol_name: str) -> str: ...
    def _tool_search(self, pattern: str, path: str | None = None) -> str: ...
    def _tool_list_dir(self, path: str | None = None) -> str: ...
```

**工具定义汇总：**

| 工具 | 参数 | 底层实现 |
|------|------|----------|
| `read_file` | `path: str` | `Path.read_text()` |
| `read_symbol` | `file: str`, `symbol_name: str` | `CodeExplorer.read_symbol()` |
| `search` | `pattern: str`, `path: str \| None` | `subprocess.run(["git", "grep", ...])` |
| `list_dir` | `path: str \| None` | `os.listdir()` + 过滤 |

**设计要点**：
- `ToolRegistry` 和 `CodeExplorer` 的关系：Registry 是编排层，CodeExplorer 和 filesystem 操作是执行层。Registry 不直接做文件/代码操作，而是调用对应的执行器。
- 工具定义用中性 `ToolDefinition` 格式存储。传给 LLM 时在 `LLMClient._build_request()` 中按 provider 转换。
- 工具返回**绝不抛异常到调用方**。所有错误以字符串形式返回，让 LLM 自行决定如何响应。

---

## 5. 修改模块接口变化

### 5.1 LLMClient (`llm_client.py`) — V2 变化

V2 新增 `chat()` 方法，实现 tool-calling 多轮对话。V1 的 `invoke()` 和 `stream()` 保持不变（V1 单轮模式仍可用）。

**新增方法：**

```python
class LLMClient:
    # V1 方法不变: __init__, invoke, stream, _invoke_anthropic, _stream_anthropic, etc.

    def chat(
        self,
        prompt: Prompt,
        tools: list[ToolDefinition],
        max_turns: int = 20,
        read_file_budget: int = 8,
        search_budget: int = 10,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """
        Agent 循环 —— 多轮 tool-calling 对话，带分类预算和自适应退出。

        预算系统：
        - read_file_budget: 读文件上限（最耗 token）
        - search_budget: grep 搜索上限
        - read_symbol / list_dir 不限（成本低）
        - 预算耗尽时工具返回提示文本，LLM 感知后转向输出审查结果

        流程：
        1. 初始化 messages = [system prompt, user prompt]
        2. 每轮发送 messages + tools 给 LLM
           - 如果 LLM 返回纯 text → idle_rounds += 1
             → idle_rounds >= 2 时自适应退出（探索充分）
           - 如果 LLM 返回 tool_calls → idle_rounds = 0
             → 执行工具，扣减对应预算，结果追加到 messages
        3. 达到 max_turns → 安全兜底，返回当前文本

        Args:
            prompt: 包含 system 和 user 的 Prompt
            tools: 工具定义列表（中性 ToolDefinition 格式）
            max_turns: 安全兜底轮次上限（默认 20）
            read_file_budget: read_file 调用上限
            search_budget: search 调用上限
            on_chunk: 可选的流式文本回调

        Returns:
            最终审查结果文本
        """
        ...

    # ── 新增内部方法 ──

    def _build_request_body(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
    ) -> dict:
        """
        按当前 provider 构建请求体。

        Anthropic 格式:
        {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [...],
            "system": "...",
            "tools": [{
                "name": "read_file",
                "description": "...",
                "input_schema": {...}
            }]
        }

        OpenAI 格式:
        {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [...],  # system 是 messages 的第一条
            "tools": [{
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "...",
                    "parameters": {...}
                }
            }]
        }
        """
        ...

    def _send_request(self, body: dict) -> dict:
        """发送单轮请求，返回原始响应（首次调用 + 重试逻辑）"""
        ...

    def _parse_response(self, response: dict) -> tuple[str | None, list[ToolCall]]:
        """
        解析 LLM 响应，提取文本和 tool_calls。

        Anthropic 响应格式:
        - content 里有 type="text" 的 block → text
        - content 里有 type="tool_use" 的 block → ToolCall

        OpenAI 响应格式:
        - choices[0].message.content → text
        - choices[0].message.tool_calls → ToolCall 列表

        Returns:
            (text_or_none, tool_calls_list)
        """
        ...

    def _build_tool_result_message(self, results: list[ToolResult]) -> dict:
        """
        将工具执行结果构建为追加到 messages 的消息。

        Anthropic 格式:
        {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "...",
                "content": "..."
            }]
        }

        OpenAI 格式:
        每个 tool_result 是一条独立消息:
        {
            "role": "tool",
            "tool_call_id": "...",
            "content": "..."
        }
        """
        ...
```

**设计要点**：
- `chat()` 对调用方透明——Pipeline 只看到"给我最终文本"，不知道内部有多少轮 tool-calling。
- 三种 provider 的方法（`_build_request_body`、`_parse_response`、`_build_tool_result_message`）各做格式转换。循环逻辑写一次。
- `on_chunk` 只在 text 内容上生效，不输出 tool_use 的内部过程。终端用户看到的是审查结果流式输出，不是工具调用的日志。

### 5.2 PromptBuilder (`prompt_builder.py`) — V2 变化

System prompt 注入项目文档背景 + 工具使用指引。

```python
class PromptBuilder:
    # V1 方法不变

    def build(self, diff: DiffResult) -> Prompt:
        """
        V2: System prompt 增加工具使用指引。

        Returns:
            Prompt，system prompt 包含审查角色 + 工具使用指引
        """
        system = self._system_prompt(self.severity) + self._tool_usage_instructions()
        user = self._user_prompt(diff)
        return Prompt(system=system, user=user)

    @staticmethod
    def _tool_usage_instructions() -> str:
        """
        在 system prompt 中追加工具使用指引：
        - 你拥有以下工具可以探索代码库
        - 优先使用 read_symbol 而非 read_file（节省 token）
        - 先理解 diff 涉及的模块间关系，再开始审查
        - 只调用工具探索与 diff 真正相关的文件
        """
        ...
```

### 5.3 Pipeline (`pipeline.py`) — V2 变化

```python
class Pipeline:
    def __init__(
        self,
        diff_parser: DiffParser,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        report_generator: ReportGenerator,
        tool_registry: ToolRegistry | None = None,   # 新增
    ) -> None:
        ...

    def run(
        self,
        base: str | None = None,
        head: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        max_turns: int = 20,              # 新增：安全兜底
        read_file_budget: int = 8,        # 新增
        search_budget: int = 10,           # 新增
    ) -> Path:
        """
        1. DiffParser.parse() → DiffResult
        2. PromptBuilder.build(diff) → Prompt
        3. LLMClient.chat(prompt, tools, max_turns, budgets, on_chunk) → str
        4. ReportGenerator.generate(response, diff) → Path
        """
        ...

    # ── 内部方法 ──

    def _build_tools(self) -> list[ToolDefinition]:
        """从 ToolRegistry 获取工具定义"""
        ...
```

---

## 6. 错误处理

V2 新增错误场景：

```
pipeline.run()
  │
  ├─ LLMClient.chat()
  │   ├─ 某轮 LLM 调用失败 → 重试（V1 已有逻辑）
  │   ├─ 工具执行失败      → 错误文本回传 LLM，不中断循环
  │   ├─ 分类预算耗尽      → 工具返回提示文本，LLM 转向输出结果
  │   ├─ 连续 2 轮无工具调用 → 自适应退出（探索充分）
  │   ├─ 达到 max_turns    → 安全兜底，返回当前累积文本
  │   └─ ToolResult 构建失败 → LLMError
  │
  └─ ToolRegistry.execute()
      ├─ 工具不存在   → 返回错误文本（不抛异常）
      └─ Node 执行失败 → 返回错误文本（不抛异常）
```

**关键原则**：工具层的任何失败都以字符串形式返回给 LLM，让 LLM 决定下一步。不中断 Agent 循环。

---

## 7. Anthropic vs OpenAI 的 Tool-Calling 格式差异

LLMClient 内部处理，对外透明：

| 层面 | Anthropic (tool-use) | OpenAI (function-calling) |
|------|---------------------|--------------------------|
| 工具定义 | `input_schema` 字段 | `parameters` 字段 |
| 系统消息 | 顶层 `system` 参数 | messages 数组的第一条 |
| 工具调用 | content block `type: "tool_use"` | `tool_calls` 数组 |
| 结果回传 | content block `type: "tool_result"`, `role: "user"` | `role: "tool"`, 带 `tool_call_id` |
| 流式 | tool_use block 增量到达 | 需要解析 stream 中的 delta |

所有这些差异封装在三个方法中：
- `_build_request_body()` — 定义 → SDK 请求格式
- `_parse_response()` — SDK 响应 → `(str, list[ToolCall])`
- `_build_tool_result_message()` — `list[ToolResult]` → SDK messages 追加

---

## 8. 测试策略

### 新增单元测试

| 模块 | 测试对象 | 策略 |
|------|---------|------|
| `CodeExplorer` | `read_symbol()` | 用固定 Python 源码文件，断言函数/类提取 |
| `CodeExplorer` | 符号不存在 | 返回错误描述字符串 |
| `ToolRegistry` | `definitions()` | 断言返回 4 个工具定义 |
| `ToolRegistry` | `execute()` | 用真实 cwd，调用 `read_file` 验证结果 |
| `ToolRegistry` | 工具不存在 | 断言返回错误文本 |

### 修改的单元测试

| 模块 | 变化 |
|------|------|
| `PromptBuilder.build()` | 增加工具使用指引的测试 |
| `LLMClient.chat()` | Mock SDK 响应，验证循环逻辑和 tool_calling 流程 |
| `Pipeline.run()` | Mock LLMClient，验证 Agent 循环开关 |

### mock 边界坚持

- `LLMClient.chat()` 使用 mock 应答（已验证的 V1 模式）
- `DiffParser`, `ToolRegistry`, `CodeExplorer` 均不 mock——它们的操作是文件系统和 git，可预测且不依赖外部服务

---

## 9. 与 V1 的向后兼容

- `Pipeline.run()` 的签名完全向后兼容：`base`, `head`, `on_chunk` 行为不变
- `LLMClient.invoke()` 和 `stream()` 不变——V1 单轮模式仍可用
- `PromptBuilder.build(diff)` — 签名不变，内部追加了工具使用指引 section
- `ReportGenerator` 不变
- `DiffParser` 不变
- CLI (`cli.py`) 零变化——所有 V2 行为由 Pipeline 内部处理