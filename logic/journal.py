"""Player-safe, in-memory event history for replay, search and browser export."""

import asyncio
import json

from core.schemas import ServerEvent

PUBLIC_FIELDS = {
    "state_update": {
        "title",
        "scenario_title",
        "global_narrative",
        "player_resolutions",
        "round_number",
        "submitted_actions",
        "player_order",
        "dice_results",
        "original_scenario",
    },
    "action_echo": {"round_number", "player_name", "player_color_index", "action", "action_id"},
    "round_start": {"round_number"},
    "game_ended": {"msg"},
    "chat_echo": {"name", "chat"},
}


class PublicJournal:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.cursor = 0
        self.lock = asyncio.Lock()
        self._events: list[str] = []

    async def record(self, event: ServerEvent) -> ServerEvent:
        fields = PUBLIC_FIELDS.get(event.type)
        if fields is None:
            return event
        async with self.lock:
            # A cancelled delivery may retry an already recorded event.
            if event.payload.get("session_id") == self.session_id and isinstance(
                event.payload.get("event_id"), int
            ):
                return event
            self.cursor += 1
            payload = {key: value for key, value in event.payload.items() if key in fields}
            payload.update(session_id=self.session_id, event_id=self.cursor)
            public = ServerEvent(type=event.type, payload=payload)
            event.payload = payload
            self._events.append(public.model_dump_json())
            return public

    async def page(self, after: int, limit: int, search: str) -> dict[str, object]:
        async with self.lock:
            events = []
            cursor = after
            size = 0
            more = False
            search = search.casefold()
            for index in range(after, len(self._events)):
                line = self._events[index]
                matches = not search or search in line.casefold()
                if matches and (
                    len(events) == limit or (events and size + len(line.encode("utf-8")) > 524288)
                ):
                    more = True
                    break
                cursor = index + 1
                if matches:
                    events.append(json.loads(line))
                    size += len(line.encode("utf-8"))
            return {
                "session_id": self.session_id,
                "events": events,
                "cursor": cursor,
                "has_more": more,
                "latest_event_id": self.cursor,
                "incomplete": False,
            }
