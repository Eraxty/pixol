"""Reminders that survive restarts: stored in reminders.json, posted back into the thread when due."""

import asyncio
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import config


def _load() -> list[dict]:
    try:
        return json.loads(config.REMINDERS_FILE.read_text())
    except Exception:
        return []


_items: list[dict] = _load()


def _save():
    config.REMINDERS_FILE.write_text(json.dumps(_items, indent=1))


def _when(in_minutes: float | None, at: str | None) -> float:
    if in_minutes is not None:
        if not 0 < in_minutes <= 60 * 24 * 60:
            raise ValueError("pick something between now and 60 days out")
        return time.time() + in_minutes * 60
    if at:
        tz = ZoneInfo(config.TZ)
        t = datetime.fromisoformat(at.strip())
        t = t if t.tzinfo else t.replace(tzinfo=tz)
        if t.timestamp() <= time.time():
            raise ValueError(f"{at} is already in the past")
        return t.timestamp()
    raise ValueError("need in_minutes or at")


def add(user: str, channel: str, thread: str, text: str,
        in_minutes: float | None = None, at: str | None = None) -> str:
    if sum(r["user"] == user for r in _items) >= 20:
        raise ValueError("you already have 20 reminders pending, cancel some first")
    due = _when(in_minutes, at)
    rid = uuid.uuid4().hex[:6]
    _items.append({"id": rid, "user": user, "channel": channel, "thread": thread,
                   "text": text.strip()[:500], "due": due})
    _save()
    local = datetime.fromtimestamp(due, ZoneInfo(config.TZ))
    return f"reminder {rid} set for {local:%a %d %b %H:%M} ({config.TZ})"


def list_for(user: str) -> str:
    mine = sorted((r for r in _items if r["user"] == user), key=lambda r: r["due"])
    if not mine:
        return "no pending reminders"
    tz = ZoneInfo(config.TZ)
    return "\n".join(f"{r['id']}: {datetime.fromtimestamp(r['due'], tz):%a %d %b %H:%M} — {r['text']}" for r in mine)


def cancel(user: str, rid: str) -> str:
    """Only the person who set a reminder can cancel it."""
    before = len(_items)
    _items[:] = [r for r in _items if not (r["user"] == user and (rid == "all" or r["id"] == rid))]
    _save()
    n = before - len(_items)
    return f"cancelled {n}" if n else "no reminder of yours with that id"


async def loop(post):
    """post(channel, thread, text) is awaited for each reminder as it comes due."""
    while True:
        now = time.time()
        due = [r for r in _items if r["due"] <= now]
        for r in due:
            try:
                await post(r["channel"], r["thread"], f"<@{r['user']}> reminder: {r['text']}")
            except Exception as e:  # noqa: BLE001
                print(f"[pixol] reminder {r['id']} failed: {e}", flush=True)
            _items.remove(r)
        if due:
            _save()
        await asyncio.sleep(15)
