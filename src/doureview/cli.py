from pathlib import Path

import typer

from .pipeline import Pipeline
from .diff_parser import DiffParser
from .prompt_builder import PromptBuilder
from .llm_client import LLMClient
from .report_generator import ReportGenerator
from .tools import ToolRegistry
from .logger import VerboseLogger
from .config import Config, Provider

app = typer.Typer()


@app.command()
def review(
    base: str = typer.Option(None, "--base", "-b", help="diff 范围的起点（如 main）"),
    head: str = typer.Option(None, "--head", "-H", help="diff 范围的终点（如 HEAD）"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示 LLM 工具调用过程"),
):
    """DouReview —— AI 代码审查工具"""
    Config.validate()

    # 根据 .env 中 DOUREVIEW_PROVIDER 自动选择 API Key 和提供商
    if Config.PROVIDER == Provider.ANTHROPIC:
        llm_client = LLMClient(
            provider="anthropic",
            api_key=Config.ANTHROPIC_API_KEY,
            model=Config.MODEL,
        )
    else:
        llm_client = LLMClient(
            provider="openai",
            api_key=Config.OPENAI_API_KEY,
            base_url=Config.OPENAI_BASE_URL,
            model=Config.MODEL,
        )

    pipeline = Pipeline(
        diff_parser=DiffParser(max_lines=Config.MAX_DIFF_LINES),
        prompt_builder=PromptBuilder(severity=Config.SEVERITY),
        llm_client=llm_client,
        report_generator=ReportGenerator(output_dir=Config.OUTPUT_DIR),
        tool_registry=ToolRegistry(cwd=Path.cwd()),  # V2: 启用 Agentic Review
    )

    try:
        if Config.QUIET:
            report_path = pipeline.run(base=base, head=head)
        else:
            if verbose:
                typer.echo("\n--- 审查中（verbose 模式）---\n")
            else:
                typer.echo("\n--- 审查中 ---\n")
            report_path = pipeline.run(
                base=base,
                head=head,
                on_chunk=lambda c: typer.echo(c, nl=False),
                logger=VerboseLogger.echo(lambda msg: typer.echo(f"  {msg}")) if verbose else None,
            )
            typer.echo("\n")
        typer.echo(f"\n✅ Review report: {report_path}")
    except Exception as e:
        typer.echo(f"\n❌ {e}", err=True)
        raise typer.Exit(code=1)


def main():
    app()
