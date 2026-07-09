from doureview.report_generator import ReportGenerator
from doureview.models import (
    DiffMode, DiffResult, FileChange, Hunk, DiffLine, ChangeType, IssueSeverity
)


class TestReportGenerator:
    """测试 ReportGenerator.parse()。"""

    def test_parse_critical_warning_suggestion(self):
        """正确解析三种严重程度的问题。"""
        response = """## Critical
### `src/auth.py:42` — nil pointer dereference
**问题**: user.Session accessed without nil check
**建议**: add nil check before accessing .Token

## Warning
### `src/auth.py:78` — error not propagated
**问题**: error is logged but not returned
**建议**: return the error

## Suggestion
### `src/auth.py:15` — function too long
**问题**: login function is 120 lines
**建议**: split into smaller functions
"""
        generator = ReportGenerator()
        report = generator.parse(response, _make_diff())

        assert len(report.issues) == 3
        assert report.critical_count == 1
        assert report.warning_count == 1
        assert report.suggestion_count == 1

    def test_parse_extracts_file_and_line(self):
        """正确提取文件路径和行号。"""
        response = """## Critical
### `path/to/file.go:42` — some issue
**问题**: something
**建议**: something
"""
        report = ReportGenerator().parse(response, _make_diff())

        issue = report.issues[0]
        assert issue.file_path == "path/to/file.go"
        assert issue.line_number == 42

    def test_parse_no_line_number_for_global_issues(self):
        """全局性问题行号为 None。"""
        response = """## Suggestion
### `src/main.py` — add type hints
**问题**: no type annotations
**建议**: add type hints
"""
        report = ReportGenerator().parse(response, _make_diff())

        assert report.issues[0].line_number is None

    def test_empty_section(self):
        """空 section（"无"）不产生 issue。"""
        response = """## Critical
无

## Warning
### `a.py:1` — something
**问题**: x
**建议**: y
"""
        report = ReportGenerator().parse(response, _make_diff())

        assert len(report.issues) == 1
        assert report.issues[0].severity == IssueSeverity.WARNING

    def test_report_metadata_from_diff(self):
        """报告元信息来自 DiffResult。"""
        diff = _make_diff()
        report = ReportGenerator().parse("## Critical\n无\n", diff)

        assert report.files_changed == 2
        assert report.lines_added == 5
        assert report.lines_deleted == 3
        assert not report.is_truncated


def _make_diff() -> DiffResult:
    return DiffResult(
        files=[
            FileChange(path="a.py", old_path=None, change_type=ChangeType.MODIFIED,
                       hunks=[], lines_added=3, lines_deleted=2),
            FileChange(path="b.py", old_path=None, change_type=ChangeType.ADDED,
                       hunks=[], lines_added=2, lines_deleted=1),
        ],
        total_lines_added=5,
        total_lines_deleted=3,
        is_truncated=False,
        mode=DiffMode.UNSTAGED,
        base=None,
        head=None,
    )
