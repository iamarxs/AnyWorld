"""Public replay excludes private data and action IDs are idempotent."""

import asyncio
import json
from pathlib import Path

from core.schemas import ServerEvent
from logic.journal import PublicJournal
from test_engine import build_started_game, payload


def test_journal_projection_pagination_and_search():
    async def run():
        journal = PublicJournal("test-session")
        for number in range(3):
            await journal.record(
                ServerEvent(
                    type="state_update",
                    payload={
                        "round_number": number + 1,
                        "global_narrative": f"Gate {number}",
                        "private_guidance": "secret",
                        "hidden_rolls": {"Alice": 42},
                        "dice_results": {"Bob": 42},
                    },
                )
            )
        first = await journal.page(0, 2, "")
        assert first["has_more"] and first["cursor"] == 2
        second = await journal.page(first["cursor"], 2, "")
        assert len(second["events"]) == 1 and not second["has_more"]
        assert len((await journal.page(0, 100, "Gate 1"))["events"]) == 1
        exported = json.dumps(first["events"] + second["events"])
        assert "secret" not in exported
        assert "hidden_rolls" not in exported

    asyncio.run(run())


def test_duplicate_action_acknowledgment_and_complete_snapshot(tmp_path):
    async def run():
        engine, sender, _ = await build_started_game(tmp_path)
        action = payload(
            "action",
            action="Wait",
            action_id="attempt-1",
            session_id=engine.session_id,
            round_number=1,
        )
        await engine.process_payload("host", action)
        before = dict(engine.round_buffer)
        await engine.process_payload("host", action)
        assert engine.round_buffer == before
        await engine.process_payload("player", payload("action", action="Walk"))
        await engine.wait_for_inference()
        snapshot = engine._snapshot_locked(engine.players["host"])
        assert snapshot["latest_round"]["round_number"] == 1
        assert "dice_results" in snapshot["latest_round"]
        await engine.process_payload("host", action)
        assert engine.round_buffer == {}
        assert any(event.type == "action_accepted" for _, event in sender.events)
        await engine.shutdown()

    asyncio.run(run())


def test_full_history_is_retained_without_creating_files(tmp_path, monkeypatch):
    def forbid_files(*args, **kwargs):
        raise AssertionError("Public history must not access files")

    monkeypatch.setattr(Path, "open", forbid_files)
    monkeypatch.setattr(Path, "mkdir", forbid_files)

    async def run():
        journal = PublicJournal("memory-session")
        for number in range(3501):
            await journal.record(
                ServerEvent(type="chat_echo", payload={"chat": f"{number}:" + "x" * 1000})
            )
        identities = []
        after = 0
        while True:
            page = await journal.page(after, 100, "")
            identities.extend(event["payload"]["event_id"] for event in page["events"])
            assert not page["incomplete"]
            assert page["latest_event_id"] == 3501
            after = page["cursor"]
            if not page["has_more"]:
                break
        assert identities == list(range(1, 3502))
        assert not (tmp_path / ".public_games").exists()

    asyncio.run(run())


def test_record_retries_are_idempotent_and_pages_are_independent():
    async def run():
        journal = PublicJournal("retry-session")
        event = ServerEvent(type="chat_echo", payload={"name": "Alice", "chat": "Hello"})
        await journal.record(event)
        await journal.record(event)
        page = await journal.page(0, 100, "")
        assert len(page["events"]) == page["latest_event_id"] == 1
        page["events"][0]["payload"]["chat"] = "Changed"
        event.payload["chat"] = "Changed too"
        assert (await journal.page(0, 100, ""))["events"][0]["payload"]["chat"] == "Hello"
        # A new journal cannot recover a previous game's in-memory history.
        assert (await PublicJournal("retry-session").page(0, 100, ""))["events"] == []

    asyncio.run(run())


def test_pages_stay_bounded_by_bytes():
    async def run():
        journal = PublicJournal("large-events")
        for _ in range(2):
            await journal.record(
                ServerEvent(type="state_update", payload={"global_narrative": "x" * 300000})
            )
        first = await journal.page(0, 100, "")
        assert len(first["events"]) == 1 and first["has_more"] and first["cursor"] == 1
        second = await journal.page(first["cursor"], 100, "")
        assert len(second["events"]) == 1 and not second["has_more"]

    asyncio.run(run())


def test_search_filtering_and_empty_new_journal_are_complete():
    async def run():
        journal = PublicJournal("filtered-history")
        assert not (await journal.page(0, 1, ""))["incomplete"]
        for number in range(4):
            await journal.record(ServerEvent(type="chat_echo", payload={"chat": str(number)}))
        page = await journal.page(0, 1, "no matches")
        assert not page["incomplete"] and page["cursor"] == 4

    asyncio.run(run())


def test_history_export_requests_work_after_end_without_public_files(tmp_path):
    async def run():
        engine, sender, _ = await build_started_game(tmp_path)
        await engine.process_payload("host", payload("chat", message="Export this chat"))
        await engine.shutdown()
        await engine.process_payload(
            "host", payload("journal_request", mode="export", after=0, limit=100, search="")
        )
        page = sender.events_of_type("journal_page")[-1].payload
        assert page["mode"] == "export" and not page["has_more"]
        assert page["session_id"] == engine.session_id
        assert page["events"][-1]["type"] == "game_ended"
        assert any(event["payload"].get("chat") == "Export this chat" for event in page["events"])
        assert not (tmp_path / ".public_games").exists()
        assert engine.transcript.path.is_file()

    asyncio.run(run())
