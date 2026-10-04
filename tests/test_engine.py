"""Game DFA and turn-order integration tests."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from core.config import settings
from core.schemas import ClientPayload, DicePlan, RoundResolution
from logic.engine import GameEngine, GameState, IDLE_ACTION
from logic.transcript import GameTranscript


from support import FakeSender, FakeResolver


def password_digest(password: str, client_id: str) -> str:
    """Compute the SHA-256 digest for a password and client id."""
    return hashlib.sha256(f"{password}{client_id}".encode()).hexdigest()


def payload(event_type: str, **data: object) -> ClientPayload:
    """Build a validated ClientPayload."""
    return ClientPayload.model_validate({"event_type": event_type, "data": data})


async def authenticate(
    engine: GameEngine,
    client_id: str,
    *,
    name: str,
    password: str,
    reconnect_token: str | None = None,
) -> None:
    """Authenticate directly through the lobby API used by the socket gateway."""
    auth_payload = payload(
        "auth",
        name=name,
        password_digest=password_digest(password, client_id),
        reconnect_token=reconnect_token,
    )
    await engine._authenticate(client_id, auth_payload.data)


async def build_started_game(tmp_path: Path) -> tuple[GameEngine, FakeSender, FakeResolver]:
    """Build an engine with a started game."""
    sender = FakeSender()
    resolver = FakeResolver()
    engine = GameEngine(sender, resolver)
    engine.transcript = GameTranscript(tmp_path / "logs")

    await authenticate(
        engine,
        "host",
        name="Host",
        password=settings.server.host_password,
    )
    await engine.process_payload(
        "host", payload("scenario_init", scenario="A gate blocks the road.")
    )
    await engine.wait_for_inference()
    await authenticate(
        engine,
        "player",
        name="Player",
        password=settings.server.player_password,
    )
    await engine.process_payload("host", payload("start_game"))
    await engine.wait_for_inference()
    return engine, sender, resolver


def test_full_round_is_strict_and_logged(tmp_path: Path) -> None:
    """Verify a full round is strict, ordered and logged."""

    async def run() -> None:
        engine, sender, resolver = await build_started_game(tmp_path)
        assert engine.state is GameState.ACTIVE_TURN
        assert engine.active_player_id == "host"
        assert resolver.start_names == [["Host", "Player"]]
        assert engine.opening_scenario == "Host, Player stand at a gate."

        await engine.process_payload("player", payload("action", action="Runs ahead"))
        assert sender.events_of_type("error")[-1].payload["msg"] == "It is not your turn."

        await engine.process_payload("host", payload("action", action="Opens the gate"))
        assert engine.active_player_id == "player"
        await engine.process_payload("player", payload("action", action="Keeps watch"))
        await engine.wait_for_inference()

        assert resolver.rounds == 1
        snapshot = engine._snapshot_locked(engine.players["player"])
        assert snapshot["opening_scenario"] == "Host, Player stand at a gate."
        assert snapshot["scenario_state"] == "State after round 1."
        assert engine.round_counter == 1
        assert engine.state is GameState.ACTIVE_TURN
        assert engine.active_player_id == "host"
        assert sender.events_of_type("state_update")[-1].payload["global_narrative"] == (
            "State after round 1."
        )

        assert engine.transcript.path is not None
        transcript = engine.transcript.path.read_text(encoding="utf-8")
        assert "<h2>Round 1</h2>" in transcript
        assert '<dt class="player-color-0">Host</dt><dd>Opens the gate</dd>' in transcript
        assert '<dt class="player-color-1">Player</dt><dd>Keeps watch</dd>' in transcript
        assert '<p class="state">State after round 1.</p>' in transcript

    asyncio.run(run())


def test_scenario_title_is_generated_before_game_start(tmp_path: Path) -> None:
    """Verify the scenario title is generated before start."""

    async def run() -> None:
        sender = FakeSender()
        resolver = FakeResolver()
        engine = GameEngine(sender, resolver)
        engine.transcript = GameTranscript(tmp_path / "logs")

        await authenticate(
            engine,
            "host",
            name="Host",
            password=settings.server.host_password,
        )
        await engine.process_payload(
            "host", payload("scenario_init", scenario="A gate blocks the road.")
        )
        await engine.wait_for_inference()

        ready = sender.events_of_type("scenario_ready")[-1]
        assert ready.payload["title"] == "The Test Quest"
        assert engine.scenario_title == "The Test Quest"
        assert engine.current_scenario_state is None
        assert resolver.start_names == []
        assert engine.opening_scenario is None
        assert not sender.events_of_type("state_update")

        await engine.process_payload("host", payload("start_game"))
        await engine.wait_for_inference()
        assert engine.scenario_title == "The Test Quest"
        start_update = sender.events_of_type("state_update")[-1]
        assert start_update.payload["scenario_title"] == "The Test Quest"
        assert start_update.payload["global_narrative"] == "Host stand at a gate."
        assert start_update.payload["original_scenario"] == "A gate blocks the road."

    asyncio.run(run())


def test_scenario_uses_one_separate_chance_event_and_freeform_guidance(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sender = FakeSender()
        resolver = FakeResolver()
        engine = GameEngine(sender, resolver)
        engine.transcript = GameTranscript(tmp_path / "logs")

        await authenticate(
            engine,
            "host",
            name="Host",
            password=settings.server.host_password,
        )
        await engine.process_payload(
            "host",
            payload(
                "scenario_init",
                scenario="A gate blocks the road.",
                guidance="Keep the tone eerie.",
                chance_event="Add a 40% chance every round that a bell rings.",
            ),
        )
        await engine.wait_for_inference()

        assert engine.private_guidance == (
            "Keep the tone eerie.\nAdd a 40% chance every round that a bell rings."
        )
        assert sender.events_of_type("scenario_ready")
        await engine.process_payload("host", payload("start_game"))
        await engine.wait_for_inference()
        content = engine.transcript.path.read_text(encoding="utf-8")
        assert '<h3>Freeform guidance</h3>\n<p class="state">Keep the tone eerie.</p>' in content
        assert (
            '<h3>Chance event</h3>\n<p class="state">'
            "Add a 40% chance every round that a bell rings.</p>"
        ) in content

        await engine.shutdown()

    asyncio.run(run())


def test_scenario_rejects_percentage_events_in_freeform_guidance(tmp_path: Path) -> None:
    async def run() -> None:
        sender = FakeSender()
        engine = GameEngine(sender, FakeResolver())
        engine.transcript = GameTranscript(tmp_path / "logs")
        await authenticate(
            engine,
            "host",
            name="Host",
            password=settings.server.host_password,
        )
        await engine.process_payload(
            "host",
            payload(
                "scenario_init",
                scenario="A gate blocks the road.",
                guidance="Add a 20% chance every round that a bell rings.\n"
                "Add a 30% chance every round that a raven appears.",
            ),
        )

        assert engine.state is GameState.SCENARIO_INJECTION
        assert "dedicated chance event field" in sender.events_of_type("error")[-1].payload["msg"]

    asyncio.run(run())


def test_active_disconnect_injects_idle_and_advances(tmp_path: Path) -> None:
    """Verify an active disconnect injects idle and advances the turn."""

    async def run() -> None:
        engine, sender, resolver = await build_started_game(tmp_path)
        await engine.process_payload("host", payload("action", action="Waits"))
        assert engine.active_player_id == "player"

        await engine.handle_disconnect("player")
        await engine.wait_for_inference()

        assert resolver.rounds == 1
        assert engine.active_player_id == "host"
        state_event = sender.events_of_type("state_update")[-1]
        player_result = state_event.payload["player_resolutions"]["Player"]
        assert "Resolved:" in player_result
        assert player_result.endswith(IDLE_ACTION)
        assert sender.events_of_type("system_msg")[-1].payload["msg"] == ("Player disconnected.")

    asyncio.run(run())


def test_host_can_end_game_and_finalize_transcript(tmp_path: Path) -> None:
    """Verify the host can end the game and finalize the transcript."""

    async def run() -> None:
        engine, sender, _ = await build_started_game(tmp_path)

        await engine.process_payload("player", payload("end_game"))
        assert sender.events_of_type("error")[-1].payload["msg"] == (
            "Only the host can end the game."
        )

        await engine.process_payload("host", payload("end_game"))
        assert engine.state is GameState.ENDED
        assert sender.events_of_type("game_ended")[-1].payload["msg"] == (
            "The host ended the game."
        )
        assert engine.transcript.path is not None
        assert "</html>" in engine.transcript.path.read_text(encoding="utf-8")

    asyncio.run(run())


def test_raw_password_is_rejected(tmp_path: Path) -> None:
    """Reject a raw password instead of a digest."""

    async def run() -> None:
        sender = FakeSender()
        engine = GameEngine(sender, FakeResolver())
        engine.transcript = GameTranscript(tmp_path / "logs")

        with pytest.raises(ValueError, match="'password_digest' must be a string"):
            await engine._authenticate(
                "host",
                payload("auth", name="Host", password=settings.server.host_password).data,
            )
        assert not engine.players

    asyncio.run(run())


def test_chat_remains_available_outside_turns(tmp_path: Path) -> None:
    """Verify chat works outside turns."""

    async def run() -> None:
        engine, sender, _ = await build_started_game(tmp_path)
        await engine.process_payload("player", payload("chat", message="Ready!"))

        chat_event = sender.events_of_type("chat_echo")[-1]
        assert chat_event.payload["name"] == "Player"
        assert chat_event.payload["chat"] == "Ready!"
        assert chat_event.payload["session_id"] == engine.session_id

    asyncio.run(run())


def test_round_without_checks_never_rolls_and_emits_clean_outcomes(tmp_path, monkeypatch):
    """A no-check plan completes without dice or duplicate labels in public outputs."""

    async def run():
        engine, sender, resolver = await build_started_game(tmp_path)

        async def plan_dice(actions, current_state):
            return DicePlan(rolls={name: False for name in actions}, hidden_rolls=[])

        async def resolve(actions, dice, hidden_rolls):
            assert dice == {}
            assert hidden_rolls == set()
            return RoundResolution(
                global_narrative="A journal is on the table.",
                player_resolutions={name: f"{name}: {name}: Sees a journal." for name in actions},
            )

        def unexpected_roll():
            raise AssertionError("No check should consume a dice roll")

        monkeypatch.setattr(resolver, "plan_dice", plan_dice, raising=False)
        monkeypatch.setattr(resolver, "generate_resolution", resolve)
        monkeypatch.setattr("logic.engine.roll_d100", unexpected_roll)
        await engine.process_payload("host", payload("action", action="Look around"))
        await engine.process_payload("player", payload("action", action="Look for useful items"))
        await engine.wait_for_inference()
        update = sender.events_of_type("state_update")[-1].payload
        assert update["dice_results"] == {}
        assert update["player_resolutions"] == {
            "Host": "Sees a journal.",
            "Player": "Sees a journal.",
        }
        assert engine.round_counter == 1
        transcript = engine.transcript.path.read_text(encoding="utf-8")
        assert '<dt class="player-color-0">Host</dt><dd>Sees a journal.</dd>' in transcript
        assert "Dice rolls" not in transcript

    asyncio.run(run())


def test_title_then_opening_are_the_only_lobby_inference_calls(tmp_path):
    """Joining players never generates or receives narrative before the host starts."""
    from core.schemas import ScenarioTitle
    from logic.llm_manager import LLMContextManager
    from test_priority_one_llm import FakeClient

    async def run():
        opening = "Host the ranger and Player the mage approach the ivy-covered gate."

        def respond(request):
            if request["response_format"] is ScenarioTitle:
                return ScenarioTitle(title="The Ivy Gate")
            return RoundResolution(global_narrative=opening, player_resolutions={})

        client = FakeClient(respond)
        manager = LLMContextManager(client)
        sender = FakeSender()
        engine = GameEngine(sender, manager)
        engine.transcript = GameTranscript(tmp_path / "logs")
        await authenticate(
            engine,
            "host",
            name="Host",
            password=settings.server.host_password,
        )
        await engine.process_payload(
            "host", payload("scenario_init", scenario="A gate blocks the road.")
        )
        await engine.wait_for_inference()
        assert engine.state is GameState.AWAITING_PLAYERS
        assert len(client.calls) == 1
        assert client.calls[0]["response_format"] is ScenarioTitle
        assert set(ScenarioTitle.model_json_schema()["properties"]) == {"title"}
        assert manager.history == []
        assert engine.current_scenario_state is None
        assert engine.transcript.path is None
        await authenticate(
            engine,
            "player",
            name="Player",
            password=settings.server.player_password,
        )
        assert len(client.calls) == 1
        snapshot = sender.events_of_type("auth_ok")[-1].payload
        assert snapshot["scenario_title"] == "The Ivy Gate"
        assert snapshot["opening_scenario"] is None
        assert snapshot["scenario_state"] is None
        assert snapshot["original_scenario"] == "A gate blocks the road."
        assert not sender.events_of_type("state_update")
        await engine.process_payload("host", payload("start_game"))
        await engine.wait_for_inference()
        assert len(client.calls) == 2
        prompt = client.calls[1]["messages"][-1]["content"]
        assert "Host, Player" in prompt
        assert "occupation, class, role" in prompt
        assert any("A gate blocks the road." in m["content"] for m in client.calls[1]["messages"])
        assert engine.state is GameState.ACTIVE_TURN
        updates = sender.events_of_type("state_update")
        assert len(updates) == 1
        assert updates[0].payload["global_narrative"] == opening
        content = engine.transcript.path.read_text(encoding="utf-8")
        assert 'Original scenario prompt</h2>\n<p class="state">A gate blocks the road.' in content
        assert f'Opening scenario</h2>\n<p class="state">{opening}' in content
        await engine.shutdown()

    asyncio.run(run())
