"""Deterministic inference/cancellation/reconnect races."""

import asyncio
import threading

import pytest

from core.config import settings
from core.schemas import DicePlan, RoundResolution
from logic.engine import GameEngine, GameState
from logic.llm_manager import LLMResolutionError
from logic.transcript import GameTranscript
from test_engine import FakeResolver, FakeSender, password_digest, payload


class ControlledResolver(FakeResolver):
    """Resolver that can block, fail and record inference phases."""

    def __init__(self):
        """Initialize the controlled resolver state."""
        super().__init__()
        self.block_phase = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.after_cancel = "cancel"
        self.fail_plan = False
        self.fail_round = False
        self.plans = 0
        self.received_rolls = []
        self.received_actions = []
        self.hidden = False
        self.closed = False

    async def gate(self, phase):
        """Block at the given phase until released or cancelled."""
        if self.block_phase != phase:
            return
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if self.after_cancel == "failure":
                raise LLMResolutionError("PRIVATE provider details")
            if self.after_cancel != "success":
                raise

    async def generate_initial_state(self):
        """Gate the initial state generation."""
        await self.gate("initial")
        return await super().generate_initial_state()

    async def generate_start_state(self, names):
        """Gate the start state generation."""
        await self.gate("start")
        return await super().generate_start_state(names)

    async def plan_dice(self, actions, current_state=""):
        """Return a dice plan, optionally failing."""
        self.plans += 1
        await self.gate("dice")
        if self.fail_plan:
            raise LLMResolutionError("PRIVATE missing secret check")
        return DicePlan(
            rolls={name: True for name in actions},
            hidden_rolls=list(actions) if self.hidden else [],
        )

    async def generate_resolution(self, actions, dice_results=None, hidden_rolls=None):
        """Return a resolution, optionally failing."""
        self.received_rolls.append(dict(dice_results or {}))
        self.received_actions.append(dict(actions))
        await self.gate("round")
        if self.fail_round:
            raise LLMResolutionError("PRIVATE failure")
        return RoundResolution(
            global_narrative="A breeze rises.",
            player_resolutions={name: f"{name} moves onward." for name in actions},
        )

    async def close(self):
        """Record the close."""
        self.closed = True


async def auth(engine, who):
    """Authenticate a client against the engine."""
    password = settings.server.host_password if who == "host" else settings.server.player_password
    await engine.process_payload(
        who,
        payload(
            "auth",
            name=who.title(),
            password_digest=password_digest(password, who),
            reconnect_token=engine.players[who].reconnect_token if who in engine.players else "",
        ),
    )


async def setup(tmp_path, phase=None):
    """Build an engine advanced to the given phase."""
    resolver = ControlledResolver()
    sender = FakeSender()
    engine = GameEngine(sender, resolver)
    engine.transcript = GameTranscript(tmp_path)
    await auth(engine, "host")
    if phase == "initial":
        resolver.block_phase = phase
    await engine.process_payload(
        "host", payload("scenario_init", scenario="A locked gate", guidance="PRIVATE_TRIGGER")
    )
    if phase == "initial":
        return engine, sender, resolver
    await engine.wait_for_inference()
    await auth(engine, "player")
    if phase == "start":
        resolver.block_phase = phase
    await engine.process_payload("host", payload("start_game"))
    if phase != "start":
        await engine.wait_for_inference()
    return engine, sender, resolver


async def submit_round(engine):
    """Submit a full round of actions."""
    await engine.process_payload("host", payload("action", action="Open gate"))
    await engine.process_payload("player", payload("action", action="Watch Mira"))


@pytest.mark.parametrize("phase", ["initial", "start", "dice", "round"])
@pytest.mark.parametrize("after_cancel", ["cancel", "success", "failure"])
def test_end_during_every_phase_is_terminal_and_chat_is_responsive(tmp_path, phase, after_cancel):
    """Verify ending during any phase is terminal and chat stays responsive."""

    async def run():
        engine, sender, resolver = await setup(tmp_path, phase)
        resolver.after_cancel = after_cancel
        if phase in {"dice", "round"}:
            resolver.block_phase = phase
            await submit_round(engine)
        await asyncio.wait_for(resolver.entered.wait(), 1)
        # Includes the same player whose input triggered inference.
        who = "player" if phase in {"dice", "round"} else "host"
        await asyncio.wait_for(
            engine.process_payload(who, payload("chat", message="Still here")), 1
        )
        assert sender.events_of_type("chat_echo")[-1].payload["chat"] == "Still here"
        await asyncio.wait_for(engine.process_payload("host", payload("end_game")), 1)
        await engine.wait_for_inference()
        assert engine.state is GameState.ENDED
        assert engine.active_player_id is None
        ended = next(i for i, (_, event) in enumerate(sender.events) if event.type == "game_ended")
        assert not any(
            event.type in {"state_update", "turn_directive", "scenario_ready"}
            for _, event in sender.events[(ended + 1) :]
        )
        assert "PRIVATE" not in str(sender.events)
        if engine.transcript.path is not None:
            text = engine.transcript.path.read_text(encoding="utf-8")
            assert text.endswith("</html>\n") and "PRIVATE" not in text

    asyncio.run(run())


def test_failed_plan_pauses_without_unchecked_resolution_and_can_retry(tmp_path):
    """Verify a failed plan pauses and can be retried."""

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        resolver.fail_plan = True
        await submit_round(engine)
        await engine.wait_for_inference()
        assert engine.round_paused
        assert engine.round_buffer == {"host": "Open gate", "player": "Watch Mira"}
        assert not resolver.received_rolls
        assert "PRIVATE" not in str(sender.events)
        resolver.fail_plan = False
        await engine.process_payload("host", payload("retry_round"))
        await engine.wait_for_inference()
        assert engine.round_counter == 1 and not engine.round_paused

    asyncio.run(run())


def test_failed_resolution_retries_identical_rolls_and_keeps_hidden_checks_private(tmp_path):
    """Verify a failed resolution retries with the same rolls and keeps hidden checks private."""

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        resolver.hidden = True
        resolver.fail_round = True
        await submit_round(engine)
        await engine.wait_for_inference()
        assert engine.round_paused
        await engine.process_payload("player", payload("retry_round"))
        assert "Only the host" in sender.events_of_type("error")[-1].payload["msg"]
        resolver.fail_round = False
        await engine.process_payload("host", payload("retry_round"))
        await engine.wait_for_inference()
        assert resolver.plans == 1
        assert resolver.received_rolls[0] == resolver.received_rolls[1]
        assert resolver.received_actions[0] == resolver.received_actions[1]
        assert sender.events_of_type("state_update")[-1].payload["dice_results"] == {}
        text = engine.transcript.path.read_text(encoding="utf-8")
        assert "Dice rolls" not in text and "PRIVATE" not in text
        await engine.shutdown()
        assert resolver.closed

    asyncio.run(run())


@pytest.mark.parametrize("during_inference", [False, True])
@pytest.mark.parametrize("first_return", ["host", "player"])
def test_all_disconnected_resume_exactly_one_turn(tmp_path, during_inference, first_return):
    """Verify a fully disconnected game resumes with exactly one turn."""

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        if during_inference:
            resolver.block_phase = "round"
            await submit_round(engine)
            await resolver.entered.wait()
        await engine.handle_disconnect("player")
        await engine.handle_disconnect("host")
        if during_inference:
            resolver.release.set()
            await engine.wait_for_inference()
        assert engine.active_player_id is None
        before = len(sender.events_of_type("turn_directive"))
        await auth(engine, first_return)
        assert engine.active_player_id == first_return
        assert len(sender.events_of_type("turn_directive")) == before + 1
        await engine.process_payload(first_return, payload("action", action="Return to the gate"))
        await engine.wait_for_inference()
        assert engine.round_counter == (2 if during_inference else 1)
        await engine.shutdown()

    asyncio.run(run())


def test_new_connection_transitions_survive_older_round_commit(tmp_path):
    """Verify a new connection's transitions survive an older round commit."""

    async def run():
        engine, _, resolver = await setup(tmp_path)
        await engine.handle_disconnect("player")
        resolver.block_phase = "round"
        await engine.process_payload("host", payload("action", action="Wait"))
        await resolver.entered.wait()
        version = engine.players["player"].connection_version
        await auth(engine, "player")
        assert engine.players["player"].connection_version > version
        resolver.release.set()
        await engine.wait_for_inference()
        assert engine.players["player"].return_pending
        await submit_round(engine)
        await engine.wait_for_inference()
        assert "in-world return" in resolver.received_actions[-1]["Player"]
        assert not engine.players["player"].return_pending
        await engine.shutdown()

    asyncio.run(run())


def test_rejected_preflight_keeps_active_turn_and_unlocks_client(tmp_path):
    """Verify a rejected preflight keeps the active turn and unlocks the client."""

    async def run():
        engine, sender, resolver = await setup(tmp_path)

        async def reject(actions, state):
            raise LLMResolutionError("Combined actions exceed budget.")

        resolver.preflight_round = reject
        await engine.process_payload("host", payload("action", action="Too much"))
        assert engine.active_player_id == "host" and engine.round_buffer == {}
        assert sender.events[-1][1].type == "turn_directive"
        assert "budget" in sender.events_of_type("error")[-1].payload["msg"]
        await engine.shutdown()

    asyncio.run(run())


def test_end_waits_for_committed_transcript_write_then_finalizes(tmp_path):
    """Verify ending waits for a committed transcript write before finalizing."""

    async def run():
        engine, sender, _ = await setup(tmp_path)
        entered, release = threading.Event(), threading.Event()
        original = engine.transcript._append

        def blocked(text):
            if "<article>" in text:
                entered.set()
                assert release.wait(3)
            original(text)

        engine.transcript._append = blocked
        await submit_round(engine)
        assert await asyncio.to_thread(entered.wait, 2)
        ending = asyncio.create_task(engine.process_payload("host", payload("end_game")))
        await engine.process_payload("host", payload("chat", message="Still responsive"))
        release.set()
        await asyncio.wait_for(ending, 2)
        text = engine.transcript.path.read_text(encoding="utf-8")
        assert text.index("<article>") < text.index("</main>")
        assert text.endswith("</html>\n") and text.count("</html>") == 1
        assert engine.state is GameState.ENDED
        ended = [event.type for _, event in sender.events]
        assert ended.index("game_ended") > ended.index("state_update")

    asyncio.run(run())


def test_cancelled_transcript_write_cannot_be_overtaken_by_finalization(tmp_path):
    """Verify a cancelled transcript write cannot be overtaken by finalization."""

    async def run():
        transcript = GameTranscript(tmp_path)
        await transcript.start("Gate", "Opening")
        entered, release = threading.Event(), threading.Event()
        original = transcript._append

        def blocked(text):
            if "<article>" in text:
                entered.set()
                assert release.wait(3)
            original(text)

        transcript._append = blocked
        writing = asyncio.create_task(
            transcript.append_round(
                1,
                {"Alice": "wait"},
                RoundResolution(
                    global_narrative="after", player_resolutions={"Alice": "Alice waits"}
                ),
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        writing.cancel()
        ending = asyncio.create_task(transcript.finalize())
        release.set()
        await asyncio.gather(writing, return_exceptions=True)
        await ending
        text = transcript.path.read_text(encoding="utf-8")
        assert text.endswith("</html>\n")
        with pytest.raises(RuntimeError, match="finalized"):
            await transcript.append_round(
                2, {}, RoundResolution(global_narrative="after", player_resolutions={})
            )

    asyncio.run(run())


def test_disconnect_triggered_round_reports_backend_outage_and_retries(tmp_path, monkeypatch):
    """Idle injection may trigger work, but an outage must preserve the round and explain why."""
    from logic.llm_manager import LLMBackendUnavailableError

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        original_plan = resolver.plan_dice

        async def unavailable(actions, current_state=""):
            raise LLMBackendUnavailableError("PRIVATE provider details")

        monkeypatch.setattr(resolver, "plan_dice", unavailable)
        await engine.process_payload("host", payload("action", action="Look around"))
        await engine.handle_disconnect("player")
        await engine.wait_for_inference()
        assert engine.round_paused
        assert len(engine.round_buffer) == 2
        message = sender.events_of_type("error")[-1].payload["msg"]
        assert "Could not connect to the LLM backend" in message
        assert "PRIVATE" not in message
        monkeypatch.setattr(resolver, "plan_dice", original_plan)
        await engine.process_payload("host", payload("retry_round"))
        await engine.wait_for_inference()
        assert engine.round_counter == 1
        assert not engine.round_paused

    asyncio.run(run())
