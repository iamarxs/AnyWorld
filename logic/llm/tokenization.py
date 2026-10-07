"""Provider-aware token counting, context discovery and request budgets."""

import asyncio
import json
from collections import OrderedDict
from hashlib import sha256
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any

import httpx
import tiktoken
from pydantic import BaseModel

from core.config import settings
from .response_schemas import schema_text

# Both providers count hidden reasoning within the completion cap.
_THINKING_OUTPUT_BUDGETS = {"none": 0, "low": 2_048, "medium": 4_096, "high": 8_192}
_LOCAL_ONLY = ContextVar("local_token_estimates", default=False)
_CHANCE_OUTPUT_CAP = ContextVar("chance_output_cap", default=0)
_NO_THINKING_KINDS = frozenset(
    {
        "title",
        "dice",
        "chance_rule",
        "chance_trigger",
        "dice_audit",
        "event_audit",
        "summary_audit",
    }
)


class TokenBudget:
    """Own tokenizer state and bound every formatted inference request."""

    def __init__(self) -> None:
        """Initialize provider discovery state and bounded local count caches.

        Compatible providers are measured through their chat template and tokenizer
        when those endpoints respond.  Other paths use the conservative local
        estimate until a provider-specific tokenizer is known.
        """
        self._http: httpx.AsyncClient | None = None
        self.context_window_size = (
            settings.llm.openai_context_window_size
            if settings.llm.provider == "openai"
            and settings.llm.openai_context_window_size is not None
            else settings.llm.context_window_size
        )
        self.context_window_source = "configured fallback"
        self._context_discovered = settings.llm.provider != "compatible"
        self.encoding = None
        self._encoding_loaded = False
        self.token_count_method = "conservative UTF-8 estimate"
        self._text_counts: OrderedDict = OrderedDict()
        self._request_counts: OrderedDict = OrderedDict()
        self._template_identity = "undiscovered"
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._backend_retry_at = 0.0
        self._discovery_retry_at = 0.0
        self._cache_bytes = 0

    @contextmanager
    def local_estimates(self):
        token = _LOCAL_ONLY.set(True)
        try:
            yield
        finally:
            _LOCAL_ONLY.reset(token)

    def output_limit(self, kind: str) -> int:
        """Return the configured output token cap for a request kind."""
        if kind == "title":
            return min(128, settings.llm.initial_output_tokens)
        if kind == "chance_trigger":
            return max(settings.llm.dice_output_tokens, _CHANCE_OUTPUT_CAP.get())
        cap_kind = (
            "summary"
            if kind in {"summary_audit", "event_audit", "dice_audit"}
            else "dice" if kind in {"chance_rule", "chance_trigger"} else kind
        )
        return getattr(settings.llm, f"{cap_kind}_output_tokens")

    @contextmanager
    def chance_participants(self, names):
        """Reserve the focused reply for the actual party, in this task only."""
        reply = json.dumps({"occurrences": list(names)}, ensure_ascii=True)
        token = _CHANCE_OUTPUT_CAP.set(self.count_tokens(reply) + 64)
        try:
            yield
        finally:
            _CHANCE_OUTPUT_CAP.reset(token)

    @staticmethod
    def reasoning_effort(kind: str = "round") -> str:
        """Disable hidden reasoning for short metadata and audit requests."""
        return "none" if kind in _NO_THINKING_KINDS else settings.llm.reasoning_effort

    @classmethod
    def thinking_output_limit(cls, kind: str = "round") -> int:
        """Reserve completion tokens for hidden reasoning on both providers."""
        return _THINKING_OUTPUT_BUDGETS[cls.reasoning_effort(kind)]

    def request_output_limit(self, kind: str) -> int:
        """Return the backend cap, including visible output and hidden reasoning."""
        return self.output_limit(kind) + self.thinking_output_limit(kind)

    def fits(self, count: int, kind: str) -> bool:
        """Return whether a token count fits within the context budget."""
        return count + self.request_output_limit(kind) + settings.llm.token_safety_margin <= (
            self.context_window_size
        )

    async def _http_client(self) -> httpx.AsyncClient:
        """Return a lazily-created HTTP client for backend discovery."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=2.0, headers={"Authorization": f"Bearer {settings.llm.api_key}"}
            )
        return self._http

    def _backend_base(self) -> str:
        """Return the backend base URL without a trailing /v1."""
        base = settings.llm.endpoint.rstrip("/")
        return base[:-3] if base.endswith("/v1") else base

    @staticmethod
    def template_options(kind: str = "round") -> dict[str, Any]:
        """Use the same explicit llama.cpp template options for counting and generation."""
        if settings.llm.provider == "compatible":
            effort = TokenBudget.reasoning_effort(kind)
            return {
                "chat_template_kwargs": {
                    "enable_thinking": effort != "none",
                    "reasoning_effort": effort,
                }
            }
        return {}

    async def input_tokens(
        self, messages: list[dict[str, str]], schema: type[BaseModel] | None, kind: str = "round"
    ) -> int:
        """Reuse formatted message counts across schemas with the same tokenizer identity."""
        if _LOCAL_ONLY.get():
            self.token_count_method = "conservative UTF-8 estimate"
            count = self.context_size(messages)
            return count + (self.count_tokens(schema_text(schema)) + 64 if schema else 0)
        template_options = self.template_options(kind)
        template_identity = tuple(
            (
                name,
                tuple(sorted(value.items())) if isinstance(value, dict) else value,
            )
            for name, value in sorted(template_options.items())
        )
        message_identity = tuple((message["role"], message["content"]) for message in messages)
        key = (
            settings.llm.provider,
            settings.llm.endpoint,
            settings.llm.model_name,
            settings.llm.tokenizer_encoding,
            settings.llm.openai_tokenizer_encoding,
            self._template_identity,
            template_identity,
            message_identity,
        )
        if key in self._request_counts:
            count, method = self._request_counts[key]
            self._request_counts.move_to_end(key)
        else:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._measure(messages, kind))
                self._inflight[key] = task
            try:
                count, method = await asyncio.shield(task)
            finally:
                if task.done():
                    self._inflight.pop(key, None)
            # Retry unavailable tokenization rather than pinning a backend failure.
            if method != "conservative UTF-8 estimate" and key not in self._request_counts:
                self._request_counts[key] = (count, method)
                self._cache_bytes += sum(
                    len(content.encode("utf-8")) for _, content in message_identity
                )
                while len(self._request_counts) > 128 or self._cache_bytes > 4 * 1024 * 1024:
                    evicted, _ = self._request_counts.popitem(last=False)
                    self._cache_bytes -= sum(
                        len(content.encode("utf-8")) for _, content in evicted[-1]
                    )
        self.token_count_method = method
        if schema is not None:
            count += self.count_tokens(schema_text(schema)) + 64
            if method == "backend template/tokenizer":
                self.token_count_method += " + schema allowance"
        return count

    async def _measure(self, messages, kind):
        try:
            async with asyncio.timeout(5):
                count = await self._uncached_message_tokens(messages, kind)
                return count, self.token_count_method
        except TimeoutError:
            return self.context_size(messages), "conservative UTF-8 estimate"

    async def _uncached_message_tokens(
        self, messages: list[dict[str, str]], kind: str = "round"
    ) -> int:
        """Count formatted messages without a response-schema allowance."""
        if settings.llm.provider == "openai" and not self._encoding_loaded:
            self._encoding_loaded = True
            try:
                known_encoding = tiktoken.encoding_name_for_model(settings.llm.model_name)
                configured = settings.llm.openai_tokenizer_encoding
                if configured is None:
                    configured = settings.llm.tokenizer_encoding
                if configured in ("auto", known_encoding):
                    self.encoding = await asyncio.to_thread(tiktoken.get_encoding, known_encoding)
                    self.token_count_method = "model tokenizer + estimated framing/schema allowance"
            except (KeyError, ValueError, OSError):
                # Unknown/mismatched encodings retain the conservative byte estimate.
                self.encoding = None
        if settings.llm.provider == "compatible" and not _LOCAL_ONLY.get():
            if monotonic() < self._backend_retry_at:
                self.token_count_method = "conservative UTF-8 estimate"
                return self.context_size(messages)
            try:
                http = await self._http_client()
                rendered = await http.post(
                    self._backend_base() + "/apply-template",
                    json={"messages": messages, **self.template_options(kind)},
                )
                rendered.raise_for_status()
                prompt = rendered.json()["prompt"]
                if not isinstance(prompt, str):
                    raise ValueError("Invalid chat template response")
                tokens = await http.post(
                    self._backend_base() + "/tokenize",
                    json={"content": prompt, "add_special": True, "parse_special": True},
                )
                tokens.raise_for_status()
                token_ids = tokens.json()["tokens"]
                if not isinstance(token_ids, list):
                    raise ValueError("Invalid tokenizer response")
                self.token_count_method = "backend template/tokenizer"
                return len(token_ids)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.token_count_method = "conservative UTF-8 estimate"
                self._backend_retry_at = monotonic() + 1.0
        return self.context_size(messages)

    async def discover_context_window(self) -> None:
        """Discover the backend context window size when available."""
        if (
            self._context_discovered
            or settings.llm.provider != "compatible"
            or _LOCAL_ONLY.get()
            or monotonic() < self._discovery_retry_at
        ):
            return
        try:
            http = await self._http_client()
            response = await http.get(self._backend_base() + "/props")
            response.raise_for_status()
            props = response.json()
            self._template_identity = sha256(
                json.dumps(
                    {
                        name: props.get(name)
                        for name in ("chat_template", "model_path", "build_info")
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            # Only use the generation slot's context, not an ambiguous global n_ctx.
            discovered = props.get("default_generation_settings", {}).get("n_ctx")
            if (
                isinstance(discovered, int)
                and not isinstance(discovered, bool)
                and discovered >= 2048
            ):
                self.context_window_size = discovered
                self.context_window_source = "llama.cpp /props per-slot n_ctx"
                self._context_discovered = True
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            self._discovery_retry_at = monotonic() + 1.0

    def count_tokens(self, content: str) -> int:
        """Count tokens in a string using the encoding or a byte estimate."""
        encoded = content.encode("utf-8")
        key = (self.encoding, sha256(encoded).digest())
        if key in self._text_counts:
            self._text_counts.move_to_end(key)
            return self._text_counts[key]
        count = (
            len(self.encoding.encode(content, disallowed_special=()))
            if self.encoding is not None
            else len(encoded)
        )
        self._text_counts[key] = count
        if len(self._text_counts) > 512:
            self._text_counts.popitem(last=False)
        return count

    def context_size(self, messages: list[dict[str, str]]) -> int:
        """Estimate the total token size of a message list."""
        return 32 + sum(self.count_tokens(item["content"]) + 32 for item in messages)

    def clear_caches(self) -> None:
        """Discard retained counts when starting a new game."""
        self._text_counts.clear()
        self._request_counts.clear()
        self._cache_bytes = 0

    async def close(self) -> None:
        """Close the lazily-created discovery/tokenizer HTTP client."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        if self._http is not None:
            await self._http.aclose()
            self._http = None
