"""Validate structured output and protect private guidance and rolls."""

import logging
import re
from html import unescape

from pydantic import BaseModel

from core.schemas import ContextSummary, DicePlan, RoundResolution, ChanceEventResult
from .errors import LLMResolutionError
from logic.dice import non_percentage_guidance_lines

logger = logging.getLogger(__name__)


def check_semantics(result: BaseModel, names: tuple[str, ...] | None) -> None:
    """Reject incomplete or inconsistent structured output before accepting it."""
    if isinstance(result, DicePlan):
        if names is not None and set(result.rolls) != set(names):
            raise LLMResolutionError("Invalid dice plan participants.")
        if len(set(result.hidden_rolls)) != len(result.hidden_rolls) or not set(
            result.hidden_rolls
        ) <= {name for name, needed in result.rolls.items() if needed}:
            raise LLMResolutionError("Invalid hidden dice membership.")
    if isinstance(result, ContextSummary):
        if names is not None and set(result.player_states) != set(names):
            raise LLMResolutionError("Invalid summary participants.")
        if not result.world_state.strip() or any(
            not text.strip() or text.strip().casefold() in ("{}", "[]", "none", "unknown", "null")
            for text in result.player_states.values()
        ):
            raise LLMResolutionError("Summary contains empty or unknown player state.")
    if isinstance(result, RoundResolution):
        for text in (
            result.global_narrative,
            *result.player_resolutions.values(),
        ):
            # Inspect decoded entities too, but retain the original output. Stripping
            # arbitrary tags could erase an entire malformed outcome such as <I/O Error: ...>.
            decoded = unescape(text)
            if (
                re.search(r"<\s*/?\s*[A-Za-z][^>\n]*>", decoded)
                or "```" in decoded
                or re.search(
                    r"\[\s*SYSTEM\b|^\s*(?:I/O\s+Error|SYSTEM INJECTION)\s*:",
                    decoded,
                    re.IGNORECASE | re.MULTILINE,
                )
            ):
                raise LLMResolutionError(
                    "Narrative must be plain prose without markup or technical status "
                    "messages; describe disconnects only through in-world consequences."
                )
        if not result.global_narrative.strip():
            raise LLMResolutionError("Model returned empty required narrative content.")
        if names is not None and set(result.player_resolutions) != set(names):
            raise LLMResolutionError("Invalid resolution participants.")
        if any(not value.strip() for value in result.player_resolutions.values()):
            raise LLMResolutionError("Model returned an empty player outcome.")
        for value in result.player_resolutions.values():
            # Catch clear incomplete clauses without requiring English punctuation
            # on every outcome or trying to move text between player identities.
            ending = value.rstrip().rstrip("\"'\u2019\u201d)]}").rstrip()
            if ending.endswith((",", ";", ":")) or re.match(r"\s*\$[A-Za-z]", value):
                raise LLMResolutionError(
                    "Player outcomes must be complete, self-contained prose. Finish "
                    "sentences inside their own player field, not the next player's field; "
                    "remove stray prefixes. Preserve the supplied actions, dice and facts."
                )


def check_public_output(
    result: RoundResolution,
    private_rolls: dict[str, int],
    guidance: str,
    public_rolls: dict[str, int] | None = None,
    private_events: list[ChanceEventResult] | None = None,
) -> None:
    """Reject direct guidance echoes and explicit hidden dice disclosures.

    This is a conservative backstop, not a claim to detect every paraphrase of
    a secret. Prompt instructions still distinguish observable consequences, and
    this check runs before the result can enter public events or retained history.
    """
    text = " ".join([result.global_narrative, *result.player_resolutions.values()])
    normalized = " ".join(text.casefold().split())
    fragments = re.split(r"[.!?\n]+", guidance)
    secret_literals = re.findall(
        r"(?:password|passphrase|secret(?:\s+(?:code|word))?|pin|token|salainen\s+tunnus|"
        r"秘密の合言葉|秘密口令)\s*(?:is|=|:|on|は|是)\s*[\"']?([^\s\"'.,;!?]+)",
        guidance,
        re.IGNORECASE,
    )
    if any(
        re.search(
            (
                re.escape(value.casefold())
                if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", value)
                else r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)"
            ),
            normalized,
        )
        for value in secret_literals
    ):
        raise LLMResolutionError(
            "Model output disclosed a named private secret; no result committed."
        )
    if any(
        len(fragment.strip()) >= 4
        and re.search(
            r"(?<!\w)" + re.escape(" ".join(fragment.casefold().split())) + r"(?!\w)", normalized
        )
        for fragment in fragments
    ):
        raise LLMResolutionError("Model output disclosed private guidance; no result committed.")
    for name, value in private_rolls.items():
        if value in (public_rolls or {}).values() and not re.search(
            rf"(?:hidden|private|secret|{re.escape(name)})[^.!?\n]{{0,80}}\b{value}\b",
            text,
            re.IGNORECASE,
        ):
            continue
        if re.search(
            rf"\b(?:rolled?|check|d100|dice)\b[^.!?\n]{{0,80}}\b{value}\b", text, re.IGNORECASE
        ) or re.search(rf"\b{value}\s*/\s*100\b", text):
            raise LLMResolutionError("Model output disclosed a private check; no result committed.")
    for event in private_events or []:
        if re.search(rf"\b{event.event.chance_percent}\s*(?:%|percent\b)", text, re.IGNORECASE):
            raise LLMResolutionError("Model output disclosed a private event probability.")


def normalize_hidden_roll_sources(plan: DicePlan, guidance: str) -> DicePlan:
    """Keep a required roll public when its private cause is missing or invalid."""
    valid_sources = {
        " ".join(line.casefold().split()): line.strip()
        for line in non_percentage_guidance_lines(guidance)
    }
    hidden = []
    sources = {}
    for name in plan.hidden_rolls:
        source = plan.hidden_roll_sources.get(name, "")
        normalized = " ".join(source.casefold().split())
        if plan.rolls.get(name) and normalized in valid_sources:
            hidden.append(name)
            sources[name] = valid_sources[normalized]
        else:
            logger.info(
                "Ignoring unsupported hidden classification for %r; action roll remains public",
                name,
            )
    return plan.model_copy(update={"hidden_rolls": hidden, "hidden_roll_sources": sources})
