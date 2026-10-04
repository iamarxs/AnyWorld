"""Offline regressions for the October review's transaction recovery findings."""

import asyncio
import time

import pytest

from core.config import settings
from core.schemas import RoundResolution
from logic.engine import GameEngine, GameState
from logic.transcript import GameTranscript
from support import FakeSender
from test_engine import payload
from test_priority_one_lifecycle import ControlledResolver, auth, setup, submit_round


@pytest.mark.parametrize("phase", ["transcript", "debug", "journal", "journal_chat", "debug_error"])
def test_committed_round_delivery_survives_prepublication_failure(tmp_path, monkeypatch, phase):
    async def run():
        engine, sender, resolver = await setup(tmp_path)
        settings.llm.request_timeout_seconds = 0.02
        entered = asyncio.Event()
        commits = []
        original_commit = resolver.commit_resolution

        def commit(prepared):
            commits.append(prepared)
            original_commit(prepared)

        monkeypatch.setattr(resolver, "commit_resolution", commit)
        if phase == "transcript":
            append = engine.transcript._append

            def delayed(text):
                time.sleep(0.12)
                append(text)

            monkeypatch.setattr(engine.transcript, "_append", delayed)
        elif phase.startswith("debug"):

            async def debug(*args):
                if phase == "debug_error":
                    raise RuntimeError("Optional diagnostics failed")
                await asyncio.Event().wait()

            monkeypatch.setattr(resolver, "complete_round_debug", debug)
        else:
            record = engine.journal.record

            async def delayed(event):
                recorded = await record(event)
                if event.type == "state_update" and event.payload.get("round_number") == 1:
                    entered.set()
                    await asyncio.sleep(0.12)
                return recorded

            monkeypatch.setattr(engine.journal, "record", delayed)
        await submit_round(engine)
        chat = None
        if phase == "journal_chat":
            await asyncio.wait_for(entered.wait(), 1)
            chat = asyncio.create_task(
                engine.process_payload("host", payload("chat", message="During write"))
            )
        await asyncio.wait_for(engine.wait_for_inference(), 2)
        if chat is not None:
            await chat
        assert engine.round_counter == 1 and engine.state is GameState.ACTIVE_TURN
        assert not engine.round_paused and not engine.pending_delivery
        assert len(commits) == resolver.plans == len(resolver.received_rolls) == 1
        delivered = [event for _, event in sender.events]
        result_index = next(
            index
            for index, event in enumerate(delivered)
            if event.type == "state_update" and event.payload.get("round_number") == 1
        )
        next_index = next(
            index
            for index, event in enumerate(delivered)
            if event.type == "turn_directive" and event.payload.get("round_number") == 2
        )
        assert result_index < next_index
        if phase == "journal_chat":
            chat_index = next(
                index
                for index, event in enumerate(delivered)
                if event.type == "chat_echo" and event.payload["chat"] == "During write"
            )
            assert result_index < chat_index
            identities = [
                event.payload["event_id"] for event in delivered if "event_id" in event.payload
            ]
            assert identities == sorted(set(identities))
        page = await engine.journal.page(0, 100, "")
        results = [
            event
            for event in page["events"]
            if event["type"] == "state_update" and event["payload"].get("round_number") == 1
        ]
        assert len(results) == 1
        assert results[0]["payload"]["event_id"] == delivered[result_index].payload["event_id"]
        assert "PRIVATE" not in str(results)
        await engine.shutdown()

    asyncio.run(run())


def test_timed_out_opening_write_is_discarded_and_start_can_retry(tmp_path, monkeypatch):
    async def run():
        resolver = ControlledResolver()
        engine = GameEngine(FakeSender(), resolver)
        engine.transcript = GameTranscript(tmp_path)
        await auth(engine, "host")
        await engine.process_payload("host", payload("scenario_init", scenario="A quiet room"))
        await engine.wait_for_inference()
        openings = []

        async def opening(names):
            text = f"Opening attempt {len(openings) + 1}"
            openings.append(text)
            return RoundResolution(
                global_narrative=text, player_resolutions={name: text for name in names}
            )

        monkeypatch.setattr(resolver, "generate_start_state", opening)
        create = engine.transcript._create

        def delayed(*args):
            time.sleep(0.12)
            return create(*args)

        monkeypatch.setattr(engine.transcript, "_create", delayed)
        settings.llm.request_timeout_seconds = 0.02
        await engine.process_payload("host", payload("start_game"))
        await asyncio.wait_for(engine.wait_for_inference(), 2)
        assert engine.state is GameState.AWAITING_PLAYERS
        assert engine.opening_scenario is None and engine.transcript.path is None
        assert not list(tmp_path.glob("*.html"))
        monkeypatch.setattr(engine.transcript, "_create", create)
        settings.llm.request_timeout_seconds = 120.0
        await engine.process_payload("host", payload("start_game"))
        await engine.wait_for_inference()
        assert engine.state is GameState.ACTIVE_TURN
        assert engine.opening_scenario == "Opening attempt 2"
        assert len(list(tmp_path.glob("*.html"))) == 1
        text = engine.transcript.path.read_text(encoding="utf-8")
        assert "Opening attempt 2" in text and "Opening attempt 1" not in text
        await engine.shutdown()

    asyncio.run(run())
