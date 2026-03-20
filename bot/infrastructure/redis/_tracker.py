"""Трекер, анонимные сообщения, changelog."""

from __future__ import annotations

import json


class TrackerStoreMixin:

    # ── Анонимные сообщения ───────────────────────────────

    async def anon_register_chat(self, chat_id: int, title: str) -> None:
        await self._r.hset("anon:chats", str(chat_id), title)

    async def anon_get_chats(self) -> list[tuple[int, str]]:
        raw = await self._r.hgetall("anon:chats")
        return [(int(k), v) for k, v in raw.items()]

    async def anon_get_state(self, user_id: int) -> dict | None:
        raw = await self._r.get(f"anon:state:{user_id}")
        return json.loads(raw) if raw else None

    async def anon_set_state(self, user_id: int, state: dict, ttl: int = 300) -> None:
        await self._r.set(f"anon:state:{user_id}", json.dumps(state, ensure_ascii=False), ex=ttl)

    async def anon_clear_state(self, user_id: int) -> None:
        await self._r.delete(f"anon:state:{user_id}")

    # ── Трекер: привязка чатов ────────────────────────────

    async def tracker_set_source(self, source_chat_id: int, tracker_chat_id: int) -> None:
        await self._r.set(f"tracker:source:{source_chat_id}", str(tracker_chat_id))

    async def tracker_get_tracker_id(self, source_chat_id: int) -> int | None:
        raw = await self._r.get(f"tracker:source:{source_chat_id}")
        return int(raw) if raw else None

    async def tracker_set_topic(self, tracker_chat_id: int, topic_type: str, thread_id: int) -> None:
        await self._r.set(f"tracker:topic:{tracker_chat_id}:{topic_type}", str(thread_id))

    async def tracker_get_topic(self, tracker_chat_id: int, topic_type: str) -> int | None:
        raw = await self._r.get(f"tracker:topic:{tracker_chat_id}:{topic_type}")
        return int(raw) if raw else None

    async def tracker_next_id(self) -> int:
        return int(await self._r.incr("tracker:counter"))

    # ── Changelog ─────────────────────────────────────────

    _CHANGELOG_MAX = 20

    async def changelog_add(self, tracker_chat_id: int, message_id: int, text: str, date: str) -> None:
        key = f"changelog:{tracker_chat_id}"
        raw = await self._r.get(key)
        entries: list[dict] = json.loads(raw) if raw else []
        entries = [e for e in entries if e["message_id"] != message_id]
        entries.insert(0, {"message_id": message_id, "text": text, "date": date})
        entries = entries[: self._CHANGELOG_MAX]
        await self._r.set(key, json.dumps(entries, ensure_ascii=False))

    async def changelog_remove(self, tracker_chat_id: int, message_id: int) -> None:
        key = f"changelog:{tracker_chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return
        entries = [e for e in json.loads(raw) if e["message_id"] != message_id]
        await self._r.set(key, json.dumps(entries, ensure_ascii=False))

    async def changelog_get_all(self, tracker_chat_id: int) -> list[dict]:
        raw = await self._r.get(f"changelog:{tracker_chat_id}")
        return json.loads(raw) if raw else []

    async def changelog_scan_all(self) -> list[tuple[int, list[dict]]]:
        result: list[tuple[int, list[dict]]] = []
        async for key in self._r.scan_iter("changelog:*"):
            raw = await self._r.get(key)
            if not raw:
                continue
            tracker_chat_id = int(key.split(":", 1)[1])
            entries = json.loads(raw)
            if entries:
                result.append((tracker_chat_id, entries))
        return result
