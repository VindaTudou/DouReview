from doureview.prompt_builder import PromptBuilder, Severity
from doureview.models import (
    DiffMode, DiffResult, FileChange, Hunk, DiffLine, ChangeType
)


class TestPromptBuilder:
    """测试 PromptBuilder.build()。"""

    def test_build_returns_prompt_with_system_and_user(self):
        """build 返回包含 system 和 user 字段的 Prompt。"""
        diff = _make_simple_diff()
        builder = PromptBuilder()
        prompt = builder.build(diff)

        assert "代码审查专家" in prompt.system
        assert "src/main.py" in prompt.user
        assert len(prompt.system) > 50
        assert len(prompt.user) > 50

    def test_severity_affects_system_prompt(self):
        """不同严格程度影响 system prompt 内容。"""
        diff = _make_simple_diff()
        strict = PromptBuilder(severity=Severity.STRICT).build(diff)
        relaxed = PromptBuilder(severity=Severity.RELAXED).build(diff)

        assert "最严格" in strict.system
        assert "宽松" in relaxed.system

    def test_truncated_diff_shows_warning(self):
        """截断 diff 时 user prompt 包含警告。"""
        diff = _make_simple_diff(is_truncated=True)
        prompt = PromptBuilder().build(diff)

        assert "截断" in prompt.user

    def test_file_path_appears_in_user_prompt(self):
        """user prompt 包含变更文件的路径。"""
        diff = _make_simple_diff()
        prompt = PromptBuilder().build(diff)

        assert "src/main.py" in prompt.user

    def test_line_changes_appear_in_user_prompt(self):
        """user prompt 包含具体变更行。"""
        diff = _make_simple_diff()
        prompt = PromptBuilder().build(diff)

        # diff 格式中应有 + / - 行
        assert "+def hello():" in prompt.user
        assert "-def old_hello():" in prompt.user


def _make_simple_diff(is_truncated: bool = False) -> DiffResult:
    return DiffResult(
        files=[
            FileChange(
                path="src/main.py",
                old_path=None,
                change_type=ChangeType.MODIFIED,
                hunks=[
                    Hunk(
                        header="@@ -1,2 +1,2 @@",
                        lines=[
                            DiffLine(type="-", content="def old_hello():", old_lineno=1, new_lineno=None),
                            DiffLine(type="+", content="def hello():", old_lineno=None, new_lineno=1),
                        ],
                    )
                ],
                lines_added=1,
                lines_deleted=1,
            )
        ],
        total_lines_added=1,
        total_lines_deleted=1,
        is_truncated=is_truncated,
        mode=DiffMode.UNSTAGED,
        base=None,
        head=None,
    )
