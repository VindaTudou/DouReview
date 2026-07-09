import time
from typing import Iterator

from anthropic import Anthropic, APIStatusError, APITimeoutError, RateLimitError, AuthenticationError
from openai import OpenAI, APIStatusError as OAIStatusError, APITimeoutError as OAITimeoutError
from openai import RateLimitError as OAIRateLimitError
from openai import AuthenticationError as OAIAuthenticationError

from .models import Prompt
from .errors import LLMError, LLMAuthError, LLMRateLimitError


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
