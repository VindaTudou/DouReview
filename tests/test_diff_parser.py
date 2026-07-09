from doureview.diff_parser import DiffParser, DiffMode
from doureview.models import ChangeType


class TestDiffParser:
    """测试 DiffParser._parse_output —— 不依赖真实 git 仓库。"""

    def test_parse_modified_file(self, sample_unified_diff):
        """解析标准 modified 文件。"""
        parser = DiffParser()
        result = parser._parse_output(sample_unified_diff, DiffMode.UNSTAGED, None, None)

        assert len(result.files) == 3
        assert result.total_lines_added == 4  # hello.py +1, new_file.py +3 (含空行)
        assert result.total_lines_deleted == 1  # hello.py -1
        assert not result.is_truncated

    def test_parse_identifies_change_types(self, sample_unified_diff):
        """正确识别文件变更类型。"""
        parser = DiffParser()
        result = parser._parse_output(sample_unified_diff, DiffMode.UNSTAGED, None, None)

        changes = {f.path: f.change_type for f in result.files}
        binary_file = [f for f in result.files if f.path == "logo.png"][0]

        assert changes["hello.py"] == ChangeType.MODIFIED
        assert changes["new_file.py"] == ChangeType.ADDED
        assert binary_file.change_type == ChangeType.BINARY

    def test_binary_file_no_hunks(self, sample_unified_diff):
        """二进制文件不解析 hunks。"""
        parser = DiffParser()
        result = parser._parse_output(sample_unified_diff, DiffMode.UNSTAGED, None, None)

        binary = [f for f in result.files if f.change_type == ChangeType.BINARY][0]
        assert len(binary.hunks) == 0
        assert binary.lines_added == 0
        assert binary.lines_deleted == 0

    def test_hunk_lines_parsed_correctly(self, sample_unified_diff):
        """Hunk 行正确区分 + / - / 空格。"""
        parser = DiffParser()
        result = parser._parse_output(sample_unified_diff, DiffMode.UNSTAGED, None, None)

        hello = [f for f in result.files if f.path == "hello.py"][0]
        assert len(hello.hunks) == 1
        hunk = hello.hunks[0]
        types = [line.type for line in hunk.lines]
        assert "+" in types
        assert "-" in types
        assert " " in types  # 上下文行

    def test_truncation_detection(self):
        """超过 max_lines 时标记 is_truncated。"""
        parser = DiffParser(max_lines=3)
        diff_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,0 +1,2 @@
+line1
+line2
+line3
+line4
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1,0 +1,2 @@
+other
+another
"""
        result = parser._parse_output(diff_text, DiffMode.UNSTAGED, None, None)
        assert result.is_truncated
