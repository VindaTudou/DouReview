# DouReview — AI Code Review Agent

面向个人开发者的 CLI 代码审查工具：读取 `git diff`，交由 LLM 分析，生成结构化的 Review Report。
 
---

## 背景与动机

- **目标用户**：个人开发者，在本地提交前或 PR 提交后快速自查代码质量。
- **解决的问题**：人工逐行 review 自己的改动耗时且容易遗漏；现有工具（GitHub Copilot Code Review、CodeRabbit）多为团队/CI 集成设计，个人使用门槛高或需要付费。
- **差异化**：零配置 CLI，单命令运行，只依赖 `git` + LLM API，审查结果落盘为本地 Markdown 文件，方便归档和引用。

---

## 目标与非目标

### V1 目标

- [x] 读取 `git diff HEAD`（默认审查所有未提交的改动），支持指定审查范围（`--base --head`）
- [x] 将 diff 注入 Prompt 模板，调用 LLM 生成代码审查
- [x] 输出结构化的 Markdown Review Report，包含文件路径、行号、严重程度、问题描述、修复建议
- [x] 作为单命令 CLI 工具可运行（如 `dourevew --base main --head HEAD`）
- [x] 典型 diff（200 行以内）审查在 30 秒内完成

### V1 非目标（明确不做）

- 不做 Web UI / GUI
- 不集成 GitHub PR / GitLab MR（V2 考虑）
- 不支持自定义审查规则（V2 考虑）
- 不做多人协作 review
- 不做增量/全量代码仓库分析

---

## 架构设计

### 整体数据流

```
git diff ──▶ Prompt 构造 ──▶ LLM 调用 ──▶ Report 生成
   │              │                │              │
   │        注入 diff 内容    模型分析代码     格式化输出
   │        拼装审查指令      查找 bug /       写入 .md 文件
   │                         评判代码质量
```

### 关键设计决策

| 决策 | 选择 | 原因 | 备选方案与代价 |
|------|------|------|---------------|
| 架构模式 | 四阶段流水线 | 每阶段职责单一，易于测试和替换 | 大单体灵活但难维护 |
| diff 处理 | 全量注入 Prompt（有截断保护） | V1 场景 diff 通常 <500 行，简单直接 | 分片处理更稳但增加复杂度，V2 考虑 |
| 大 diff 策略 | 超出上下文窗口时截断并告警 | 先保证不崩溃，再迭代优化 | 分片 + 聚合更完整但 V1 性价比低 |
| LLM 接入 | Claude API | 代码理解能力强，支持长上下文 | GPT-4 也可，但 Claude 在代码任务上表现更稳定 |
| Report 格式 | Markdown 文件，落盘到 `./doureview/` | 可版本控制、可归档、可被任何编辑器打开 | JSON 更结构化但人不易读 |

---

## 模块设计

### 模块 1：Diff Parser

- **输入**：git 仓库路径 + diff 模式（unstaged / staged / commit-range）
- **输出**：结构化的 diff 数据（文件列表，每个文件包含变更行号、变更内容、变更类型）
- **处理**：
  1. 执行 `git diff`（根据模式拼参数）
  2. 解析 unified diff 格式
  3. 提取文件名、hunk 头、+/- 行
  4. 统计变更行数，若超过上限（如 1000 行）则标记为 "large diff"
- **边界情况**：
  - 空 diff → 提示 "无变更内容，跳过审查"
  - 二进制文件 → 跳过，标注 "binary file, skipped"
  - 删除文件 / 重命名文件 → 正确识别变更类型
  - 非 git 仓库 → 报错退出

### 模块 2：Prompt Builder

- **输入**：结构化 diff 数据 + 审查配置（严格程度、关注领域）
- **输出**：完整的 LLM Prompt（system prompt + user prompt）
- **处理**：
  1. 加载 Prompt 模板（内置默认模板）
  2. 将 diff 数据注入模板的占位符
  3. 拼接审查指令（查找 bug、代码质量、安全隐患、最佳实践）
  4. 附加输出格式约束（要求 LLM 按指定 Markdown 结构输出）
- **设计要点**：
  - System prompt 定义角色和审查标准
  - User prompt 包含具体 diff 和输出格式要求
  - 若 diff 过大，在 prompt 中明确告知 LLM 内容已被截断

### 模块 3：LLM Client

- **输入**：完整 Prompt
- **输出**：LLM 原始响应文本
- **处理**：
  1. 调用 LLM API（Claude API / OpenAI 兼容接口）
  2. 流式输出审查进度到终端
  3. 返回完整响应
- **错误处理**：
  | 场景 | 策略 |
  |------|------|
  | 网络超时 | 重试 3 次，指数退避（1s / 2s / 4s） |
  | API 鉴权失败 | 提示检查 API Key，退出 |
  | 速率限制 | 等待 Retry-After 后重试 |
  | 响应格式异常 | 记录原始响应到日志，报告解析失败 |
  | 上下文超限 | 截断 diff 后重试一次 |

### 模块 4：Report Generator

- **输入**：LLM 响应文本
- **输出**：Markdown 报告文件，写入 `./doureview/review-{timestamp}.md`
- **处理**：
  1. 解析 LLM 响应，提取结构化内容
  2. 若解析失败，将原始响应作为 fallback 写入报告
  3. 生成报告头部（时间、分支、变更统计）
  4. 按严重程度排序（Critical → Warning → Suggestion）
  5. 写入 Markdown 文件
- **输出格式示例**：

```markdown
# Code Review Report
**时间**: 2026-07-07 17:30
**分支**: feature/user-auth → main
**变更统计**: 3 files, +120 / -45

---

## Critical (1)

### `src/auth/login.go:42` — nil pointer dereference risk
**问题**: `user.Session` 在未做 nil 检查的情况下直接访问 `.Token`
**建议**:
```go
if user.Session != nil {
    token := user.Session.Token
}
```

## Warning (2)

### `src/auth/login.go:78` — 错误未向上传播
...

## Suggestion (3)

### `src/auth/login.go:15` — 函数过长，建议拆分
...
```

---

## 错误处理总览

| 阶段 | 错误场景 | 处理策略 |
|------|---------|---------|
| Diff Parser | 非 git 仓库 | 报错退出，exit code 1 |
| Diff Parser | 空 diff | 提示并正常退出，exit code 0 |
| Prompt Builder | 模板文件缺失 | 使用内置硬编码默认模板 |
| LLM Client | 网络/鉴权/限流 | 重试 + 友好报错 |
| LLM Client | diff 超出上下文 | 截断 + 告警标注 |
| Report Generator | 响应解析失败 | 降级为原始响应写入 |

---

## 技术选型

| 组件 | 选择 | 原因 |
|------|------|------|
| 语言 | Python 3.11+ | 生态成熟，LLM SDK 支持好，开发效率高 |
| LLM SDK | `anthropic` (官方) | 直接调用 Claude API，无额外抽象层 |
| CLI 框架 | `click` / `typer` | 轻量，参数解析清晰 |
| 测试 | `pytest` | Python 事实标准 |
| 配置 | 环境变量 + `.env` 文件 | V1 最简方案，V2 考虑配置文件 |

---

## CLI 接口设计

CLI 只暴露审查范围，其余配置（API Key、模型、输出目录等）通过 `.env` 文件管理。

```bash
# 审查所有未提交的改动（默认，最常用）
dourevew

# 审查两个引用之间的改动
dourevew --base main --head feature/xxx
dourevew --base main --head HEAD
```

`.env` 配置示例：

```bash
# 二选一：anthropic 或 openai
DOUREVIEW_PROVIDER=anthropic

# ---- Anthropic ----
ANTHROPIC_API_KEY=sk-ant-xxx
DOUREVIEW_MODEL=claude-sonnet-4-5-20250914

# ---- 或 OpenAI / 兼容接口 ----
# DOUREVIEW_PROVIDER=openai
# OPENAI_API_KEY=sk-xxx
# OPENAI_BASE_URL=https://api.openai.com/v1
# DOUREVIEW_MODEL=gpt-4o

# ---- 通用配置 ----
DOUREVIEW_SEVERITY=normal          # strict | normal | relaxed
DOUREVIEW_MAX_DIFF_LINES=1000
DOUREVIEW_OUTPUT_DIR=dourevew
```

---

## V1 验收标准

- [ ] `dourevew` 单命令可运行，无额外配置（除 API Key）
- [ ] `dourevew` 单命令可运行（默认审查所有未提交的改动），支持 `--base --head` 指定审查范围
- [ ] 对 200 行以内的 diff，审查完成时间 < 30 秒
- [ ] Report 包含：文件路径、行号、严重程度（Critical/Warning/Suggestion）、问题描述、修复建议
- [ ] Report 按严重程度排序，写入 `./doureview/` 目录
- [ ] 空 diff 时友好提示并正常退出
- [ ] 网络错误时自动重试，鉴权失败时给出明确提示
- [ ] 二进制文件变更被正确跳过并标注

---

## 未来迭代

- **V2**: GitHub PR / GitLab MR 集成，支持 inline comment
- **V2**: 自定义审查规则（按语言、按关注领域）
- **V2**: 大 diff 分片处理（超过上下文窗口时分片审查 + 聚合）
- **V3**: 审查历史对比（同一文件多次审查的变化趋势）
- **V3**: 本地模型支持（Ollama 集成，敏感代码不出本地）
