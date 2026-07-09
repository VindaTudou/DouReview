import subprocess
from pathlib import Path

from .models import DiffMode, DiffResult, FileChange, Hunk, DiffLine, ChangeType
from .errors import NotAGitRepoError, EmptyDiffError


class DiffParser:
    """
    解析 git diff 输出。

    用法:
        parser = DiffParser(repo_path=Path.cwd(), max_lines=1000)
        result = parser.parse(DiffMode.STAGED)
    """

    def __init__(self, repo_path: Path | None = None, max_lines: int = 1000) -> None:
        self.repo_path = repo_path or Path.cwd()
        self.max_lines = max_lines

    def parse(
        self,
        mode: DiffMode,
        base: str | None = None,
        head: str | None = None,
    ) -> DiffResult:
        """执行 git diff 并返回结构化结果。"""
        cmd = self._build_command(mode, base, head)
        raw = self._run_git(cmd)
        if not raw.strip():
            raise EmptyDiffError("没有变更内容，跳过审查。")
        return self._parse_output(raw, mode, base, head)

    # ── 内部方法 ──

    def _build_command(self, mode: DiffMode, base: str | None, head: str | None) -> list[str]:
        """根据模式拼接 git diff 参数。"""
        if mode == DiffMode.COMMITTED:
            if not base or not head:
                raise ValueError("COMMITTED 模式需要提供 base 和 head 参数。")
            return ["git", "-C", str(self.repo_path), "diff", base, head]
        elif mode == DiffMode.STAGED:
            return ["git", "-C", str(self.repo_path), "diff", "--staged"]
        else:
            return ["git", "-C", str(self.repo_path), "diff"]

    def _run_git(self, cmd: list[str]) -> str:
        """执行 git 命令，返回 stdout。"""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True
            )
            return result.stdout
        except subprocess.CalledProcessError:
            raise NotAGitRepoError("当前目录不是 git 仓库，或 git 命令执行失败。")

    def _parse_output(
        self, raw: str, mode: DiffMode, base: str | None, head: str | None
    ) -> DiffResult:
        """解析 unified diff 格式的原始输出。"""
        files: list[FileChange] = []
        total_added = 0
        total_deleted = 0

        raw_parts = raw.split("diff --git ")
        for part in raw_parts:
            if not part.strip():
                continue
            file_change = self._parse_file(part)
            total_added += file_change.lines_added
            total_deleted += file_change.lines_deleted
            files.append(file_change)

        is_truncated = (total_added + total_deleted) > self.max_lines

        return DiffResult(
            files=files,
            total_lines_added=total_added,
            total_lines_deleted=total_deleted,
            is_truncated=is_truncated,
            mode=mode,
            base=base,
            head=head,
        )

    def _parse_file(self, raw_section: str) -> FileChange:
        """解析单个文件的 diff。"""
        lines = raw_section.splitlines()
        path = ""
        old_path = None
        change_type = ChangeType.MODIFIED
        hunks: list[Hunk] = []
        lines_added = 0
        lines_deleted = 0

        # 从 diff --git header 行作为 fallback 提取路径
        if lines and lines[0].startswith("a/"):
            parts = lines[0].split(" ", 1)
            if len(parts) == 2 and parts[1].startswith("b/"):
                old_path = parts[0][2:]   # 去掉 "a/"
                path = parts[1][2:]       # 去掉 "b/"

        # 提取文件路径（--- / +++ 覆盖 diff header 的结果）
        for line in lines:
            if line.startswith("--- a/"):
                old_path = line[6:]
            elif line.startswith("+++ b/"):
                path = line[6:]
            elif line.startswith("Binary files"):
                change_type = ChangeType.BINARY
                break
            elif line.startswith("new file mode"):
                change_type = ChangeType.ADDED
            elif line.startswith("deleted file mode"):
                change_type = ChangeType.DELETED
            elif line.startswith("rename from"):
                change_type = ChangeType.RENAMED
            elif line.startswith("rename to"):
                pass  # old_path 已在前面 captured

        # 二进制文件跳过 hunk 解析
        if change_type == ChangeType.BINARY:
            return FileChange(
                path=path or "_binary_",
                old_path=old_path,
                change_type=change_type,
                hunks=[],
                lines_added=0,
                lines_deleted=0,
            )

        # 解析 hunks
        current_hunk_lines: list[DiffLine] = []
        current_header = ""

        for line in lines:
            if line.startswith("@@"):
                if current_header:
                    hunks.append(Hunk(header=current_header, lines=current_hunk_lines))
                    current_hunk_lines = []
                current_header = line
            elif line.startswith("+") and not line.startswith("+++"):
                current_hunk_lines.append(
                    DiffLine(type="+", content=line[1:], old_lineno=None, new_lineno=None)
                )
                lines_added += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk_lines.append(
                    DiffLine(type="-", content=line[1:], old_lineno=None, new_lineno=None)
                )
                lines_deleted += 1
            elif line.startswith(" "):
                current_hunk_lines.append(
                    DiffLine(type=" ", content=line[1:], old_lineno=None, new_lineno=None)
                )

        if current_header:
            hunks.append(Hunk(header=current_header, lines=current_hunk_lines))

        return FileChange(
            path=path,
            old_path=old_path,
            change_type=change_type,
            hunks=hunks,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
        )
