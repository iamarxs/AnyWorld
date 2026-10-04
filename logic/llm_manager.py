"""OpenAI-compatible inference and bounded conversation context."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Any

from openai import (
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
    OpenAIError,
)
from pydantic import BaseModel, ValidationError

from core.config import settings
from core.schemas import (
    ChanceEventResult,
    ChanceRuleDecision,
    ChanceRuleInterpretation,
    ChanceTriggerPlan,
    DicePlan,
    RoundResolution,
    ScenarioTitle,
)
from logic.usage import UsageTotals
from logic.dice import (
    chance_events_from_decision,
    conditional_chance_rule,
    has_non_percentage_private_guidance,
    non_percentage_guidance_lines,
)
from logic.presentation import name_resolution
from logic.debug_log import RawResponseLogger, set_debug_round_number
from logic.llm import auditing, prompts, provider, compaction
from logic.llm.errors import (
    LLMBackendUnavailableError,
    LLMOutputTruncatedError,
    LLMResolutionError,
)
from logic.llm.response_schemas import participant_schema
from logic.llm.request_kind import RequestKind
from logic.llm.tokenization import TokenBudget
from logic.llm.validation import check_semantics, check_public_output, normalize_hidden_roll_sources
from logic.models import PreparedResolution

# Keep the existing public imports available to callers.
__all__ = [
    "LLMContextManager",
    "LLMResolutionError",
    "LLMBackendUnavailableError",
    "participant_schema",
]

logger = logging.getLogger(__name__)


class LLMContextManager:
    """Resolve one game's narrative while preserving bounded, private context.

    The manager keeps immutable genesis, optional durable memory, and recent
    compact input/output pairs.  ``_request`` is the single path for provider
    calls, schema validation, privacy checks, usage accounting, and history
    commits; compaction is transactional so failed summaries leave the original
    context intact.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        """Initialize the manager with an optional client and configured context."""
        self.client = (
            client.with_options(max_retries=0) if isinstance(client, AsyncOpenAI) else client
        )
        self._debug_logger: RawResponseLogger | None = None
        self.budget = TokenBudget()
        self._retained_measurement = None
        self.system_prompt = {"role": "system", "content": settings.llm.system_prompt}
        self.genesis_state: dict[str, str] | None = None
        self.history: list[dict[str, str]] = []
        self.memory: dict[str, str] | None = None
        self.private_guidance = ""
        self.chance_rule_interpretation: ChanceRuleInterpretation | None = None
        self._chance_rule_prepared = False
        self._known_player_names: list[str] = []
        self.game_usage = UsageTotals()
        self.round_usage = UsageTotals()
        self.usage_round: int | None = None
        self.last_request: dict[str, Any] | None = None
        self.round_usage_by_kind: dict[str, UsageTotals] = {}
        self.round_work_seconds = 0.0
        self.round_failures = 0
        self.last_round_error: str | None = None
        self._round_started: float | None = None
        self._memory_revision = 0

    def _create_client(self) -> AsyncOpenAI:
        """Create either a direct OpenAI client or a local compatible client."""
        client_options: dict[str, Any] = {
            "api_key": settings.llm.api_key,
            "timeout": settings.llm.request_timeout_seconds,
            "max_retries": 0,  # Each retry is measured explicitly below.
        }
        if settings.llm.provider == "compatible":
            client_options["base_url"] = settings.llm.endpoint
        if settings.llm.debug_raw_responses:
            raw_logger = RawResponseLogger()
            self._debug_logger = raw_logger
            client_options["http_client"] = DefaultAsyncHttpxClient(
                event_hooks={
                    "request": [raw_logger.capture_request],
                    "response": [raw_logger.capture],
                }
            )
        client = AsyncOpenAI(**client_options)
        # Passing None to the constructor would still inherit OPENAI_ORG_ID and
        # OPENAI_PROJECT_ID. Scope this client's requests to its configured API key.
        client.organization = None
        client.project = None
        return client

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Set a new initial scenario and optional host guidance, then clear history."""
        logger.info(
            "Setting genesis context (guidance=%s, scenario_chars=%d)",
            bool(guidance),
            len(scenario),
        )
        content = f"Initial Scenario:\n{scenario}"
        if guidance:
            content += (
                "\n\nAdditional DM Guidance:\n"
                f"{guidance}\n"
                "Follow compatible steering throughout play without quoting it. Keep the "
                "guidance and secret check triggers or values private; narrate only observable "
                "in-world consequences."
            )
        self.genesis_state = {"role": "user", "content": content}
        self.history.clear()
        self._memory_revision += 1
        self._retained_measurement = None
        self.budget.clear_caches()
        self.memory = None
        self.private_guidance = guidance
        self.chance_rule_interpretation = None
        self._chance_rule_prepared = False
        self._known_player_names = []
        self.game_usage = UsageTotals()
        self.round_usage = UsageTotals()
        self.usage_round = None
        self.last_request = None
        self.round_usage_by_kind = {}
        self.round_work_seconds = 0.0
        self.round_failures = 0
        self.last_round_error = None
        self._round_started = None
        set_debug_round_number(None)

    async def generate_scenario_title(self) -> str:
        """Generate only a title, without creating or remembering narrative."""
        logger.info("Generating scenario title")
        prompt = prompts.title_prompt()
        result = await self._request(
            prompt,
            ScenarioTitle,
            remember=False,
            include_history=False,
            kind="title",
        )
        return result.title.strip()

    async def generate_start_state(self, player_names: list[str]) -> RoundResolution:
        """Introduce the joined players in the scenario when play begins."""
        logger.info("Generating start state for %d players", len(player_names))
        prompt = prompts.start_state_prompt(player_names)
        return await self._request(
            prompt,
            participant_schema(RoundResolution, (), provider=settings.llm.provider),
            remember=True,
            kind="initial",
            expected_names=(),
            opening_names=tuple(player_names),
            history_prompt=prompts.opening_memory_prompt(player_names),
        )

    async def _stage(self, generate) -> PreparedResolution:
        """Compute conversation changes privately; cancellation restores the checkpoint."""
        memory, history, names = self.memory, list(self.history), list(self._known_player_names)
        revision = self._memory_revision
        try:
            result = await generate()
            return PreparedResolution(
                result, self.memory, tuple(self.history), tuple(self._known_player_names), revision
            )
        finally:
            self.memory, self.history, self._known_player_names = memory, history, names

    async def stage_start_state(self, player_names: list[str]) -> PreparedResolution:
        return await self._stage(lambda: self.generate_start_state(player_names))

    async def stage_resolution(
        self,
        actions: dict[str, str],
        dice_results: dict[str, int],
        hidden_rolls: set[str],
        chance_events: list[ChanceEventResult] | None = None,
    ) -> PreparedResolution:
        return await self._stage(
            lambda: self.generate_resolution(
                actions,
                dice_results,
                hidden_rolls,
                **({"chance_events": chance_events} if chance_events else {}),
            )
        )

    def commit_resolution(self, prepared: PreparedResolution) -> None:
        if prepared.revision != self._memory_revision:
            raise RuntimeError("Stale conversation checkpoint")
        self.memory = prepared.memory
        self.history = list(prepared.history)
        self._known_player_names = list(prepared.names)
        self._memory_revision += 1
        self._retained_measurement = None

    async def prepare_chance_rule(self) -> None:
        """Normalize the one conditional chance instruction once before players act."""
        if self._chance_rule_prepared:
            return
        rule = conditional_chance_rule(self.private_guidance)
        if rule is not None:
            interpretation = await self._request(
                prompts.chance_rule_prompt(rule[0]),
                ChanceRuleInterpretation,
                remember=False,
                include_history=False,
                kind="chance_rule",
            )
            self.chance_rule_interpretation = interpretation
        self._chance_rule_prepared = True

    def configure_chance_rule(self, rule) -> None:
        if rule is not None:
            self.chance_rule_interpretation = ChanceRuleInterpretation(
                trigger_type="world_transition",
                trigger_description=rule.trigger or "Once this round",
                occurrence_scope=rule.scope,
                effect=rule.effect,
                eligibility=rule.eligibility,
                cadence=rule.cadence,
                structured=True,
            )
            self._chance_rule_prepared = True

    async def _plan_chance_triggers(
        self, round_buffer: dict[str, str], current_state: str
    ) -> ChanceRuleDecision | None:
        """Run the focused structured trigger pass for the normalized conditional rule."""
        interpretation = self.chance_rule_interpretation
        if interpretation is None:
            return None
        if interpretation.cadence == "per_round" and not interpretation.eligibility:
            return ChanceRuleDecision(
                trigger="per_round",
                reason="Once for each eligible scope this round.",
                occurrences=(
                    list(round_buffer)
                    if interpretation.occurrence_scope == "per_player"
                    else ["shared"]
                ),
            )
        prompt, schema = self._build_round_request(
            RequestKind.CHANCE_TRIGGER, round_buffer, current_state
        )
        with self.budget.chance_participants(round_buffer):
            result = await self._request(
                prompt,
                schema,
                remember=False,
                kind="chance_trigger",
            )
        names = {name.casefold(): name for name in round_buffer}
        matched = []
        for value in result.occurrences:
            if interpretation.occurrence_scope == "shared":
                if value.strip().casefold() == "shared" or value.strip().casefold() in names:
                    matched = ["shared"]
                    break
            else:
                name = names.get(value.strip().casefold())
                if name is not None and name not in matched:
                    matched.append(name)
        return ChanceRuleDecision(
            trigger="condition",
            occurrences=matched,
            reason=(
                "The structured trigger pass found a current occurrence."
                if matched
                else "The structured trigger pass found no current occurrence."
            ),
        )

    async def plan_dice(self, round_buffer: dict[str, str], current_state: str = "") -> DicePlan:
        """Ask the DM which actions need uncertainty resolved by a d100.

        The public paragraph is not a complete state snapshot. Include genesis, private
        guidance, durable memory and recent rounds so unchanged facts still affect checks.
        """
        logger.info("Planning dice for %d player actions", len(round_buffer))
        await self.prepare_chance_rule()
        chance_decision = await self._plan_chance_triggers(round_buffer, current_state)
        prompt, schema = self._build_round_request(RequestKind.DICE, round_buffer, current_state)
        return await self._request(
            prompt,
            schema,
            remember=False,
            kind="dice",
            expected_names=tuple(round_buffer),
            planning_input={"actions": round_buffer, "current_state": current_state},
            chance_decision=chance_decision,
        )

    async def generate_resolution(
        self,
        round_buffer: dict[str, str],
        dice_results: dict[str, int] | None = None,
        hidden_rolls: set[str] | None = None,
        chance_events: list[ChanceEventResult] | None = None,
    ) -> RoundResolution:
        """Resolve a round of actions into a coherent narrative outcome."""
        logger.info(
            "Generating resolution for %d actions (dice_results=%d)",
            len(round_buffer),
            len(dice_results or {}),
        )
        prompt, schema = self._build_round_request(
            RequestKind.ROUND,
            round_buffer,
            dice_results=dice_results,
            hidden_rolls=hidden_rolls,
            chance_events=chance_events,
        )
        return await self._request(
            prompt,
            schema,
            remember=True,
            expected_names=tuple(round_buffer),
            private_rolls={
                name: value
                for name, value in (dice_results or {}).items()
                if name in (hidden_rolls or set())
            },
            private_events=chance_events,
            public_rolls={
                name: value
                for name, value in (dice_results or {}).items()
                if name not in (hidden_rolls or set())
            },
            history_prompt=prompts.round_memory_prompt(
                round_buffer, self.usage_round, dice_results, hidden_rolls, chance_events
            ),
        )

    def _build_round_request(
        self,
        kind: RequestKind,
        actions: dict[str, str],
        current_state: str = "",
        *,
        dice_results: dict[str, int] | None = None,
        hidden_rolls: set[str] | None = None,
        chance_events: list[ChanceEventResult] | None = None,
    ) -> tuple[dict[str, str], type[BaseModel]]:
        """Build the same prompt/schema for admission and actual round calls."""
        if kind == RequestKind.ROUND:
            return prompts.resolution_prompt(
                actions, dice_results, hidden_rolls, chance_events, self.private_guidance
            ), participant_schema(RoundResolution, tuple(actions), provider=settings.llm.provider)
        if kind == RequestKind.DICE:
            return prompts.dice_prompt(actions, current_state), participant_schema(
                DicePlan,
                tuple(actions),
                has_non_percentage_private_guidance(self.private_guidance),
                settings.llm.provider,
                private_sources=non_percentage_guidance_lines(self.private_guidance),
            )
        if kind == RequestKind.CHANCE_TRIGGER and self.chance_rule_interpretation is not None:
            return (
                prompts.chance_trigger_prompt(
                    actions, current_state, self.chance_rule_interpretation
                ),
                ChanceTriggerPlan,
            )
        raise ValueError(f"Unsupported round request kind: {kind}")

    def _fixed_messages(self, kind: str = "round") -> list[dict[str, str]]:
        """Return the stable system/genesis/memory prefix for a request kind.

        Dice planning uses its own conservative system policy.  Conditional
        trigger detection also receives a version of genesis without the raw
        guidance, because the normalized rule is supplied separately.
        """
        system = self.system_prompt
        if kind == "dice":
            system = {"role": "system", "content": prompts.DICE_PLANNER_SYSTEM_PROMPT}
        genesis = self.genesis_state
        if kind == "chance_trigger" and genesis is not None:
            scenario = genesis["content"].split("\n\nAdditional DM Guidance:", 1)[0]
            genesis = {"role": "user", "content": scenario}
        return [
            system,
            *([genesis] if genesis else []),
            *([self.memory] if self.memory else []),
        ]

    async def preflight_round(self, actions: dict[str, str], current_state: str = "") -> None:
        """Bound aggregate admission work, then use labelled local estimates."""
        try:
            async with asyncio.timeout(5):
                await self._preflight_round(actions, current_state)
        except TimeoutError:
            with self.budget.local_estimates():
                await self._preflight_round(actions, current_state)

    async def _preflight_round(self, actions: dict[str, str], current_state: str = "") -> None:
        """Reject impossible fixed context/actions before accepting the next action.

        Recent history is compactable; durable memory is not silently expendable.  This
        check uses the same prompt builders and response schemas as the planner and the
        resolution request.  The final resolution prompt is checked again by ``_parse``
        after generated dice and chance-event context are available.
        """
        await self.prepare_chance_rule()
        await self.budget.discover_context_window()
        kinds = [RequestKind.ROUND, RequestKind.DICE]
        if self.chance_rule_interpretation is not None:
            kinds.insert(1, RequestKind.CHANCE_TRIGGER)
        for kind in kinds:
            prompt, schema = self._build_round_request(kind, actions, current_state)
            prompt = prompts.prepare_request_prompt(
                prompt, issubclass(schema, RoundResolution), self.private_guidance
            )
            with self.budget.chance_participants(actions):
                count = await self.budget.input_tokens(
                    [*self._fixed_messages(kind), prompt], schema, kind
                )
                if not self.budget.fits(count, kind):
                    raise LLMResolutionError(
                        "Scenario, durable memory and combined actions exceed the context budget. "
                        "Shorten the action or use a larger backend context."
                    )

    async def _request(
        self,
        prompt: dict[str, str],
        schema: type[BaseModel],
        *,
        remember: bool,
        include_history: bool = True,
        kind: str = "round",
        private_rolls: dict[str, int] | None = None,
        public_rolls: dict[str, int] | None = None,
        private_events: list[ChanceEventResult] | None = None,
        chance_decision: ChanceRuleDecision | None = None,
        planning_input: dict[str, Any] | None = None,
        expected_names: tuple[str, ...] | None = None,
        opening_names: tuple[str, ...] | None = None,
        history_prompt: dict[str, str] | None = None,
    ) -> Any:
        """Run one bounded request and commit only validated, privacy-safe output.

        The method may compact history before parsing, retries schema failures with
        repair instructions, performs chance/hidden-check audits, and stores the
        supplied compact input record only after all checks pass.
        """
        kind = RequestKind(kind)
        prompt = prompts.prepare_request_prompt(
            prompt, issubclass(schema, RoundResolution), self.private_guidance
        )
        await self.budget.discover_context_window()
        if include_history:
            await self._compact_if_needed(prompt, schema, kind)
        messages = [*self._fixed_messages(kind), *(self.history if include_history else []), prompt]
        for repair in range(settings.llm.max_retries + 1):
            result = None
            try:
                result = await self._parse(messages, schema, kind, repair_attempt=repair)
                response_text = getattr(result, "_provider_response_text", None)
                if isinstance(result, DicePlan):
                    # A stray privacy label cannot create a roll the planner declined.
                    # Reject unknown names before filtering declined checks.
                    if not set(result.hidden_rolls) <= set(result.rolls):
                        raise LLMResolutionError("Invalid hidden dice membership.")
                    result = result.model_copy(
                        update={
                            "hidden_rolls": [
                                name
                                for name in result.hidden_rolls
                                if result.rolls.get(name) is True
                            ]
                        }
                    )
                check_semantics(result, expected_names)
                if isinstance(result, DicePlan):
                    try:
                        result = result.model_copy(
                            update={
                                "chance_events": chance_events_from_decision(
                                    chance_decision,
                                    self.private_guidance,
                                    self.chance_rule_interpretation,
                                    expected_names,
                                ),
                            }
                        )
                    except ValueError as exc:
                        raise LLMResolutionError(str(exc)) from exc
                    result = normalize_hidden_roll_sources(result, self.private_guidance)
                    result = await auditing.classify_hidden_checks(
                        self._parse, result, planning_input or {}, repair_attempt=repair
                    )
                    result = self._revalidate_result(result, DicePlan)
                    check_semantics(result, expected_names)
                if isinstance(result, ScenarioTitle):
                    public_title = RoundResolution(
                        global_narrative=result.title, player_resolutions={}
                    )
                    check_semantics(public_title, ())
                    check_public_output(public_title, {}, self.private_guidance)
                if isinstance(result, RoundResolution):
                    result = self._normalize_narrative(result, schema)
                    check_semantics(result, expected_names)
                    checks = dict(private_rolls or {})
                    checks.update(
                        {f"event-{i}": event.roll for i, event in enumerate(private_events or [])}
                    )
                    check_public_output(
                        result, checks, self.private_guidance, public_rolls, private_events
                    )
                    if opening_names is not None and any(
                        name.casefold() not in result.global_narrative.casefold()
                        for name in opening_names
                    ):
                        raise LLMResolutionError(
                            "Opening narrative must introduce every player by their supplied "
                            "name with an occupation, class, or role: " + json.dumps(opening_names)
                        )
                    if any(event.occurred for event in (private_events or [])):
                        await auditing.audit_chance_outcomes(
                            self._parse, result, private_events, repair_attempt=repair
                        )
                break
            except LLMResolutionError as exc:
                # Transport failures have already exhausted their provider retry
                # budget. A semantic repair cannot fix an unavailable provider.
                if isinstance(exc.__cause__, OpenAIError):
                    raise
                logger.warning(
                    "LLM %s output validation failed (repair=%d): %s", kind, repair, str(exc)
                )
                if repair == settings.llm.max_retries:
                    raise
                if kind == "dice" and isinstance(exc, LLMOutputTruncatedError):
                    repair_instruction = (
                        "The previous dice response reached its output limit before returning "
                        "complete JSON. Reasoning is disabled for this request. Make a direct, "
                        "minimal classification and return only compact JSON; omit explanations. "
                        "Include every required player and chance-rule key. All-false action rolls "
                        "are valid, and hidden_rolls may list only names with a true roll."
                    )
                else:
                    repair_instruction = (
                        "Correct the output contract: return only the requested object. "
                        "Use exactly the required schema keys and player names. Include nonempty "
                        "narrative/outcomes where requested. Hidden checks must be unique, "
                        "required rolls from private guidance. Never disclose private guidance "
                        "or hidden values. Do not change the supplied actions, dice or facts. "
                        "When fixing hidden_roll_sources or hidden_rolls, preserve rolls: "
                        "making a check public must not remove the action's required d100."
                    )
                messages = [
                    *messages,
                    *(
                        [{"role": "assistant", "content": result.model_dump_json()}]
                        if isinstance(result, DicePlan)
                        else []
                    ),
                    {
                        "role": "user",
                        "content": (
                            repair_instruction
                            + " Validation issue: "
                            + str(exc)
                            + " Required player keys: "
                            + json.dumps(expected_names)
                        ),
                    },
                ]
        if remember:
            names = opening_names if opening_names is not None else expected_names
            self._commit_history(
                result, schema, history_prompt or prompt, response_text, names or ()
            )
        return result

    @staticmethod
    def _revalidate_result(result: BaseModel, schema: type[BaseModel]) -> BaseModel:
        """Check model_copy updates with the repairable output contract."""
        try:
            return schema.model_validate(result.model_dump())
        except ValidationError as exc:
            raise LLMResolutionError("Normalized output violated its schema.") from exc

    def _normalize_narrative(
        self, result: RoundResolution, schema: type[BaseModel]
    ) -> RoundResolution:
        """Normalize presentation before final semantic and privacy checks."""
        normalized = result.model_copy(
            update={
                "player_resolutions": {
                    name: name_resolution(name, text)
                    for name, text in result.player_resolutions.items()
                }
            }
        )
        return self._revalidate_result(normalized, schema)

    def _commit_history(
        self,
        result: BaseModel,
        schema: type[BaseModel],
        prompt: dict[str, str],
        response_text: str | None,
        names: tuple[str, ...],
    ) -> None:
        """Commit validated output and its participants together without awaiting."""
        content = result.model_dump_json()
        if response_text:
            try:
                original = schema.model_validate_json(response_text)
                if original.model_dump() == result.model_dump():
                    content = response_text
            except (ValidationError, ValueError):
                pass
        self.history.extend([prompt, {"role": "assistant", "content": content}])
        self._known_player_names = list(dict.fromkeys([*self._known_player_names, *names]))

    def begin_round_usage(self, number: int) -> None:
        """Start accounting once per round; host retries keep the same totals."""
        set_debug_round_number(number)
        if self.usage_round != number:
            self.usage_round = number
            self.round_usage = UsageTotals()
            self.round_usage_by_kind = {}
            self.round_work_seconds = 0.0
            self.round_failures = 0
            self.last_round_error = None
            self._round_started = None
        if self._round_started is None:
            self._round_started = perf_counter()

    def finish_round_usage(self, error: str | None = None) -> None:
        """Include budgeting, tokenization and retry waits in round work time."""
        if self._round_started is not None:
            self.round_work_seconds += perf_counter() - self._round_started
            self._round_started = None
            self.round_failures += error is not None
            self.last_round_error = error

    def round_debug_state(self) -> dict[str, Any]:
        """Return private resolver state for the local full-round diagnostic."""
        return {
            "private_guidance": self.private_guidance,
            "genesis_state": self.genesis_state,
            "durable_memory": self.memory,
            "recent_history": list(self.history),
            "known_player_names": list(self._known_player_names),
            "chance_rule_interpretation": (
                self.chance_rule_interpretation.model_dump()
                if self.chance_rule_interpretation is not None
                else None
            ),
        }

    async def complete_round_debug(self, number: int, engine_summary: dict[str, Any]) -> None:
        """Combine the round's requests with engine and resolver private state."""
        try:
            if self._debug_logger is None:
                return
            await self._debug_logger.finish_round(
                number,
                {
                    "engine": engine_summary,
                    "resolver": self.round_debug_state(),
                },
            )
        except Exception as exc:
            # Diagnostics must never prevent a committed round from being published.
            logger.warning("Round debug log could not be finalized: %s", type(exc).__name__)
        finally:
            set_debug_round_number(None)

    async def refresh_usage(self) -> None:
        """Measure retained messages with the request tokenizer, without inference."""
        messages = [*self._fixed_messages(), *self.history]
        count = await self.budget.input_tokens(messages, None)
        self._retained_measurement = (messages, count, self.token_count_method)

    def usage_snapshot(self) -> dict[str, Any]:
        """Separate estimated retained messages from billed request consumption."""
        messages = [*self._fixed_messages(), *self.history]
        measured = self._retained_measurement
        if measured is not None and measured[0] == messages:
            retained, method = measured[1:]
        else:
            retained = self.budget.context_size(messages)
            method = (
                "local tokenizer estimate"
                if self.budget.encoding
                else "conservative UTF-8 estimate"
            )
        return {
            "round_number": self.usage_round,
            "round_failures": self.round_failures,
            "last_round_error": self.last_round_error,
            "round_work_seconds": self.round_work_seconds
            + (perf_counter() - self._round_started if self._round_started is not None else 0),
            "round": self.round_usage.snapshot(),
            "game": self.game_usage.snapshot(),
            "round_by_kind": {
                kind: usage.snapshot() for kind, usage in self.round_usage_by_kind.items()
            },
            "last_request": self.last_request,
            "retained_context_tokens": retained,
            "context_window_size": self.context_window_size,
            "context_window_source": self.context_window_source,
            "counting_method": f"Retained messages: {method}; excludes next input/schema/output",
        }

    async def _parse(self, messages, schema, kind, repair_attempt=0):
        return await provider.execute(self, messages, schema, kind, repair_attempt)

    async def _parse_attempt(self, messages, schema, kind, count, attempt, repair_attempt=0):
        return await provider.attempt(self, messages, schema, kind, count, attempt, repair_attempt)

    async def _compact_if_needed(self, prompt, schema, kind):
        await compaction.compact(self, prompt, schema, kind)

    async def close(self) -> None:
        """Close the OpenAI and HTTP clients."""
        if self.client is not None:
            await self.client.close()
            self.client = None
        await self.budget.close()

    @property
    def context_window_size(self) -> int:
        """The discovered per-slot context limit, or configured fallback."""
        return self.budget.context_window_size

    @context_window_size.setter
    def context_window_size(self, value: int) -> None:
        """Override the budget's context limit for tests or controlled diagnostics."""
        self.budget.context_window_size = value

    @property
    def context_window_source(self) -> str:
        """Describe whether the context limit came from discovery or configuration."""
        return self.budget.context_window_source

    @property
    def token_count_method(self) -> str:
        """Describe the tokenizer or conservative estimate used for the last count."""
        return self.budget.token_count_method

    async def discover_context_window(self) -> None:
        """Expose optional backend context discovery for setup and diagnostics.

        Normal request and preflight paths call the budget directly as needed, so
        discovery failure remains recoverable rather than permanently cached.
        """
        await self.budget.discover_context_window()
