"""Bounded provider execution and attempt accounting."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Any

from openai import OpenAIError, APIConnectionError, APITimeoutError
from pydantic import BaseModel, ValidationError
from core.config import settings
from logic.debug_log import request_type_context
from logic.usage import UsageTotals, counter
from .errors import LLMResolutionError, LLMBackendUnavailableError, LLMOutputTruncatedError
from .request_kind import RequestKind

logger = logging.getLogger("logic.llm_manager")


async def execute(
    self,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    kind: str,
    repair_attempt: int = 0,
) -> Any:
    """Measure each attempt, including SDK-compatible transient retries."""
    kind = RequestKind(kind)
    count = await self.budget.input_tokens(messages, schema, kind)
    if not self.budget.fits(count, kind):
        raise LLMResolutionError(
            "Request exceeds the context budget; history and durable memory were preserved."
        )
    # A repair is already the next logical request for the same output. Restarting the
    # full transient-retry sequence for every repair would multiply provider calls
    # (max_retries + 1)^2. The initial request may use the configured retry budget; a
    # repair gets one provider attempt and either succeeds or advances to the next repair.
    attempt_limit = settings.llm.max_retries + 1 if repair_attempt == 0 else 1
    retry_backoff = sum(min(0.5 * 2**attempt, 8.0) for attempt in range(attempt_limit - 1))
    overall_timeout = settings.llm.request_timeout_seconds * attempt_limit + retry_backoff
    try:
        async with asyncio.timeout(overall_timeout):
            for attempt in range(attempt_limit):
                try:
                    result, response_text = await self._parse_attempt(
                        messages, schema, kind, count, attempt, repair_attempt
                    )
                    result._provider_response_text = response_text
                    return result
                except LLMResolutionError as exc:
                    cause = exc.__cause__
                    status = getattr(cause, "status_code", None)
                    transient = isinstance(cause, OpenAIError) and (
                        status in (408, 409, 429)
                        or (status is not None and status >= 500)
                        or type(cause).__name__ in ("APIConnectionError", "APITimeoutError")
                    )
                    if not transient or attempt == attempt_limit - 1:
                        raise
                    logger.info("Retrying LLM request kind=%s attempt=%d", kind, attempt + 2)
                    await asyncio.sleep(min(0.5 * 2**attempt, 8.0))
    except TimeoutError as exc:
        raise LLMResolutionError("The model request failed: deadline exceeded.") from exc


async def attempt(
    self,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    kind: str,
    count: int,
    attempt: int,
    repair_attempt: int = 0,
) -> tuple[BaseModel, str | None]:
    """Send and account for one provider attempt."""
    kind = RequestKind(kind)
    if self.client is None:
        self.client = self._create_client()
    started = perf_counter()
    response = None
    error = None
    cap_key = "max_tokens" if settings.llm.provider == "compatible" else "max_completion_tokens"
    effort = self.budget.reasoning_effort(kind)
    template_options = self.budget.template_options(kind)
    try:
        with request_type_context(kind):
            response = await self.client.beta.chat.completions.parse(
                model=settings.llm.model_name,
                messages=messages,
                response_format=schema,
                reasoning_effort=effort,
                **({"extra_body": template_options} if template_options else {}),
                **{cap_key: self.budget.request_output_limit(kind)},
            )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMOutputTruncatedError(
                "Model output reached its token limit; no result committed."
            )
        parsed = choice.message.parsed
        if parsed is None:
            raise LLMResolutionError("Model returned no validated result.")
        result = schema.model_validate(
            parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
        )
        response_text = getattr(choice.message, "content", None)
    except asyncio.CancelledError:
        error = "CancelledError"
        raise
    except LLMResolutionError:
        error = "LLMResolutionError"
        raise
    except (
        OpenAIError,
        ValidationError,
        IndexError,
        AttributeError,
        TypeError,
        ValueError,
        TimeoutError,
    ) as exc:
        # Provider exception bodies may contain private prompts. Keep them out of
        # public errors and logs; retain only the exception class for diagnosis.
        error = type(exc).__name__
        response = getattr(exc, "completion", response)
        logger.warning("LLM %s failed: %s", kind, error)
        if error == "LengthFinishReasonError":
            raise LLMOutputTruncatedError(
                "Model output reached its token limit; no result committed."
            ) from exc
        if isinstance(exc, APIConnectionError) and not isinstance(exc, APITimeoutError):
            raise LLMBackendUnavailableError(
                "Could not connect to the LLM backend; no result committed."
            ) from exc
        raise LLMResolutionError(
            "The model request failed or was truncated; no result committed."
        ) from exc
    finally:
        usage = getattr(response, "usage", None)
        timings = getattr(response, "timings", None)
        record = {
            "kind": kind,
            "round_number": self.usage_round,
            "retry": attempt > 0 or repair_attempt > 0,
            "repair_attempt": repair_attempt,
            "attempt": attempt + repair_attempt + 1,
            "estimated_input_tokens": count,
            "configured_output_tokens": self.budget.output_limit(kind),
            "thinking_output_tokens": self.budget.thinking_output_limit(kind),
            "request_output_tokens": self.budget.request_output_limit(kind),
            "counting_method": self.token_count_method,
            "input_tokens": counter(usage, "prompt_tokens"),
            "completion_tokens": counter(usage, "completion_tokens"),
            "total_tokens": counter(usage, "total_tokens"),
            "cached_tokens": counter(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens"
            ),
            "processed_prompt_tokens": counter(timings, "prompt_n"),
            "reused_prompt_tokens": counter(timings, "cache_n"),
            "latency_seconds": perf_counter() - started,
            "error": error,
        }
        self.last_request = record
        self.game_usage.add(record)
        if self.usage_round is not None:
            self.round_usage.add(record)
            self.round_usage_by_kind.setdefault(kind, UsageTotals()).add(record)
        logger.info("LLM usage %s", json.dumps(record, sort_keys=True))
    return result, response_text
