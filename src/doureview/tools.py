from __future__ import annotations

import subprocess
from pathlib import Path

from .models import ToolCall, ToolResult, ToolDefinition
from .code_explorer import CodeExplorer


class ToolRegistry:
    """
    工具注册表 —— 管理所有可用工具的定义和执行路由。

    用法:
        registry = ToolRegistry(cwd=Path.cwd())
        registry.register_all()  # 注册内置工具

        # 获取工具定义（发给 LLM）
        definitions = registry.definitions()

        # 执行工具调用
        result = registry.execute(tool_call)
    """

    # 默认忽略的目录
    IGNORED_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', '.idea', '.pytest_cache', 'dist', 'build', '.egg-info'}

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._tools: dict[str, ToolDefinition] = {}
        self._explorer = CodeExplorer()

        # 预算计数器
        self._read_file_count = 0
        self._search_count = 0
        self._read_file_budget = 15
        self._search_budget = 20

    def set_budgets(self, read_file: int = 15, search: int = 20) -> None:
        """设置工具调用预算上限。"""
        self._read_file_budget = read_file
        self._search_budget = search

    def register_all(self) -> None:
        """注册所有内置工具。"""
        self._register("read_file", "读取文件的完整内容。用来阅读项目文档（README.md、docs/*.md）或需要完整上下文的源码文件。",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于项目根目录的文件路径，如 'README.md' 或 'src/main.py'"}
                },
                "required": ["path"]
            })
        self._register("read_symbol", "读取 Python 文件中指定函数或类的完整源码（含 docstring 和函数体）。优先使用此工具而非 read_file，可以节省 token。",
            {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "相对于项目根目录的 Python 文件路径"},
                    "symbol_name": {"type": "string", "description": "函数名或类名，如 'parse_config'"}
                },
                "required": ["file", "symbol_name"]
            })
        self._register("search", "在项目中搜索代码模式（底层用 git grep）。用于查找函数引用、调用关系、类使用位置等。返回文件:行号:内容 格式的匹配列表，最多 50 条。",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索模式，支持正则表达式，如 'def run' 或 'import.*pipeline'"},
                    "path": {"type": "string", "description": "可选，限定搜索路径。如 'src/' 只搜索 src 目录"}
                },
                "required": ["pattern"]
            })
        self._register("list_dir", "列出目录结构。用于浏览项目组织、发现文档和源码目录。不传参数时列出项目根目录。",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "可选，相对于项目根目录的路径。不传则列出根目录"}
                },
                "required": []
            })

    def _register(self, name: str, description: str, parameters: dict) -> None:
        """注册单个工具。"""
        self._tools[name] = ToolDefinition(name=name, description=description, parameters=parameters)

    def definitions(self) -> list[ToolDefinition]:
        """返回所有已注册工具的定义列表。"""
        return list(self._tools.values())

    def execute(self, call: ToolCall) -> ToolResult:
        """根据工具名路由到对应执行器。工具不存在时返回错误文本，不抛异常。"""
        handler = {
            "read_file": self._tool_read_file,
            "read_symbol": self._tool_read_symbol,
            "search": self._tool_search,
            "list_dir": self._tool_list_dir,
        }.get(call.name)

        if handler is None:
            available = ", ".join(self._tools.keys())
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"错误：未知工具 '{call.name}'。可用工具：{available}"
            )

        try:
            content = handler(call.arguments)
        except Exception as e:
            content = f"错误：执行 {call.name} 时出错 —— {e}"

        return ToolResult(tool_call_id=call.id, name=call.name, content=content)

    # ── 工具执行器 ──

    def _tool_read_file(self, args: dict[str, object]) -> str:
        """读取文件完整内容。"""
        if self._read_file_count >= self._read_file_budget:
            return f"已达到 read_file 调用上限（{self._read_file_budget} 次），请基于已有信息输出审查结果。"
        self._read_file_count += 1

        path = str(args.get("path", ""))
        if not path:
            return "错误：缺少 path 参数"

        full_path = self.cwd / path
        # 安全检查：防止路径穿越
        try:
            full_path = full_path.resolve()
            cwd_resolved = self.cwd.resolve()
            if not str(full_path).startswith(str(cwd_resolved) + "/") and full_path != cwd_resolved:
                return f"错误：路径 {path} 不在项目目录内"
        except Exception:
            return f"错误：无法解析路径 {path}"
        if not full_path.exists():
            return f"错误：文件 {path} 不存在"

        if full_path.is_dir():
            return f"错误：{path} 是一个目录，请使用 list_dir 查看目录内容"

        try:
            content = full_path.read_text(encoding="utf-8")
            return content
        except Exception as e:
            return f"错误：无法读取 {path} —— {e}"

    def _tool_read_symbol(self, args: dict[str, object]) -> str:
        """读取 Python 符号源码，委托给 CodeExplorer。"""
        file = str(args.get("file", ""))
        symbol_name = str(args.get("symbol_name", ""))
        if not file or not symbol_name:
            return "错误：缺少 file 或 symbol_name 参数"
        return self._explorer.read_symbol(file, symbol_name, self.cwd)

    def _tool_search(self, args: dict[str, object]) -> str:
        """在项目中搜索代码模式。"""
        if self._search_count >= self._search_budget:
            return f"已达到 search 调用上限（{self._search_budget} 次），请基于已有信息输出审查结果。"
        self._search_count += 1

        pattern = str(args.get("pattern", ""))
        if not pattern:
            return "错误：缺少 pattern 参数"

        search_path = str(args.get("path", "")) if args.get("path") else None

        try:
            cmd = ["git", "-C", str(self.cwd), "grep", "-n", "-I", "-E", pattern]
            if search_path:
                cmd.extend(["--", search_path])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            output = result.stdout.strip()
            if not output:
                return f"未找到匹配 '{pattern}' 的结果"
            # 截断到 50 条
            lines = output.split("\n")
            if len(lines) > 50:
                return "\n".join(lines[:50]) + f"\n...（共 {len(lines)} 条结果，已截断为 50 条）"
            return output
        except subprocess.TimeoutExpired:
            return "错误：搜索超时"
        except Exception as e:
            return f"错误：搜索失败 —— {e}"

    def _tool_list_dir(self, args: dict[str, object]) -> str:
        """列出目录结构。"""
        path_str = str(args.get("path", "")) if args.get("path") else ""
        target = self.cwd / path_str if path_str else self.cwd
        # 安全检查：防止路径穿越
        try:
            target = target.resolve()
            cwd_resolved = self.cwd.resolve()
            if not str(target).startswith(str(cwd_resolved) + "/") and target != cwd_resolved:
                return f"错误：路径 {path_str or '.'} 不在项目目录内"
        except Exception:
            return f"错误：无法解析路径 {path_str or '.'}"

        if not target.exists():
            return f"错误：目录 {path_str or '.'} 不存在"
        if not target.is_dir():
            return f"错误：{path_str} 不是目录"

        tree = self._format_tree(target, prefix="")
        if not tree.strip():
            return f"目录 {path_str or '.'} 为空或所有条目已被过滤"
        return tree

    def _format_tree(self, directory: Path, prefix: str, max_depth: int = 3, _depth: int = 0) -> str:
        """递归格式化目录树。"""
        if _depth >= max_depth:
            return ""

        parts: list[str] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return f"{prefix}[无权限访问]\n"

        for i, entry in enumerate(entries):
            if entry.name.startswith(".") and entry.name not in {".env.example"}:
                continue
            if entry.name in self.IGNORED_DIRS:
                continue

            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            parts.append(f"{prefix}{connector}{entry.name}")

            if entry.is_dir():
                sub_prefix = "    " if is_last else "│   "
                subtree = self._format_tree(entry, prefix + sub_prefix, max_depth, _depth + 1)
                if subtree:
                    parts.append(subtree)

        return "\n".join(parts)
