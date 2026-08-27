"""Game DFA and turn-order integration tests."""

import asyncio
from pathlib import Path

from core.config import settings
from core.schemas import ClientPayload, RoundResolution, ServerEvent
from logic.engine import GameEngine, GameState, IDLE_ACTION
from logic.transcript import GameTranscript


class FakeSender:
    def __init__(self) -> None:
        self.events: list[tuple[str | None, ServerEvent]] = []

    async def broadcast_global(self, event: ServerEvent) -> None:
        self.events.append((None, event))

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        self.events.append((client_id, event))

    def events_of_type(self, event_type: str) -> list[ServerEvent]:
        return [event for _, event in self.events if event.type == event_type]


class FakeResolver:
    def __init__(self) -> None:
        self.scenario = ""
        self.rounds = 0

    def set_genesis(self, scenario: str) -> None:
        self.scenario = scenario

    async def generate_initial_state(self) -> RoundResolution:
        return RoundResolution(
            round_title="The Test Quest",
            global_narrative="Two adventurers stand at a gate.",
            player_resolutions={},
        )

    async def generate_resolution(self, round_buffer: dict[str, str]) -> RoundResolution:
        self.rounds += 1
        return RoundResolution(
            round_title=f"Round {self.rounds}",
            global_narrative=f"State after round {self.rounds}.",
            player_resolutions={
                name: f"Resolved: {action}" for name, action in round_buffer.items()
            },
        )


def payload(event_type: str, **data: object) -> ClientPayload:
    return ClientPayload.model_validate({"event_type": event_type, "data": data})


async def build_started_game(tmp_path: Path) -> tuple[GameEngine, FakeSender, FakeResolver]:
    sender = FakeSender()
    resolver = FakeResolver()
    engine = GameEngine(sender, resolver)
    engine.transcript = GameTranscript(tmp_path / "logs")

    await engine.process_payload(
        "host", payload("auth", name="Host", password=settings.server.host_password)
    )
    await engine.process_payload(
        "host", payload("scenario_init", scenario="A gate blocks the road.")
    )
    await engine.process_payload(
        "player", payload("auth", name="Player", password=settings.server.player_password)
    )
    await engine.process_payload("host", payload("start_game"))
    return engine, sender, resolver


def test_full_round_is_strict_and_logged(tmp_path: Path) -> None:
    async def run() -> None:
        engine, sender, resolver = await build_started_game(tmp_path)
        assert engine.state is GameState.ACTIVE_TURN
        assert engine.active_player_id == "host"

        await engine.process_payload("player", payload("action", action="Runs ahead"))
        assert sender.events_of_type("error")[-1].payload["msg"] == "It is not your turn."

        await engine.process_payload("host", payload("action", action="Opens the gate"))
        assert engine.active_player_id == "player"
        await engine.process_payload("player", payload("action", action="Keeps watch"))

        assert resolver.rounds == 1
        assert engine.round_counter == 1
        assert engine.state is GameState.ACTIVE_TURN
        assert engine.active_player_id == "host"
        assert sender.events_of_type("state_update")[-1].payload["global_narrative"] == (
            "State after round 1."
        )

        assert engine.transcript.path is not None
        transcript = engine.transcript.path.read_text(encoding="utf-8")
        assert "Round: 1\nState: Two adventurers stand at a gate." in transcript
        assert "Host: Opens the gate" in transcript
        assert "Player: Keeps watch" in transcript
        assert "Resulting state: State after round 1." in transcript

    asyncio.run(run())


def test_active_disconnect_injects_idle_and_advances(tmp_path: Path) -> None:
    async def run() -> None:
        engine, sender, resolver = await build_started_game(tmp_path)
        await engine.process_payload("host", payload("action", action="Waits"))
        assert engine.active_player_id == "player"

        await engine.handle_disconnect("player")

        assert resolver.rounds == 1
        assert engine.active_player_id == "host"
        state_event = sender.events_of_type("state_update")[-1]
        player_result = state_event.payload["player_resolutions"]["Player"]
        assert player_result.startswith(
            "Resolved: [SYSTEM: Explain this player's in-world departure"
        )
        assert player_result.endswith(IDLE_ACTION)
        assert sender.events_of_type("system_msg")[-1].payload["msg"] == ("Player disconnected.")

    asyncio.run(run())


def test_chat_remains_available_outside_turns(tmp_path: Path) -> None:
    async def run() -> None:
        engine, sender, _ = await build_started_game(tmp_path)
        await engine.process_payload("player", payload("chat", message="Ready!"))

        chat_event = sender.events_of_type("chat_echo")[-1]
        assert chat_event.payload == {"name": "Player", "chat": "Ready!"}

    asyncio.run(run())
