# DouReview

面向个人开发者的 AI 代码审查 CLI 工具。读取 `git diff`，LLM 以 Agent 模式自主探索代码库（读取源码、搜索引用、浏览目录），生成结构化的 Markdown 审查报告。

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url> && cd DouReview

# 2. 创建配置文件
cp src/doureview/.env.example src/doureview/.env
vim src/doureview/.env    # 填入 API Key（见下方配置参考）

# 3. 安装为全局 CLI
pipx install .

# 4. 在任意 git 仓库中运行
cd your-project
doureview
```

## 效果示例

`doureview` 审查所有未提交的改动，实时输出审查进度，最终生成报告：

```
## Critical (3)
### `src/order_service.py:61` — 库存不足时静默返回导致超卖
...
### `src/user_service.py:74` — 密码明文存储，严重安全隐患
...
### `src/user_service.py:44` — 硬删除用户且无权限检查

## Warning (2)
### `src/user_service.py:28` — register 方法删除了空值校验
...
### `src/utils.py:37` — parse_json_response 未捕获 JSONDecodeError
...
```

报告保存在 `doureview/review-{时间戳}.md`，每次运行生成新文件，不覆盖历史记录。

## 命令

```bash
doureview                              # 审查所有未提交的改动（默认）
doureview --base main --head HEAD      # 审查 main 到当前 HEAD 的改动
doureview --base main --head feature/x # 审查功能分支的全部改动
doureview -v                           # verbose 模式：显示 LLM 工具调用过程
```

## 工作原理

DouReview 以 **Agent 模式**工作 —— LLM 不只是看 `git diff`，而是像人类审查者一样主动探索代码库：

```
git diff → Prompt 构建 → Agent 循环 → LLM 自主探索代码 → 审查报告
                              ↓
                  ┌──────────────────────────┐
                  │ read_file(path)           │ ← 读取文件完整内容
                  │ read_symbol(file, name)   │ ← 读取函数/类源码（AST）
                  │ search(pattern, path?)    │ ← git grep 搜索代码引用
                  │ list_dir(path?)           │ ← 浏览目录结构
                  └──────────────────────────┘
```

LLM 在审查过程中自主决定读哪些文件、看哪些函数、搜索什么内容。例如发现一个函数调用变更，LLM 会用 `read_symbol` 读取该函数的完整实现来判断调用是否正确，而非仅凭 diff 片段猜测。

Agent 循环最多 40 轮，`read_file` 上限 15 次、`search` 上限 20 次，LLM 不再需要探索时自适应退出。

## 配置

配置从 `src/doureview/.env` 加载（安装后为包目录下的 `.env`）。启动前需从 `.env.example` 复制并填入你的 API Key：

```bash
cp src/doureview/.env.example src/doureview/.env
```

> `.env` 包含 API Key，已被 `.gitignore` 忽略，不会被提交到 git。pipx 安装时不会将其打包进安装包，因此升级后需重新创建 `.env`。

### LLM 提供商

**Anthropic (Claude)：**

```bash
DOUREVIEW_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
DOUREVIEW_MODEL=claude-sonnet-4-5-20250914
```

**OpenAI：**

```bash
DOUREVIEW_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
DOUREVIEW_MODEL=gpt-4o
```

**DeepSeek / Ollama / 其他兼容接口：**

```bash
DOUREVIEW_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com    # 或 http://localhost:11434/v1
DOUREVIEW_MODEL=deepseek-v4-pro
```

### 通用配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOUREVIEW_SEVERITY` | `normal` | 审查严格程度：`strict` / `normal` / `relaxed` |
| `DOUREVIEW_MAX_DIFF_LINES` | `1000` | diff 行数上限，超过则截断并标注 |
| `DOUREVIEW_OUTPUT_DIR` | `doureview` | 报告输出目录（相对于被审查项目的根目录） |
| `DOUREVIEW_QUIET` | `false` | 设为 `true` 关闭流式输出 |

## 开发

```bash
# 可编辑安装
pip install -e ".[dev]"

# 运行测试
python3.11 -m pytest tests/ -v
python3.11 -m pytest tests/test_diff_parser.py -v  # 单文件

# 安装为全局 CLI 测试
pipx install --force .
```

项目要求 Python >= 3.11（类型注解使用 `X | None` 语法）。

## 架构

```
src/doureview/
├── cli.py              # CLI 入口（Typer）
├── config.py           # 配置加载（仅从包 .env 读取）
├── diff_parser.py      # git diff 解析
├── prompt_builder.py   # System + User prompt 构建
├── llm_client.py       # LLM 调用（Anthropic/OpenAI，含 Agent 循环）
├── tools.py            # 工具注册表 & 4 个代码探索工具
├── code_explorer.py    # Python AST：read_symbol 实现
├── logger.py           # Verbose 模式：工具调用可视化
├── report_generator.py # Markdown 报告生成
├── pipeline.py         # 编排层，唯一对外接口
├── models.py           # 所有模块间的数据模型
└── errors.py           # 异常类型
```

### 模块职责

| 模块 | 职责 |
|------|------|
| `diff_parser.py` | 执行 `git diff`，解析 unified diff 为 `DiffResult` |
| `prompt_builder.py` | 构建 system + user prompt，V2 注入工具使用指引 |
| `llm_client.py` | 封装 Anthropic/OpenAI SDK，提供 `invoke`/`stream`/`chat`（Agent 循环） |
| `tools.py` | `ToolRegistry`：4 个工具的注册、预算控制、路由执行 |
| `code_explorer.py` | Python AST 解析，按函数/类名提取完整源码 |
| `logger.py` | `VerboseLogger`：Agent 工具调用过程的可视化输出 |
| `report_generator.py` | 解析 LLM 响应为 `ReviewReport`，写入 Markdown |
| `pipeline.py` | 编排层，统一 V1/V2 流程，对外只暴露 `run()` |
| `models.py` | 所有模块间的数据模型（dataclass + Enum），模块间不互相 import |
| `config.py` | 配置加载与校验 |
| `errors.py` | 自定义异常类型 |

### 关键设计

- **模块间只通过 `models.py` 通信**：不互相 import（`pipeline.py` 作为编排层除外）
- **依赖注入**：`Pipeline.__init__` 接收所有模块实例，不隐式创建
- **工具错误不抛异常**：全部以自然语言字符串返回 LLM，让它自主决策
- **V1/V2 自动切换**：`Pipeline.run()` 根据是否传入 `ToolRegistry` 选择 V2 Agent 循环或 V1 单轮调用
- **双 Provider 支持**：Anthropic 原生 tool_use 和 OpenAI function-calling 均在 `llm_client.py` 内统一为中性格式

详细设计见 `docs/` 目录下的 V2 设计文档和架构文档。

## License

MIT
