from collections.abc import Callable
from pathlib import Path

from .diff_parser import DiffParser, DiffMode
from .prompt_builder import PromptBuilder
from .llm_client import LLMClient
from .report_generator import ReportGenerator
from .tools import ToolRegistry


class Pipeline:
    """
    编排审查流程。

    默认审查工作区所有改动（git diff HEAD），不分暂存/未暂存。

    用法:
        pipeline = Pipeline(
            diff_parser=DiffParser(),
            prompt_builder=PromptBuilder(),
            llm_client=LLMClient(),
            report_generator=ReportGenerator(),
        )
        # 默认：审查所有未提交的改动
        report_path = pipeline.run()
        # 指定范围
        report_path = pipeline.run(base="main", head="feature/xxx")
        # 流式输出到终端
        report_path = pipeline.run(on_chunk=lambda c: print(c, end="", flush=True))
    """

    def __init__(
        self,
        diff_parser: DiffParser,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        report_generator: ReportGenerator,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.diff_parser = diff_parser
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client
        self.report_generator = report_generator
        self.tool_registry = tool_registry

    def run(
        self,
        base: str | None = None,
        head: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        logger: "VerboseLogger | None" = None,
    ) -> Path:
        """
        执行完整的审查流程。

        默认模式（base 和 head 均为 None）：
          审查工作区所有未提交的改动
        指定范围模式：
          审查两个引用之间的改动

        Args:
            base: 可选，diff 范围的起点（如 "main"）
            head: 可选，diff 范围的终点（如 "HEAD"）
            on_chunk: 可选，流式回调。传入时使用流式调用，每收到一个文本块即回调；
                      不传时使用普通调用，直接返回完整响应。

        Returns:
            报告文件路径
        """
        # 1. 解析 diff
        if base is not None and head is not None:
            diff = self.diff_parser.parse(DiffMode.COMMITTED, base=base, head=head)
        else:
            # 默认：审查所有未提交的改动
            diff = self.diff_parser.parse(DiffMode.UNSTAGED)

        # 2. 构建 prompt
        prompt = self.prompt_builder.build(diff)

        # 3. 调用 LLM
        if self.tool_registry is not None:
            # V2: Agent 循环
            self.tool_registry.register_all()
            self.tool_registry.set_budgets(read_file=15, search=20)
            tools = self.tool_registry.definitions()
            response = self.llm_client.chat(prompt, tools, self.tool_registry, on_chunk=on_chunk, logger=logger)
        elif on_chunk is not None:
            # V1 fallback: 流式
            parts: list[str] = []
            for chunk in self.llm_client.stream(prompt):
                on_chunk(chunk)
                parts.append(chunk)
            response = "".join(parts)
        else:
            # V1 fallback: 阻塞
            response = self.llm_client.invoke(prompt)

        # 4. 生成报告
        report_path = self.report_generator.generate(response, diff)
        return report_path
