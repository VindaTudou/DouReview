import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from anthropic import Anthropic, APIStatusError, APITimeoutError, RateLimitError, AuthenticationError
from anthropic.types import Message as AnthropicMessage
from openai import OpenAI, APIStatusError as OAIStatusError, APITimeoutError as OAITimeoutError
from openai import RateLimitError as OAIRateLimitError
from openai import AuthenticationError as OAIAuthenticationError
from openai.types.chat import ChatCompletion as OpenAIChatCompletion

from .models import Prompt, ToolCall, ToolResult, ToolDefinition
from .tools import ToolRegistry
from .logger import VerboseLogger
from .errors import LLMError, LLMAuthError, LLMRateLimitError

# SDK 原始响应类型（Anthropic Message 或 OpenAI ChatCompletion）
LLMResponse = AnthropicMessage | OpenAIChatCompletion


class LLMClient:
    """
    LLM 调用客户端。支持 Anthropic 和 OpenAI 两种提供商。

    用法:
        # Anthropic
        client = LLMClient(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-5-20250914")
        # OpenAI
        client = LLMClient(provider="openai", api_key="sk-...", model="gpt-4o")
        # OpenAI 兼容接口（Ollama、DeepSeek 等）
        client = LLMClient(provider="openai", api_key="ollama", base_url="http://localhost:11434/v1", model="llama3")
    """

    def __init__(
        self,
        provider: str = "anthropic",
        api_key: str | None = None,
        model: str = "claude-sonnet-4-5-20250914",
        base_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_retries = max_retries
        self.timeout = timeout

        if provider == "anthropic":
            key = api_key
            if not key:
                raise LLMAuthError("Anthropic API Key 未提供。")
            self._client = Anthropic(api_key=key, timeout=timeout, max_retries=0)
        elif provider == "openai":
            key = api_key
            if not key:
                raise LLMAuthError("OpenAI API Key 未提供。")
            self._client = OpenAI(
                api_key=key,
                base_url=base_url or "https://api.openai.com/v1",
                timeout=timeout,
                max_retries=0,
            )
        else:
            raise ValueError(f"不支持的 provider: {provider}。可选值: anthropic, openai")

    def invoke(self, prompt: Prompt) -> str:
        """调用 LLM，返回完整响应文本。"""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return self._invoke_anthropic(prompt)
                else:
                    return self._invoke_openai(prompt)
            except Exception as e:
                last_error = e
                if not self._should_retry(e, attempt):
                    raise self._wrap_error(e)
                wait = 2 ** attempt
                time.sleep(wait)

        raise self._wrap_error(last_error)

    def stream(self, prompt: Prompt) -> Iterator[str]:
        """流式调用 LLM，逐个输出文本块。"""
        try:
            if self.provider == "anthropic":
                yield from self._stream_anthropic(prompt)
            else:
                yield from self._stream_openai(prompt)
        except Exception as e:
            raise self._wrap_error(e)

    def chat(
        self,
        prompt: Prompt,
        tools: list[ToolDefinition],
        tool_registry: ToolRegistry,
        max_turns: int = 40,
        on_chunk: Callable[[str], None] | None = None,
        logger: VerboseLogger | None = None,
    ) -> str:
        """
        Agent 循环 —— 多轮 tool-calling 对话，带分类预算和自适应退出。

        用法:
            response = client.chat(prompt, tools, tool_registry, on_chunk=lambda c: print(c, end=""))
        """

        # 初始化对话
        messages: list[dict] = []
        # system 消息
        if self.provider == "anthropic":
            # Anthropic 的 system 在顶层，不在 messages 里
            system_content = prompt.system
        else:
            messages.append({"role": "system", "content": prompt.system})
            system_content = ""

        # user 消息
        messages.append({"role": "user", "content": prompt.user})

        collected_texts: list[str] = []

        for _ in range(max_turns):
            body = self._build_chat_request_body(messages, tools, system_content)
            response = self._send_chat_request(body)
            text, tool_calls = self._parse_chat_response(response)

            if text:
                collected_texts.append(text)
                if on_chunk:
                    on_chunk(text)

            # 将 assistant 响应追加到对话历史
            if self.provider == "anthropic":
                assistant_content = []
                if text:
                    assistant_content.append({"type": "text", "text": text})
                for tc in tool_calls:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                messages.append({"role": "assistant", "content": assistant_content})
            else:
                # OpenAI
                assistant_msg = {"role": "assistant", "content": text}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_msg)

            if tool_calls:
                # 执行工具并追加结果
                tool_results = []
                for tc in tool_calls:
                    if logger:
                        logger.on_tool_call(tc)
                    result = tool_registry.execute(tc)
                    if logger:
                        logger.on_tool_result(result, tool_name=tc.name)
                    tool_results.append(result)
                messages.extend(self._build_tool_result_messages(tool_results))
            else:
                # 无工具调用 = LLM 已给出最终审查结果，退出循环
                break

        return "".join(collected_texts)

    # ── Chat / Agent 内部方法 ────────────────────────

    def _build_chat_request_body(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        system: str,
    ) -> dict[str, Any]:
        """按 provider 构建 tool-calling 请求体。"""
        if self.provider == "anthropic":
            return {
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": messages,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.parameters,
                    }
                    for t in tools
                ],
            }
        else:
            return {
                "model": self.model,
                "max_tokens": 4096,
                "messages": messages,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in tools
                ],
            }

    def _send_chat_request(self, body: dict[str, Any]) -> LLMResponse:
        """发送单轮 chat 请求，带重试。返回 Anthropic Message 或 OpenAI ChatCompletion。"""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return self._client.messages.create(**body)
                else:
                    return self._client.chat.completions.create(**body)
            except Exception as e:
                last_error = e
                if not self._should_retry(e, attempt):
                    raise self._wrap_error(e)
                wait = 2 ** attempt
                time.sleep(wait)

        raise self._wrap_error(last_error)

    def _parse_chat_response(self, response: LLMResponse) -> tuple[str | None, list[ToolCall]]:
        """
        解析 LLM 响应，提取文本和 tool_calls。

        Returns:
            (text_or_none, tool_calls_list)
        """
        if self.provider == "anthropic":
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    ))

            text = "".join(text_parts) if text_parts else None
            return text, tool_calls
        else:
            choice = response.choices[0]
            message = choice.message

            text = message.content if message.content else None

            tool_calls: list[ToolCall] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    ))

            return text, tool_calls

    def _build_tool_result_messages(self, results: list[ToolResult]) -> list[dict]:
        """将工具执行结果构建为追加到 messages 的消息列表。"""
        if self.provider == "anthropic":
            return [{
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_call_id,
                        "content": r.content,
                    }
                    for r in results
                ],
            }]
        else:
            # OpenAI: 每个 tool_result 是一条独立的 role="tool" 消息
            return [
                {
                    "role": "tool",
                    "tool_call_id": r.tool_call_id,
                    "content": r.content,
                }
                for r in results
            ]

    # ── Anthropic ────────────────────────────────

    def _invoke_anthropic(self, prompt: Prompt) -> str:
        msg = self._create_anthropic_message(prompt)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=msg["system"],
            messages=[{"role": "user", "content": msg["user"]}],
        )
        return response.content[0].text

    def _stream_anthropic(self, prompt: Prompt) -> Iterator[str]:
        msg = self._create_anthropic_message(prompt)
        with self._client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=msg["system"],
            messages=[{"role": "user", "content": msg["user"]}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    def _create_anthropic_message(self, prompt: Prompt) -> dict:
        return {"system": prompt.system, "user": prompt.user}

    # ── OpenAI ────────────────────────────────────

    def _invoke_openai(self, prompt: Prompt) -> str:
        msgs = self._create_openai_message(prompt)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=msgs,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""

    def _stream_openai(self, prompt: Prompt) -> Iterator[str]:
        msgs = self._create_openai_message(prompt)
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=msgs,
            max_tokens=4096,
            stream=True,
        )
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def _create_openai_message(self, prompt: Prompt) -> list[dict]:
        return [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ]

    # ── 错误处理 ──────────────────────────────────

    def _should_retry(self, error: Exception, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        if isinstance(error, (LLMAuthError, ValueError)):
            return False
        return True

    def _wrap_error(self, error: Exception | None) -> Exception:
        if error is None:
            return LLMError("LLM 调用失败，原因未知。")

        auth_classes = (AuthenticationError, OAIAuthenticationError)
        rate_classes = (RateLimitError, OAIRateLimitError)

        if isinstance(error, auth_classes):
            return LLMAuthError("API Key 无效，请检查 .env 中的配置。")
        if isinstance(error, rate_classes):
            return LLMRateLimitError("API 速率限制，请稍后重试。")
        if isinstance(error, (LLMAuthError, LLMRateLimitError, LLMError)):
            return error
        return LLMError(f"LLM 调用失败: {error}")
