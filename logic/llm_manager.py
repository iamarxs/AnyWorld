"""OpenAI-compatible inference and bounded conversation context."""

from typing import Any

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from core.config import settings
from core.schemas import RoundResolution


class LLMResolutionError(RuntimeError):
    """Raised when the LLM cannot produce a valid round resolution."""


class LLMContextManager:
    """Keep genesis context and FIFO round pairs within the configured window."""

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self.client = client or AsyncOpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.endpoint,
        )
        self.system_prompt = {"role": "system", "content": settings.llm.system_prompt}
        self.genesis_state: dict[str, str] | None = None
        self.history: list[dict[str, str]] = []

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Set a new initial scenario and optional host guidance, then clear history."""
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
        prompt = {
            "role": "user",
            "content": (
                "Create a concise scenario title and a vivid initial current-state paragraph "
                "with at least one immediate opportunity, tension, or story hook players can "
                "act on. Set player_resolutions to an empty object."
            ),
        }
        return await self._request(prompt, remember=True)

    async def generate_resolution(self, round_buffer: dict[str, str]) -> RoundResolution:
        actions = "\n".join(
            f"{name} attempts to: {action}" for name, action in round_buffer.items()
        )
        prompt = {
            "role": "user",
            "content": (
                "Resolve these actions as one simultaneous, causally coherent round. "
                "Reconcile actions that target another player with that player's own "
                "response; an attempted interaction succeeds only when circumstances and "
                "the target's action permit it. Move the story forward with concrete outcomes: "
                "when an action is plausible, let it discover, accomplish, or change something "
                "rather than merely describing the attempt. Introduce a relevant clue, choice, "
                "complication, NPC reaction, or world event when needed to preserve momentum. "
                "Use each exact player name as the matching player_resolutions key and explicitly "
                "name that player in their outcome. Do not emit outcomes for absent players.\n\n"
                f"Current round actions:\n{actions}"
            ),
        }
        return await self._request(prompt, remember=True)

    async def _request(self, prompt: dict[str, str], *, remember: bool) -> RoundResolution:
        messages = self._bounded_messages(prompt)
        try:
            response = await self.client.beta.chat.completions.parse(
                model=settings.llm.model_name,
                messages=messages,  # type: ignore[arg-type]
                response_format=RoundResolution,
            )
            parsed: Any = response.choices[0].message.parsed
            if parsed is None:
                raise LLMResolutionError("LLM response did not contain parsed output")
            resolution = RoundResolution.model_validate(parsed)
        except LLMResolutionError:
            raise
        except (OpenAIError, ValidationError, IndexError, TypeError, ValueError) as exc:
            raise LLMResolutionError(f"LLM resolution failed: {exc}") from exc

        if remember:
            self.history.extend(
                [prompt, {"role": "assistant", "content": resolution.model_dump_json()}]
            )
        return resolution

    def _bounded_messages(self, prompt: dict[str, str]) -> list[dict[str, str]]:
        """Evict oldest round pairs using UTF-8 bytes as a conservative token bound."""
        fixed = [self.system_prompt]
        if self.genesis_state is not None:
            fixed.append(self.genesis_state)

        output_reserve = min(2_048, settings.llm.context_window_size // 4)
        input_limit = settings.llm.context_window_size - output_reserve
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

    @staticmethod
    def _context_size(messages: list[dict[str, str]]) -> int:
        return sum(len(message["content"].encode("utf-8")) + 16 for message in messages)


llm_manager = LLMContextManager()
