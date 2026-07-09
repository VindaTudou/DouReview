# DouReview

面向个人开发者的 AI 代码审查 CLI 工具。读取 `git diff`，交由 LLM 分析，生成结构化的 Markdown 审查报告。

## 快速开始

```bash
# 1. 克隆并创建个人配置
git clone <repo-url> && cd DouReview
cp src/doureview/.env.example src/doureview/.env
vim src/doureview/.env    # 填入你的 API Key（见下方配置参考）

# 2. 安装（全局可用）
pipx install .

# 3. 在任意 git 仓库中运行
cd your-project
doureview
```

> 配置详情见下方[配置](#配置)章节。

## 效果

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

报告保存在 `doureview/review-{时间戳}.md`，每次运行不覆盖历史记录。

## 命令

```bash
doureview                              # 审查所有未提交的改动（默认）
doureview --base main --head HEAD      # 审查 main 到当前 HEAD 的改动
doureview --base main --head feature/x # 审查功能分支的改动
```

## 配置

配置从 DouReview 包目录的 `.env` 文件加载。安装前将 `.env.example` 复制为 `.env` 并填入你的 API Key：

```bash
cp src/doureview/.env.example src/doureview/.env
vim src/doureview/.env
```

> `.env` 不会被 git 追踪（`.gitignore` 已忽略），但会被打包进安装包。升级时重新克隆并编辑 `.env` 后 `pipx install --force .` 即可。

### 选择 LLM 提供商

**Anthropic (Claude):**

```bash
DOUREVIEW_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
DOUREVIEW_MODEL=claude-sonnet-4-5-20250914
```

**OpenAI:**

```bash
DOUREVIEW_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
DOUREVIEW_MODEL=gpt-4o
```

**DeepSeek / Ollama / 其他兼容接口:**

```bash
DOUREVIEW_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com    # 或 http://localhost:11434/v1
DOUREVIEW_MODEL=deepseek-v4-pro
```

### 通用配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOUREVIEW_SEVERITY` | `normal` | 审查严格程度: `strict` / `normal` / `relaxed` |
| `DOUREVIEW_MAX_DIFF_LINES` | `1000` | diff 行数上限，超过则截断并标注 |
| `DOUREVIEW_OUTPUT_DIR` | `doureview` | 报告输出目录 |
| `DOUREVIEW_QUIET` | `false` | 设为 `true` 关闭流式输出 |

## 开发

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## 架构

```
git diff ──▶ Prompt 构造 ──▶ LLM 调用 ──▶ Report 生成
```

详细设计见 [docs/design.md](docs/design.md) 和 [docs/architecture.md](docs/architecture.md)。

## License

MIT
