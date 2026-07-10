# DouReview V2 设计文档

## 1. 问题与目标

### 当前问题（V1）

V1 的 LLM 只看到裸 `git diff`，没有项目的架构背景和完整代码上下文。审查结果往往是泛泛的通用建议（"考虑添加错误处理"、"注意命名规范"），无法理解项目使用的框架、设计模式、模块间调用关系，导致建议质量低、实用性差。

### V2 目标

将 DouReview 从"单轮 prompt"升级为 **Agentic Code Review 工具**——LLM 在审查过程中可以自主调用工具读取项目文档和源码，理解上下文后再给出建议。

---

## 2. 核心变化

```
V1：git diff → Prompt → LLM → 报告

V2：git diff → Agent循环 → LLM + 工具 → 报告
                        ↓
            ┌──────────────────────────┐
            │ read_file(path)           │
            │ read_symbol(file, name)   │
            │ search(pattern, path?)    │
            │ list_dir(path?)           │
            └──────────────────────────┘
```

---

## 3. 工作流程

### 3.1 Agent 循环

给 LLM 注入 diff 内容和工具集，发起多轮对话。文档发现、代码阅读、符号提取全部由 LLM 在循环中自主决定——不需要预先扫描。

**注入内容：**
1. System prompt：审查者角色 + 工具使用指引
2. User prompt：diff 内容 + 变更统计
3. Tools：代码探索工具集（`read_file`、`read_symbol`、`search`、`list_dir`）

**典型流程：**
```
LLM: 让我先看下项目结构 → list_dir()
LLM: docs/ 和 src/ 目录，读一下 README.md → read_file("README.md")
LLM: 现在看一下 diff 涉及的函数 → read_symbol("src/pipeline.py", "run")
LLM: 搜索这个函数的调用者 → search("pipeline.run")
LLM: 上下文清楚了，开始审查...
     ## Critical
     ### `src/pipeline.py:63` — ...
```

**循环逻辑：**
```
预算初始化：
    max_turns = 20           # 安全兜底
    read_file_budget = 8     # 读文件最耗 token，单独限额
    search_budget = 10       # grep 便宜，适当放宽
    idle_rounds = 0          # 自适应退出计数器

while 轮次 < max_turns:
    发送 messages + tools → LLM 响应
    if 响应是纯 text（无 tool_calls）:
        idle_rounds += 1
        if idle_rounds >= 2:
            → 连续 2 轮无工具调用，探索充分，退出循环
    else:
        idle_rounds = 0      # 有工具调用，重置计数器
        → 执行工具调用，扣减对应预算
        → 将结果追加到 messages，继续下一轮
```

**分类预算：** 不同工具成本不同，分开限制：

| 限制项 | 默认值 | 理由 |
|--------|--------|------|
| 最大轮次 | 20 | 安全兜底，防止无限循环 |
| `read_file` 上限 | 8 次 | 最耗 token，需单独限 |
| `search` 上限 | 10 次 | 便宜但防滥用 |
| `read_symbol` / `list_dir` | 不限 | 成本低，不应限制 |
| 自适应退出 | 连续 2 轮无工具调用 | LLM 自主决定何时探索充分 |

预算信息在 system prompt 中告知 LLM，让它自己规划如何分配。预算耗尽时工具返回提示文本（如"已达到 read_file 调用上限"），LLM 感知后自动转向输出审查结果。

**LLM 自主决策：** LLM 决定读哪些文件、看哪些函数、搜索什么内容。Prompt 中会提示优先使用 `read_symbol` 而非 `read_file`（减少 token 消耗），以及建议在开始审查前先探索相关模块。

### 3.3 报告生成（不变）

LLM 最终输出的审查结果由 `ReportGenerator` 解析并生成 Markdown 报告。格式与 V1 完全兼容。

---

## 4. 工具定义

### 4.1 `read_file`

```
read_file(path: str) → str
```

读取文件的完整内容。项目相对路径。返回文件内容或错误信息。

### 4.2 `read_symbol`

```
read_symbol(file: str, symbol_name: str) → str
```

读取指定 Python 文件中某个函数或类的完整源码。使用 `ast` 标准库解析。第一期只支持 Python。

返回：函数/类的签名和源码，或 "未找到" 错误。

### 4.3 `search`

```
search(pattern: str, path: str | None = None) → str
```

在项目中搜索代码引用、调用关系。底层用 `grep` 或 `git grep`。支持路径过滤，返回匹配列表（文件:行号:内容），截断到 50 条。

### 4.4 `list_dir`

```
list_dir(path: str | None = None) → str
```

列出目录结构。不传参数时列出项目根目录。返回树状结构，过滤掉 `.git`、`node_modules`、`__pycache__`、`venv` 等常见忽略目录。

---

## 5. LLM Client 变化

### 5.1 新增多轮对话方法

```python
def chat(
    self,
    prompt: Prompt,
    tools: list[dict],
    max_turns: int = 10,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """多轮 tool-calling 对话，返回最终响应文本。"""
```

在 `LLMClient` 中实现 tool-calling 循环：

- 维护 messages 列表
- 每轮调用 LLM，解析响应中的 `tool_calls`
- Anthropic 路径：使用 `messages.create` + `tools` 参数，原生日志
- OpenAI 路径：使用 `chat.completions.create` + `tools` 参数，原生 function-calling
- 两种 SDK 的 tool-calling 均在 `LLMClient` 内统一处理

### 5.2 工具结果编解码

工具定义为 JSON Schema（两个 SDK 格式基本兼容），工具结果以 `tool_result` 形式回传给 LLM。

---

## 6. Pipeline 变化

```python
class Pipeline:
    def run(
        self,
        base: str | None = None,
        head: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> Path:
```

新增：
- **工具注册**：`_build_tools()` → 返回工具定义列表
- **工具执行器**：`_execute_tool(name, args, cwd)` → 路由到对应工具
- **Prompt 构建**：System prompt 中加入工具使用说明
- **Agent 循环**：调用 `llm_client.chat()` 代替 `llm_client.invoke()/stream()`

---

## 7. 项目结构

```
src/doureview/
├── __init__.py
├── cli.py              # 不变
├── config.py           # 不变
├── diff_parser.py      # 不变
├── errors.py           # 不变
├── llm_client.py       # 新增 chat()，tool-calling 能力
├── models.py           # 新增 ToolCall / ToolResult / ToolDefinition 模型
├── pipeline.py         # 新增 Agent 循环
├── tools.py            # 新增：工具定义 + 路由执行（read_file、search、list_dir）
├── code_explorer.py    # 新增：read_symbol（Python AST）
├── prompt_builder.py   # 修改：system prompt 增加工具使用指引
└── report_generator.py # 不变
```

新增模块：

| 模块 | 职责 | 行数估算 |
|------|------|----------|
| `tools.py` | 工具定义（JSON Schema）+ 路由执行 | ~120 行 |
| `code_explorer.py` | `read_symbol`（Python AST） | ~100 行 |

不再需要的模块：

| 模块 | 原因 |
|------|------|
| ~~doc_discovery.py~~ | 文档发现改为 LLM 通过 `list_dir` + `read_file` 工具自主完成，无需预扫描 |

---

## 8. 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | 自实现循环 | 见下方详细分析 |
| 语言支持 | 第一期 Python only | `ast` 标准库，零依赖 |
| 工具执行 | 同步顺序执行 | 简单可控，不需要并行 |
| 工具定义格式 | JSON Schema（Anthropic 格式） | OpenAI 格式可映射 |
| 最大轮次 | 默认 20 轮 + 分类预算 + 自适应退出 | 不依赖纯轮次限制，LLM 自主决策退场 |
| 上下文注入 | System prompt + LLM 通过工具自主获取 | 不预扫描，灵活且省 token |
| diff 上下文 | LLM 自主通过工具获取 | 不需要预加载 |

### 为什么不使用 LangChain

DouReview 的 Agent 循环本质上只需要做一件事：

> 在一个 while 循环里，把 messages 和 tools 发给 LLM，如果 LLM 返回 tool_calls 就执行并回传结果，如果返回 text 就结束。

Anthropic SDK 和 OpenAI SDK 都已经原生支持 tool-use / function-calling，且 DouReview 的 `LLMClient` 已经在这两个 SDK 之上做了一层薄封装。我们需要新增的能力就这一点，自己写就是几十行的事。

引入 LangChain 会带来什么：

| 维度 | LangChain | 自实现 |
|------|-----------|--------|
| Agent 循环 | 内置 `AgentExecutor`，但黑盒 | 透明的 while 循环，30 行 |
| Tool 定义 | 需要继承 `BaseTool` 或用 `@tool` 装饰器 | 原生 JSON Schema dict，和 SDK 格式一致 |
| 依赖体积 | +50MB，`langchain` + `langchain-core` + `langchain-community` 等 | 0 新增 |
| 调试难度 | 需要理解 LangChain 的回调系统和 trace 机制 | `print()` 或日志直接看到每轮 message |
| 版本风险 | LangChain 版本迭代快，API 频繁 break | 不依赖外部 |
| 抽象泄露 | 出了 bug 需要在 LangChain 源码里 debug | 所有代码自己写的，清晰可控 |
| 定制成本 | 需要学 LangChain 的 Agent/Chain/Callback 概念体系 | 无学习成本 |
| 项目现状 | 需要改写现有的 `LLMClient` 来适配 LangChain 的 LLM 接口 | `LLMClient` 增加一个方法即可 |

**DouReview 的定位是个人开发者的轻量 CLI 工具**，不是企业级 Agent 平台。LangChain 擅长解决的问题——多 Agent 协作、RAG 管道、复杂的回调链、多种 LLM 统一适配——DouReview 都不需要。为一个 30 行的循环引入 50MB 的依赖和一套新的抽象体系，不值得。

如果未来需求变化（比如需要 RAG 检索项目文档、需要多 Agent 并行审查不同模块），那时再评估要不要引入。但第一版 Agent，完全是自实现更合适。

---

## 9. 风险与边界

- **Token 消耗**：多轮对话 + 工具结果会显著增加 token 使用。设计策略：分类预算（`read_file` 限 8 次、`search` 限 5 次）、system prompt 提示 LLM 优先用低成本工具（`read_symbol` 而非 `read_file`）、自适应退出避免多余的探索轮次
- **幻觉风险**：LLM 可能读不存在文件。工具返回明确错误信息，LLM 感知后自动调整
- **超时**：Agent 循环默认 10 轮，加上 LLM 调用可能有多次 API 请求。需在前端显示进度
- **不是 GitHub Copilot**：这只做 code review 场景的上下文增强，不做实时补全、不集成 IDE