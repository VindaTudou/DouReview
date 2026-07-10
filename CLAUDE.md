# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 开发安装（可编辑模式）
pip install -e ".[dev]"

# 运行测试
python3.11 -m pytest tests/ -v          # 全部
python3.11 -m pytest tests/test_diff_parser.py -v  # 单文件

# 安装为全局 CLI 工具（测试生产行为）
pipx install --force .

# 审查当前项目
doureview                  # V2 Agent 审查
doureview -v               # V2 verbose：显示 LLM 工具调用过程
doureview --base main --head HEAD   # 指定 diff 范围
```

项目要求 Python >= 3.11（类型注解用 `X | None` 语法，不使用 `from __future__ import annotations`）。

## 架构

### 数据流（V2 默认路径）

```
git diff → DiffParser → DiffResult
              ↓
        PromptBuilder → Prompt（system + user + 工具指引）
              ↓
        LLMClient.chat() → Agent 循环（最多 40 轮）
              │
              │  每轮：发送 messages + tools → LLM 返回 text 或 tool_calls
              │  tool_calls → ToolRegistry.execute() → 结果回传 LLM
              │  无 tool_calls → 退出循环，返回审查文本
              ↓
        ReportGenerator → Markdown 报告文件
```

### V1/V2 双模式

`Pipeline.run()` 同时支持 V1 和 V2，通过 `tool_registry` 参数自动切换：

- **传入 `ToolRegistry`** → V2 Agent 循环（`llm_client.chat()`），LLM 可调用工具探索代码库
- **未传入 `ToolRegistry`** → V1 单轮 prompt（`llm_client.invoke()` 或 `stream()`），LLM 只看到裸 diff

CLI（`cli.py`）默认启用 V2 模式。

### 模块职责

| 模块 | 职责 | V1/V2 |
|------|------|:--:|
| `diff_parser.py` | 执行 `git diff`，解析 unified diff 为 `DiffResult` | V1 |
| `prompt_builder.py` | 构建 system + user prompt，V2 版注入工具使用指引 | 共用 |
| `llm_client.py` | 封装 Anthropic/OpenAI SDK，提供 `invoke`/`stream`/`chat` | 共用 |
| `tools.py` | `ToolRegistry`：4 个工具的注册、预算控制、路由执行 | V2 |
| `code_explorer.py` | Python AST 解析，按函数/类名提取完整源码 | V2 |
| `logger.py` | `VerboseLogger`：Agent 工具调用过程的可视化输出 | V2 |
| `report_generator.py` | 解析 LLM 响应为 `ReviewReport`，写入 Markdown | V1 |
| `pipeline.py` | 编排层，唯一对外接口 `run()` | 共用 |
| `models.py` | 所有模块间的数据模型（dataclass + Enum） | 共用 |

### 关键设计约束

- **模块间只通过 `models.py` 通信**：不互相 import（除了 `pipeline.py` 作为编排层）
- **依赖注入**：`Pipeline.__init__` 接收所有模块实例，不隐式创建
- **LLM 内部方法命名**：`_invoke_anthropic` / `_invoke_openai` 模式区分 provider，Agent 循环 `chat()` 内部用中性格式（`ToolCall`/`ToolResult`），`_build_tool_result_messages` 等方法做 format 转换
- **工具错误不抛异常**：全部以自然语言字符串返回 LLM，让它自主决策

### 分类预算（Agent 防滥用）

| 预算项 | 默认值 | 说明 |
|--------|:--:|------|
| 最大轮次 | 40 | 安全兜底 |
| `read_file` | 15 次 | 最耗 token |
| `search` | 20 次 | git grep |
| `read_symbol` / `list_dir` | 不限 | 成本低 |

连续一轮无 tool_calls 时自适应退出（LLM 已给出最终结果）。

### 配置

配置仅从 `src/doureview/.env` 加载（`python-dotenv` with `override=True`）。不读环境变量、不读 `cwd/.env`、不读 `~/.config/`。安装前需从 `.env.example` 复制。支持 Anthropic 和 OpenAI（含任何兼容接口如 DeepSeek、Ollama）。
