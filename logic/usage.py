"""Prompt-free inference accounting; absent provider counters remain unknown."""

from dataclasses import dataclass, field
from typing import Any

COUNTERS = (
    "input_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "processed_prompt_tokens",
    "reused_prompt_tokens",
)


def counter(value: Any, name: str) -> int | None:
    """Read a nonnegative provider counter without coercion."""
    item = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return item if type(item) is int and item >= 0 else None


@dataclass
class UsageTotals:
    """Bounded aggregates with explicit counts of missing measurements."""

    attempts: int = 0
    errors: int = 0
    retries: int = 0
    latency_seconds: float = 0.0
    estimated_input_tokens: int = 0
    known: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))
    missing: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))

    def add(self, record: dict[str, Any]) -> None:
        """Accumulate one attempt, including failed and cancelled attempts."""
        self.attempts += 1
        self.errors += record["error"] is not None
        self.retries += record["retry"]
        self.latency_seconds += record["latency_seconds"]
        self.estimated_input_tokens += record["estimated_input_tokens"]
        for name in COUNTERS:
            value = record[name]
            self.known[name] += value or 0
            self.missing[name] += value is None

    def snapshot(self) -> dict[str, Any]:
        """Return totals, retaining partial measurements separately."""
        return {
            "attempts": self.attempts,
            "errors": self.errors,
            "retries": self.retries,
            "latency_seconds": self.latency_seconds,
            "estimated_input_tokens": self.estimated_input_tokens,
            **{name: None if self.missing[name] else self.known[name] for name in COUNTERS},
            "known_tokens": dict(self.known),
            "missing_counters": dict(self.missing),
        }
