"""Dungeon Master dice mechanics."""

import secrets
import re

from core.schemas import ChanceEvent, ChanceEventResult, ChanceRuleDecision


def private_chance_rules(guidance: str) -> dict[str, tuple[str, int]]:
    """Index percentage-bearing guidance lines without interpreting their conditions."""
    rules = {}
    for line in guidance.splitlines():
        percentages = re.findall(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:%|percent\b)", line, re.IGNORECASE)
        if len(percentages) == 1 and percentages[0].isdigit() and 0 <= int(percentages[0]) <= 100:
            rules[f"rule-{len(rules) + 1}"] = (line.strip(), int(percentages[0]))
    return rules


def validate_chance_events(
    events: list[ChanceEvent],
    guidance: str,
) -> list[ChanceEvent]:
    """Resolve model references to trusted rules; tolerate unambiguous paraphrases."""
    rules = private_chance_rules(guidance)
    normalized = []
    seen = set()
    for event in events:
        reference = " ".join(event.source_rule.casefold().split())
        rule = rules.get(reference)
        if rule is None:
            candidates = [item for item in rules.values() if item[1] == event.chance_percent]
            matches = [
                item for item in candidates if " ".join(item[0].casefold().split()) == reference
            ]
            if len(matches) == 1:
                rule = matches[0]
            elif len(candidates) == 1:
                # Replace the paraphrase, rather than trusting its description of the effect.
                rule = candidates[0]
        if rule is None or rule[1] != event.chance_percent:
            raise ValueError(
                "Chance event must select a matching private whole-percent rule. "
                "Use its rule ID from the private chance rule catalog in source_rule; "
                "keep the catalog percentage unchanged."
            )
        occurrence = "round" if event.trigger == "per_round" else event.occurrence.strip()
        if not occurrence:
            raise ValueError("Conditional chance events must describe the triggering occurrence.")
        key = (rule[0].casefold(), occurrence.casefold())
        if key in seen:
            raise ValueError("Chance events must not repeat the same rule and occurrence.")
        seen.add(key)
        normalized.append(
            event.model_copy(update={"source_rule": rule[0], "occurrence": occurrence})
        )
    return normalized


def chance_events_from_decisions(
    decisions: dict[str, ChanceRuleDecision], guidance: str
) -> list[ChanceEvent]:
    """Build every roll from per-rule occurrences, without a second applicability flag."""
    rules = private_chance_rules(guidance)
    if set(decisions) != set(rules):
        raise ValueError(
            "Chance plan must account for every private rule in chance_rule_decisions: "
            + ", ".join(rules)
            + ". Do not omit earlier rows or stop after selecting one rule."
        )
    events = []
    for rule_id, (_, percentage) in rules.items():
        decision = decisions[rule_id]
        if not decision.reason.strip():
            raise ValueError("Explain each rule's triggering occurrences or why none occurred.")
        occurrences = decision.occurrences
        if decision.trigger == "per_round":
            # Every-round rules get exactly one check; other conditions use condition.
            occurrences = ["round"]
        for occurrence in occurrences:
            events.append(
                ChanceEvent(
                    source_rule=rule_id,
                    chance_percent=percentage,
                    trigger=decision.trigger,
                    occurrence=occurrence,
                )
            )
    if len(events) > 16:
        raise ValueError("More than 16 chance occurrences apply; split or simplify the rules.")
    return validate_chance_events(events, guidance)


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
