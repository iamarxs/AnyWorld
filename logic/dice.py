"""Dungeon Master dice mechanics."""

import secrets
import re
from functools import lru_cache

from core.schemas import (
    ChanceEvent,
    ChanceEventResult,
    ChanceRuleDecision,
    ChanceRuleInterpretation,
    StructuredChanceRule,
)


def private_chance_rule(guidance: str) -> tuple[str, int] | None:
    """Return the one dedicated percentage rule, without interpreting its condition."""
    rule = None
    for line in guidance.splitlines():
        percentages = re.findall(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:%|percent\b)", line, re.IGNORECASE)
        if len(percentages) == 1 and percentages[0].isdigit() and 0 <= int(percentages[0]) <= 100:
            if rule is not None:
                raise ValueError("Only one dedicated percentage rule is allowed per game.")
            rule = (line.strip(), int(percentages[0]))
    return rule


def combine_private_guidance(guidance: str, chance_event: str = "") -> str:
    """Validate the single optional chance field and combine it with freeform guidance."""
    if re.search(r"%|\bpercent(?:age)?\b|\bper\s+cent\b", guidance, re.IGNORECASE):
        raise ValueError(
            "Freeform DM guidance cannot contain percentage events. Put one optional "
            "percentage-based event in the dedicated chance event field."
        )
    if not chance_event:
        return guidance
    if "\n" in chance_event or "\r" in chance_event:
        raise ValueError("'chance_event' must be a single line")
    matches = re.findall(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:%|percent\b)", chance_event, re.IGNORECASE)
    if (
        len(matches) != 1
        or not matches[0].isdigit()
        or not 0 <= int(matches[0]) <= 100
        or private_chance_rule(chance_event) is None
    ):
        raise ValueError(
            "'chance_event' must contain exactly one whole-number percentage from 0 to 100."
        )
    return "\n".join(part for part in (guidance, chance_event) if part)


def _is_per_round_rule(instruction: str) -> bool:
    """Return whether a rule explicitly requests one check on every turn/round."""
    return bool(
        re.search(
            r"\b(?:per|each|every)\s+(?:single\s+)?(?:turn|round)s?\b",
            instruction,
            re.IGNORECASE,
        )
    )


def _has_conditional_trigger(instruction: str) -> bool:
    """Return whether a chance rule names a condition instead of defaulting to each round."""
    return bool(
        re.search(
            r"\b(?:when(?:ever)?|if|after|before|once|upon|while|until|"
            r"as\s+soon\s+as|each\s+time|every\s+time)\b",
            instruction,
            re.IGNORECASE,
        )
    )


def conditional_chance_rule(guidance: str) -> tuple[str, int] | None:
    """Return the dedicated rule when it explicitly names a condition."""
    rule = private_chance_rule(guidance)
    if rule is not None and not _is_per_round_rule(rule[0]) and _has_conditional_trigger(rule[0]):
        return rule
    return None


@lru_cache(maxsize=64)
def non_percentage_guidance_lines(guidance: str) -> tuple[str, ...]:
    """Return reusable private guidance lines that can identify hidden checks."""
    return tuple(
        line.strip()
        for line in guidance.splitlines()
        if len(line.strip()) >= 12 and not re.search(r"%|\bpercent\b", line, re.IGNORECASE)
    )


def has_non_percentage_private_guidance(guidance: str) -> bool:
    """Return whether guidance contains a possible private cause beyond chance rules."""
    chance_rule = private_chance_rule(guidance)
    chance_line = chance_rule[0] if chance_rule is not None else None
    return any(line.strip() and line.strip() != chance_line for line in guidance.splitlines())


def _normalize_structured_occurrences(
    decision: ChanceRuleDecision,
    interpretation: ChanceRuleInterpretation | None,
    expected_names: tuple[str, ...] | None,
) -> ChanceRuleDecision:
    """Convert normalized action matches to exact participant names only."""
    if interpretation is None:
        return decision
    if not expected_names:
        return decision
    names = {name.casefold(): name for name in expected_names}
    if interpretation.occurrence_scope == "shared" and any(
        occurrence.strip().casefold() == "shared" for occurrence in decision.occurrences
    ):
        return decision.model_copy(update={"occurrences": ["shared"]})
    matched = []
    for occurrence in decision.occurrences:
        name = names.get(occurrence.strip().casefold())
        if name is not None and name not in matched:
            matched.append(name)
    if interpretation.occurrence_scope == "shared":
        matched = ["shared"] if matched else []
    return decision.model_copy(update={"occurrences": matched})


def normalize_chance_rule_decision(
    decision: ChanceRuleDecision | None,
    guidance: str,
    interpretation: ChanceRuleInterpretation | None = None,
    expected_names: tuple[str, ...] | None = None,
) -> ChanceRuleDecision | None:
    """Enforce the one host-authored trigger; unspecified rules default to each round."""
    rule = private_chance_rule(guidance)
    if rule is None:
        if decision is not None:
            raise ValueError("A chance decision was supplied without a private chance rule.")
        return None
    instruction, _ = rule
    if interpretation is not None and interpretation.structured:
        if decision is None:
            raise ValueError("Structured chance eligibility requires a decision.")
        return _normalize_structured_occurrences(
            decision.model_copy(update={"trigger": interpretation.cadence}),
            interpretation,
            expected_names,
        )
    if _is_per_round_rule(instruction) or not _has_conditional_trigger(instruction):
        return ChanceRuleDecision(
            trigger="per_round",
            occurrences=["round"],
        )
    if decision is None:
        raise ValueError("The conditional chance rule requires a structured trigger decision.")
    return _normalize_structured_occurrences(
        decision.model_copy(update={"trigger": "condition"}),
        interpretation,
        expected_names,
    )


def validate_chance_events(
    events: list[ChanceEvent],
    guidance: str,
    *,
    preserve_occurrences: bool = False,
) -> list[ChanceEvent]:
    """Resolve event metadata to the one trusted private rule."""
    rule = private_chance_rule(guidance)
    if rule is None:
        if events:
            raise ValueError("Chance events require one private percentage rule.")
        return []
    instruction, percentage = rule
    normalized = []
    seen = set()
    for event in events:
        if event.chance_percent != percentage:
            raise ValueError(
                "Chance event must keep the dedicated private rule's percentage unchanged."
            )
        occurrence = (
            "round"
            if event.trigger == "per_round" and not preserve_occurrences
            else event.occurrence.strip()
        )
        if not occurrence:
            raise ValueError("Conditional chance events must describe the triggering occurrence.")
        key = (instruction.casefold(), occurrence.casefold())
        if key in seen:
            raise ValueError("Chance events must not repeat the same rule and occurrence.")
        seen.add(key)
        normalized.append(
            event.model_copy(update={"source_rule": instruction, "occurrence": occurrence})
        )
    return normalized


def structured_rule_text(rule: StructuredChanceRule) -> str:
    """Stable private serialization; cadence and eligibility remain independent."""
    cadence = "per round" if rule.cadence == "per_round" else f"when {rule.trigger}"
    eligibility = f"; eligibility: {rule.eligibility}" if rule.eligibility else ""
    return (
        f"{rule.chance_percent}% {cadence}; scope: {rule.scope}{eligibility}; effect: {rule.effect}"
    )


def validate_legacy_rule(text: str) -> None:
    """Require host correction for mixed or missing legacy cadence."""
    if text and (_is_per_round_rule(text) == _has_conditional_trigger(text)):
        raise ValueError(
            "Legacy chance text has ambiguous cadence. Use the structured rule controls."
        )


def chance_events_from_decision(
    decision: ChanceRuleDecision | None,
    guidance: str,
    interpretation: ChanceRuleInterpretation | None = None,
    expected_names: tuple[str, ...] | None = None,
) -> list[ChanceEvent]:
    """Build every roll from the one rule's occurrences, without a second flag."""
    rule = private_chance_rule(guidance)
    normalized = normalize_chance_rule_decision(decision, guidance, interpretation, expected_names)
    if normalized is None:
        return []
    if rule is None:
        return []
    instruction, percentage = rule
    occurrences = normalized.occurrences
    if normalized.trigger == "per_round" and (
        interpretation is None or interpretation.cadence != "per_round"
    ):
        occurrences = ["round"]
    else:
        normalized_occurrences = [" ".join(occurrence.split()) for occurrence in occurrences]
        if any(
            not occurrence
            or occurrence.casefold() in {"round", "this round", "current round", "per round"}
            for occurrence in normalized_occurrences
        ):
            raise ValueError(
                "The conditional chance rule requires an occurrence describing the actual "
                "trigger; 'round' is reserved for per-round rules."
            )
        occurrences = normalized_occurrences
    events = []
    for occurrence in occurrences:
        if interpretation is not None:
            if occurrence == "shared":
                occurrence = "The shared action trigger occurred"
            else:
                occurrence = f"{occurrence} matched the normalized trigger"
        events.append(
            ChanceEvent(
                source_rule=instruction,
                chance_percent=percentage,
                trigger=normalized.trigger,
                occurrence=occurrence,
            )
        )
    return validate_chance_events(
        events,
        guidance,
        preserve_occurrences=interpretation is not None and interpretation.cadence == "per_round",
    )


def roll_chance(event: ChanceEvent) -> ChanceEventResult:
    """Resolve an exact whole-percent chance without changing action dice."""
    roll = secrets.randbelow(100) + 1
    return ChanceEventResult(event=event, roll=roll, occurred=roll <= event.chance_percent)


def roll_d100() -> int:
    """Return a cryptographically unbiased integer from 0 through 100."""
    return secrets.randbelow(101)


def describe_roll(value: int) -> str:
    """Provide the fixed interpretation supplied to the DM."""
    if value < 11:
        return "catastrophic failure"
    if value < 25:
        return "failure with a serious consequence"
    if value < 35:
        return "failure or costly partial success"
    if value < 50:
        return "mediocre success"
    if value < 65:
        return "adequate success"
    if value < 75:
        return "qualified success"
    if value < 90:
        return "resounding success"
    if value < 101:
        return "perfect success, extra benefits"
    return "success"
