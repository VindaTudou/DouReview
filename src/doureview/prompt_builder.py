from .models import DiffResult, Prompt, Severity


SYSTEM_PROMPT = """你是一个资深代码审查专家。请仔细审查以下 git diff 中的代码变更。

审查标准：
- 查找潜在的 bug（空指针、边界条件、并发问题等）
- 评估代码质量（可读性、可维护性、是否遵循最佳实践）
- 检查安全隐患（注入漏洞、敏感信息泄露等）
- 指出可以改进的性能问题

输出要求：
- 严格按以下 Markdown 格式输出，不要添加任何额外内容
- 如果没有发现某类问题，写"无"即可
- 不要输出无关的客套话

## Critical
（可能导致崩溃或安全漏洞的严重问题）
### `文件路径:行号` — 简短标题
**问题**: ...
**建议**: ...

## Warning
（潜在风险、错误处理遗漏等）
### `文件路径:行号` — 简短标题
**问题**: ...
**建议**: ...

## Suggestion
（代码风格、可读性、最佳实践建议）
### `文件路径:行号` — 简短标题
**问题**: ...
**建议**: ..."""


class PromptBuilder:
    """
    构建 LLM prompt。

    用法:
        builder = PromptBuilder(severity=Severity.NORMAL)
        prompt = builder.build(diff_result)
    """

    def __init__(self, severity: Severity = Severity.NORMAL) -> None:
        self.severity = severity

    def build(self, diff: DiffResult) -> Prompt:
        """将 diff 数据注入 prompt 模板。"""
        system = self._system_prompt()
        user = self._user_prompt(diff)
        return Prompt(system=system, user=user)

    # ── 内部方法 ──

    def _system_prompt(self) -> str:
        """构建 system prompt —— 定义角色和审查标准。"""
        severity_notes = {
            Severity.STRICT: "请以最严格的标准审查，不放过任何小问题。",
            Severity.NORMAL: "请以正常标准审查，关注有明显影响的问题。",
            Severity.RELAXED: "请以宽松标准审查，只报告严重的 bug 和安全问题。",
        }
        note = severity_notes.get(self.severity, "")
        return SYSTEM_PROMPT + f"\n\n审查严格程度：{note}"

    def _user_prompt(self, diff: DiffResult) -> str:
        """构建 user prompt —— 包含 diff 内容和输出格式要求。"""
        parts: list[str] = []
        parts.append("请审查以下代码变更：\n")

        if diff.is_truncated:
            parts.append(
                "⚠️ 注意：diff 内容超过行数上限，已被截断。"
                "以下审查仅覆盖前 {} 行变更。\n".format(self._format_diff.__code__.co_argcount)
            )
            parts.append("⚠️ 注意：diff 内容超过行数上限，已被截断。\n")

        if diff.base and diff.head:
            parts.append(f"审查范围：{diff.base} → {diff.head}\n")

        parts.append(f"共 {len(diff.files)} 个文件变更 (+{diff.total_lines_added} / -{diff.total_lines_deleted})：\n\n")

        formatted = self._format_diff(diff)
        parts.append(formatted)

        return "\n".join(parts)

    @staticmethod
    def _format_diff(diff: DiffResult) -> str:
        """将 DiffResult 格式化为 LLM prompt 中的文本块。"""
        parts: list[str] = []
        for f in diff.files:
            if f.change_type.value == "binary":
                parts.append(f"```\n[二进制文件] {f.path}\n```\n")
                continue

            header = f"### {f.path}"
            if f.old_path and f.old_path != f.path:
                header += f" (原路径: {f.old_path})"
            header += f"  [{f.change_type.value}]  +{f.lines_added} / -{f.lines_deleted}"
            parts.append(header)
            parts.append("```diff")

            for hunk in f.hunks:
                parts.append(hunk.header)
                for line in hunk.lines:
                    parts.append(f"{line.type}{line.content}")

            parts.append("```\n")

        return "\n".join(parts)
