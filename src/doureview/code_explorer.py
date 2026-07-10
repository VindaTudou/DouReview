from __future__ import annotations

import ast
from pathlib import Path


class CodeExplorer:
    """
    代码探索器。第一期仅支持 Python。

    用法:
        explorer = CodeExplorer()
        src = explorer.read_symbol("src/main.py", "parse_config", Path.cwd())
        # 返回函数 parse_config 的完整源码，含 docstring 和函数体
    """

    def read_symbol(self, file_path: str, symbol_name: str, cwd: Path) -> str:
        """
        读取 Python 文件中指定符号（函数或类）的源码。

        使用 ast 标准库解析。

        Args:
            file_path: 相对于项目根目录的文件路径
            symbol_name: 函数名或类名
            cwd: 项目根目录

        Returns:
            成功时返回格式化的源码：
                def parse_config(path: str) -> Config:
                    \"\"\"读取配置文件\"\"\"
                    ...
            失败时返回错误描述，如：
                "错误：文件 src/main.py 不存在"
                "错误：在 src/main.py 中未找到符号 parse_config"
        """
        full_path = cwd / file_path
        if not full_path.exists():
            return f"错误：文件 {file_path} 不存在"

        try:
            source = full_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"错误：无法读取 {file_path} —— {e}"

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return f"错误：{file_path} 不是有效的 Python 文件 —— {e}"

        # 查找函数/类
        node = self._find_symbol(tree, symbol_name)
        if node is None:
            return f"错误：在 {file_path} 中未找到符号 {symbol_name}"

        lines = source.splitlines()
        return self._get_source_lines(node, lines)

    # ── 内部方法 ──

    def _find_symbol(self, tree: ast.Module, name: str) -> ast.AST | None:
        """在 AST 中查找函数定义（含异步函数）或类定义。"""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    return node
            elif isinstance(node, ast.ClassDef):
                if node.name == name:
                    return node
        return None

    @staticmethod
    def _get_source_lines(node: ast.AST, lines: list[str]) -> str:
        """从源码行数组中提取 AST 节点对应的源码文本。"""
        start = node.lineno - 1  # ast 行号从 1 开始
        end = node.end_lineno    # 1-based，直接用作 slice end（slice end 是 exclusive）
        return "\n".join(lines[start:end])
