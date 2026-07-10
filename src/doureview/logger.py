"""Verbose 日志模块 —— Agent 工具调用过程的可视化输出。"""

import json
from collections.abc import Callable

from .models import ToolCall, ToolResult


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

    def on_tool_result(self, result: ToolResult) -> None:
        """工具执行完成后输出结果预览。"""
        text = result.content
        if len(text) > 400:
            lines = text.split("\n")[:8]
            text = "\n".join(lines) + "\n   ...（已截断）"

        preview = text.strip()
        if "\n" in preview:
            self._emit(f"   ↳ {preview.replace('\n', '\n     ')}")
        else:
            suffix = "..." if len(result.content) > 200 else ""
            self._emit(f"   ↳ {preview[:200]}{suffix}")
