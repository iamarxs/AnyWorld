"""OpenAI-compatible inference and bounded conversation context."""

import logging
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError
import tiktoken

from core.config import settings
from core.schemas import ContextSummary, DicePlan, RoundResolution
from logic.dice import describe_roll

logger = logging.getLogger(__name__)


class LLMResolutionError(RuntimeError):
    """Raised when the LLM cannot produce valid structured output."""


class LLMContextManager:
    """Keep genesis context and FIFO round pairs within the configured window."""

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self.client = client or self._create_client()
        # A configured value is the fallback; successful llama.cpp discovery takes precedence.
        self.context_window_size = settings.llm.context_window_size
        self._context_discovered = settings.llm.provider != "compatible"
        self.system_prompt = {"role": "system", "content": settings.llm.system_prompt}
        encoding_name = settings.llm.tokenizer_encoding
        try:
            self.encoding = tiktoken.get_encoding(encoding_name) if encoding_name else None
        except ValueError as exc:
            raise ValueError(f"Unknown tokenizer encoding: {encoding_name}") from exc
        self.system_prompt_tokens = self._count_tokens(settings.llm.system_prompt)
        self.genesis_state: dict[str, str] | None = None
        self.history: list[dict[str, str]] = []
        self.last_token_usage = self.system_prompt_tokens + 3

    @staticmethod
    def _create_client() -> AsyncOpenAI:
        """Create either a direct OpenAI client or a local compatible client."""
        client_options: dict[str, str] = {"api_key": settings.llm.api_key}
        if settings.llm.provider == "compatible":
            client_options["base_url"] = settings.llm.endpoint
        return AsyncOpenAI(**client_options)

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Set a new initial scenario and optional host guidance, then clear history."""
        logger.info("Setting genesis context (guidance=%s, scenario_chars=%d)", bool(guidance), len(scenario))
        content = f"Initial Scenario:\n{scenario}"
        if guidance:
            content += (
                "\n\nAdditional DM Guidance:\n"
                f"{guidance}\n"
                "Apply this guidance when it does not conflict with the system rules or "
                "required output schema."
            )
        self.genesis_state = {"role": "user", "content": content}
        self.history.clear()

    async def generate_initial_state(self) -> RoundResolution:
        logger.info("Generating initial scenario state")
        prompt = {
            "role": "user",
            "content": (
                "Create a concise scenario title and a vivid initial current-state paragraph "
                "with at least one immediate opportunity, tension, or story hook. Do not name, "
                "describe, count, or otherwise establish the player characters or party; their "
                "actual names are supplied only when the game starts. Set player_resolutions to "
                "an empty object."
            ),
        }
        return await self._request(prompt, RoundResolution, remember=True)

    async def generate_start_state(self, player_names: list[str]) -> RoundResolution:
        """Introduce the joined players in the scenario when play begins."""
        logger.info("Generating start state for %d players", len(player_names))
        names = ", ".join(player_names)
        prompt = {
            "role": "user",
            "content": (
                "The game is now starting. Rewrite the current "
                "world state to introduce exactly "
                "these player characters by their supplied names: "
                f"{names}. Give each a brief scenario-appropriate occupation, class, role, or "
                "other character description. Do not add, remove, or rename players, and do not "
                "resolve any player actions yet. Keep the established scenario and immediate "
                "story hook. Set player_resolutions to an empty object."
            ),
        }
        return await self._request(prompt, RoundResolution, remember=True)

    async def plan_dice(self, round_buffer: dict[str, str]) -> DicePlan:
        """Ask the DM which actions need uncertainty resolved by a d100."""
        logger.info("Planning dice for %d player actions", len(round_buffer))
        actions = "\n".join(f"{name}: {action}" for name, action in round_buffer.items())
        prompt = {
            "role": "user",
            "content": (
                "Decide whether each action needs a d100 check. Roll only when difficulty, "
                "danger, opposition, or uncertainty makes success unclear. Include every exact "
                "name in rolls. Put a name in hidden_rolls only when the check originates from "
                "private Additional DM Guidance; ordinary action checks are public.\n\nActions:\n"
                + actions
            ),
        }
        return await self._request(prompt, DicePlan, remember=False)

    async def generate_resolution(
        self, round_buffer: dict[str, str], dice_results: dict[str, int] | None = None
    ) -> RoundResolution:
        logger.info("Generating resolution for %d actions (dice_results=%d)", len(round_buffer), len(dice_results or {}))
        actions = "\n".join(
            f"{name} attempts to: {action}" for name, action in round_buffer.items()
        )
        roll_context = ""
        if dice_results:
            rendered = ", ".join(
                f"{name} rolled {value}/100 ({describe_roll(value)})"
                for name, value in dice_results.items()
            )
            roll_context = (
                "\n\nAuthoritative d100 results: " + rendered + ". Outcomes must honor "
                "these results; less than 11 is a catastrophic failure and above 90 a "
                "perfect success."
            )
        prompt = {
            "role": "user",
            "content": (
                "Resolve these actions as one simultaneous, causally coherent round. "
                "Reconcile interactions with the target's action and established facts. Move "
                "the story forward with concrete outcomes. Use each exact player name as its "
                "player_resolutions key, explicitly name that player, and emit no outcome for "
                "absent players.\n\nCurrent round actions:\n"
                f"{actions}{roll_context}"
            ),
        }
        return await self._request(prompt, RoundResolution, remember=True)

    async def _request(
        self, prompt: dict[str, str], schema: type[BaseModel], *, remember: bool
    ) -> Any:
        await self.discover_context_window()
        await self._compact_if_needed()
        logger.debug("Requesting %s with %d history messages", schema.__name__, len(self.history))
        messages = self._bounded_messages(prompt)
        try:
            response = await self.client.beta.chat.completions.parse(
                model=settings.llm.model_name,
                messages=messages,  # type: ignore[arg-type]
                response_format=schema,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise LLMResolutionError("LLM response did not contain parsed output")
            result = schema.model_validate(parsed)
        except LLMResolutionError:
            raise
        except (OpenAIError, ValidationError, IndexError, TypeError, ValueError) as exc:
            raise LLMResolutionError(f"LLM resolution failed: {exc}") from exc

        self.last_token_usage = self._context_size(messages)
        usage = getattr(response, "usage", None)
        if usage is not None and getattr(usage, "total_tokens", None) is not None:
            self.last_token_usage = int(usage.total_tokens)
            logger.info("LLM usage: prompt_tokens=%s completion_tokens=%s total_tokens=%s", getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None), usage.total_tokens)
        else:
            logger.info("LLM response has no usage statistics; estimated context tokens=%d", self.last_token_usage)
        if remember:
            self.history.extend(
                [prompt, {"role": "assistant", "content": result.model_dump_json()}]
            )
        return result

    async def _compact_if_needed(self) -> None:
        """Compact the oldest history when it reaches 90% of the input budget."""
        input_limit = self.context_window_size - min(2_048, self.context_window_size // 4)
        fixed = [self.system_prompt]
        if self.genesis_state is not None:
            fixed.append(self.genesis_state)
        if (
            len(self.history) < 4
            or self._context_size([*fixed, *self.history]) <= input_limit * 0.9
        ):
            return

        pair_count = max(1, (len(self.history) // 2) // 2)
        old_history = self.history[: pair_count * 2]
        logger.info("Compacting context: %d history messages (%d pairs)", len(self.history), pair_count)
        compact_prompt = {
            "role": "user",
            "content": (
                "Compact the following earlier RPG history into durable memory. Preserve world "
                "state, every player's current state, important NPCs, possessions, injuries, "
                "relationships, and unresolved story threads. Do not invent facts.\n\n"
                + "\n".join(item["content"] for item in old_history)
            ),
        }
        try:
            response = await self.client.beta.chat.completions.parse(
                model=settings.llm.model_name,
                messages=[*fixed, compact_prompt],  # type: ignore[arg-type]
                response_format=ContextSummary,
            )
            summary = response.choices[0].message.parsed
            if summary is None:
                return
            memory = {
                "role": "user",
                "content": "Historical memory:\n" + summary.model_dump_json(),
            }
            self.history = [
                memory,
                {"role": "assistant", "content": "Memory recorded."},
                *self.history[pair_count * 2 :],
            ]
            logger.info("Context compaction complete: %d history messages remain", len(self.history))
        except (OpenAIError, ValidationError, IndexError, TypeError, ValueError) as exc:
            # Normal FIFO trimming remains the safe fallback.
            logger.warning("Context compaction failed; using FIFO trimming: %s", exc)
            return

    async def discover_context_window(self) -> None:
        """Best-effort llama.cpp discovery, taking precedence over the configured fallback."""
        if self._context_discovered or settings.llm.provider != "compatible":
            return
        self._context_discovered = True
        try:
            base = settings.llm.endpoint.rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            async with httpx.AsyncClient(timeout=2.0) as http_client:
                response = await http_client.get(base + "/props")
                response.raise_for_status()
                props = response.json()
            discovered = props.get("default_generation_settings", {}).get("n_ctx")
            if discovered is None:
                discovered = props.get("n_ctx")
            if isinstance(discovered, int) and discovered >= 2_048:
                self.context_window_size = discovered
        except (httpx.HTTPError, ValueError, TypeError):
            # Keep the configured conservative fallback when discovery is unavailable.
            return

    def _bounded_messages(self, prompt: dict[str, str]) -> list[dict[str, str]]:
        fixed = [self.system_prompt]
        if self.genesis_state is not None:
            fixed.append(self.genesis_state)
        input_limit = self.context_window_size - min(2_048, self.context_window_size // 4)
        history = list(self.history)
        messages = [*fixed, *history, prompt]
        while history and self._context_size(messages) > input_limit:
            del history[:2]
            messages = [*fixed, *history, prompt]
        if self._context_size(messages) > input_limit:
            raise LLMResolutionError(
                "System prompt, scenario, and current actions exceed the context window"
            )
        self.history = history
        return messages

    def _count_tokens(self, content: str) -> int:
        """Count tokens via tiktoken, or approximate as one token per four characters."""
        if self.encoding is None:
            return max(1, len(content) // 4)
        return len(self.encoding.encode(content)) + 4

    def _context_size(self, messages: list[dict[str, str]]) -> int:
        return (
            self.system_prompt_tokens
            + 3
            + sum(
                self._count_tokens(message["content"])
                for message in messages
                if message is not self.system_prompt
            )
        )


llm_manager = LLMContextManager()
