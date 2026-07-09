import re
from pathlib import Path
from datetime import datetime

from .models import DiffResult, IssueSeverity, ReviewIssue, ReviewReport
from .errors import ReportParseError


class ReportGenerator:
    """
    解析 LLM 响应并生成报告文件。

    用法:
        generator = ReportGenerator(output_dir=Path("doureview"))
        report = generator.parse(response, diff_result)
        path = generator.write(report)
    """

    def __init__(self, output_dir: Path = Path("doureview")) -> None:
        self.output_dir = output_dir

    def parse(self, response: str, diff: DiffResult) -> ReviewReport:
        """从 LLM 响应中解析结构化审查报告。"""
        issues: list[ReviewIssue] = []

        current_severity: IssueSeverity | None = None
        current_issue: dict[str, str] = {}
        in_code_block = False

        lines = response.splitlines()
        for line in lines:
            stripped = line.strip()

            # 跟踪代码块边界
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            # 识别严重程度部分 —— 先保存上一个 issue，再切换 severity
            if stripped.startswith("## Critical"):
                if current_issue.get("title") and current_severity:
                    issues.append(self._build_issue(current_severity, current_issue))
                    current_issue = {}
                current_severity = IssueSeverity.CRITICAL
                continue
            elif stripped.startswith("## Warning"):
                if current_issue.get("title") and current_severity:
                    issues.append(self._build_issue(current_severity, current_issue))
                    current_issue = {}
                current_severity = IssueSeverity.WARNING
                continue
            elif stripped.startswith("## Suggestion"):
                if current_issue.get("title") and current_severity:
                    issues.append(self._build_issue(current_severity, current_issue))
                    current_issue = {}
                current_severity = IssueSeverity.SUGGESTION
                continue

            # 识别问题条目
            if stripped.startswith("### ") and current_severity:
                # 保存上一个问题
                if current_issue.get("title"):
                    issues.append(self._build_issue(current_severity, current_issue))
                    current_issue = {}

                # 解析 "### file:line — title"
                header = stripped[4:].strip()
                file_part = ""
                title = header

                # 尝试提取文件路径和行号
                file_match = re.match(r"`([^`]+)`\s*[-—]\s*(.+)", header)
                if file_match:
                    file_part = file_match.group(1)
                    title = file_match.group(2).strip()

                current_issue["file_line"] = file_part
                current_issue["title"] = title
                continue

            # 提取问题描述和建议
            if current_severity and current_issue.get("title"):
                if stripped.startswith("**问题**") or stripped.startswith("**问题：**"):
                    current_issue["description"] = self._extract_after_colon(stripped)
                elif stripped.startswith("**建议**") or stripped.startswith("**建议：**"):
                    current_issue["suggestion"] = self._extract_after_colon(stripped)

        # 保存最后一个问题
        if current_issue.get("title") and current_severity:
            issues.append(self._build_issue(current_severity, current_issue))

        return ReviewReport(
            timestamp=datetime.now(),
            base=diff.base,
            head=diff.head,
            files_changed=len(diff.files),
            lines_added=diff.total_lines_added,
            lines_deleted=diff.total_lines_deleted,
            is_truncated=diff.is_truncated,
            issues=issues,
        )

    def write(self, report: ReviewReport) -> Path:
        """将 ReviewReport 写入 Markdown 文件。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = report.timestamp.strftime("%Y%m%d-%H%M%S")
        filepath = self.output_dir / f"review-{timestamp}.md"

        parts: list[str] = []
        parts.append("# Code Review Report")
        parts.append("")
        parts.append(f"**时间**: {report.timestamp.strftime('%Y-%m-%d %H:%M')}")

        if report.base and report.head:
            parts.append(f"**审查范围**: {report.base} → {report.head}")
        else:
            parts.append("**审查范围**: 工作区未提交的改动")

        parts.append(f"**变更统计**: {report.files_changed} files, +{report.lines_added} / -{report.lines_deleted}")
        if report.is_truncated:
            parts.append("⚠️ **注意**: diff 内容已超出行数上限，以下审查仅覆盖部分变更。")
        parts.append("")
        parts.append("---")
        parts.append("")

        # Critical
        criticals = [i for i in report.issues if i.severity == IssueSeverity.CRITICAL]
        parts.append(f"## Critical ({len(criticals)})")
        parts.append("")
        if criticals:
            for issue in criticals:
                parts.extend(self._format_issue(issue))
        else:
            parts.append("无")
            parts.append("")

        # Warning
        warnings = [i for i in report.issues if i.severity == IssueSeverity.WARNING]
        parts.append(f"## Warning ({len(warnings)})")
        parts.append("")
        if warnings:
            for issue in warnings:
                parts.extend(self._format_issue(issue))
        else:
            parts.append("无")
            parts.append("")

        # Suggestion
        suggestions = [i for i in report.issues if i.severity == IssueSeverity.SUGGESTION]
        parts.append(f"## Suggestion ({len(suggestions)})")
        parts.append("")
        if suggestions:
            for issue in suggestions:
                parts.extend(self._format_issue(issue))
        else:
            parts.append("无")
            parts.append("")

        filepath.write_text("\n".join(parts), encoding="utf-8")
        return filepath

    def generate(self, response: str, diff: DiffResult) -> Path:
        """一步完成 parse + write。若 parse 失败则降级为写入原始响应。"""
        try:
            report = self.parse(response, diff)
        except ReportParseError:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filepath = self.output_dir / f"review-{timestamp}.md"
            filepath.write_text(response, encoding="utf-8")
            return filepath
        return self.write(report)

    # ── 内部方法 ──

    @staticmethod
    def _extract_after_colon(text: str) -> str:
        """提取冒号后的内容。处理 LLM 输出的各种格式变体。"""
        # 精确匹配已知模式（按长度降序，先匹配长的避免短的前缀误匹配）
        prefixes = [
            "**问题**：** ", "**问题**: ** ", "**建议**：** ", "**建议**: ** ",
            "**问题：** ** ", "**建议：** ** ",
            "**问题**：", "**问题**：** ", "**问题**: ", "**问题**:",
            "**建议**：", "**建议**：** ", "**建议**: ", "**建议**:",
            "**问题：** ", "**问题：**", "**建议：** ", "**建议：**",
        ]
        for prefix in sorted(prefixes, key=len, reverse=True):
            if text.startswith(prefix):
                return text[len(prefix):].strip()
        # 通用提取：找第一个冒号后的内容
        for sep in ["：", ": "]:
            idx = text.find(sep)
            if idx != -1:
                after = text[idx + len(sep):].lstrip()
                while after.startswith("** "):
                    after = after[3:].lstrip()
                return after
        return text.strip()

    @staticmethod
    def _build_issue(severity: IssueSeverity, raw: dict) -> ReviewIssue:
        file_path = ""
        line_number = None

        file_line = raw.get("file_line", "")
        if ":" in file_line:
            parts = file_line.rsplit(":", 1)
            file_path = parts[0].strip()
            try:
                line_number = int(parts[1].strip())
            except ValueError:
                pass
        else:
            file_path = file_line

        return ReviewIssue(
            severity=severity,
            file_path=file_path,
            line_number=line_number,
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            suggestion=raw.get("suggestion", ""),
        )

    @staticmethod
    def _format_issue(issue: ReviewIssue) -> list[str]:
        location = issue.file_path
        if issue.line_number is not None:
            location = f"{location}:{issue.line_number}"

        parts = [
            f"### `{location}` — {issue.title}",
            "",
            f"**问题**: {issue.description}",
            "",
            f"**建议**: {issue.suggestion}",
            "",
        ]
        return parts
