"""Verbose 日志模块 —— Agent 工具调用过程的可视化输出。"""

import json
from collections.abc import Callable

from .models import ToolCall, ToolResult


# 各工具的预览行数上限
_PREVIEW_LINES: dict[str, int] = {
    "read_file": 30,     # 完整文件，显示更多
    "read_symbol": 20,   # 函数/类定义，适当显示
    "search": 15,        # grep 结果，多显示几条
    "list_dir": 50,      # 树状结构，尽量不截断
}


class VerboseLogger:
    """
    格式化并输出 Agent 的工具调用过程。

    用法:
        logger = VerboseLogger(emit=lambda msg: print(msg))
        logger.on_tool_call(tool_call)
        logger.on_tool_result(result)
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit

    @staticmethod
    def echo(emit: Callable[[str], None] | None) -> "VerboseLogger | None":
        """工厂方法：emit 为 None 时返回 None，否则创建 VerboseLogger。"""
        if emit is None:
            return None
        return VerboseLogger(emit)

    # ── 公开接口 ──

    def on_tool_call(self, call: ToolCall) -> None:
        """LLM 发起工具调用时输出。"""
        args = json.dumps(call.arguments, ensure_ascii=False)
        self._emit(f"🔧 {call.name}({args})")

    def on_tool_result(self, result: ToolResult, tool_name: str = "") -> None:
        """工具执行完成后输出结果预览。"""
        text = result.content
        max_lines = _PREVIEW_LINES.get(tool_name, 12)

        lines = text.split("\n")
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines]) + f"\n   ...（共 {len(lines)} 行，已截断）"
        elif len(text) > 2000:
            text = text[:2000] + "\n   ...（已截断）"

        preview = text.strip()
        if "\n" in preview:
            self._emit(f"   ↳ {preview.replace('\n', '\n     ')}")
        else:
            suffix = "..." if len(result.content) > 200 else ""
            self._emit(f"   ↳ {preview[:200]}{suffix}")
