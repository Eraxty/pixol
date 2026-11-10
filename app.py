"""pixol — a Slack helper bot with its own browser.

Triggers: @pixol mention, the keyword "pixol" in a channel, or any DM.
"""

import asyncio
import json
import re
import time
import traceback

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

import brain
import config
import reminders
import sandbox
from browser import browser
from live import LiveReply

config.validate()

app = AsyncApp(token=config.SLACK_BOT_TOKEN)
BOT_ID: str | None = None
MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


async def clean_text(client, text: str) -> tuple[str, dict[str, str]]:
    """Drop the bot's own mention; turn other <@U..> into @Name so the model
    knows who is being talked about. Returns (text, {uid: name})."""
    people: dict[str, str] = {}
    for uid in set(MENTION_RE.findall(text)):
        if uid != BOT_ID:
            people[uid] = await name_of(client, uid)

    # a leading "@pixol" is just how people summon him; anywhere else it's
    # part of the sentence ("what on earth is @pixol") so keep it as his name
    text = re.sub(rf"^\s*<@{BOT_ID}(?:\|[^>]*)?>[\s,:]*", "", text) if BOT_ID else text

    def sub(m):
        uid = m.group(1)
        return f"@{config.BOT_NAME}" if uid == BOT_ID else f"@{people.get(uid, uid)}"

    return re.sub(r"\s+", " ", MENTION_RE.sub(sub, text)).strip(), people


async def _reaction(client, channel, ts, name, add=True):
    try:
        if add:
            await client.reactions_add(channel=channel, timestamp=ts, name=name)
        else:
            await client.reactions_remove(channel=channel, timestamp=ts, name=name)
    except Exception:
        pass


_names: dict[str, str] = {}
_aliases: dict[str, set[str]] = {}  # uid -> every name they go by, lowercased (for search)


async def name_of(client, uid: str) -> str:
    if uid not in _names:
        try:
            u = (await client.users_info(user=uid))["user"]
            p = u.get("profile", {})
            _names[uid] = p.get("display_name") or p.get("real_name") or u.get("name") or uid
            _aliases[uid] = {n.lower() for n in (p.get("display_name"), p.get("real_name"), u.get("name")) if n}
        except Exception:
            _names[uid] = uid
    return _names[uid]


WORKSPACE_URL = ""  # https://<team>.slack.com/, from auth_test


async def _resolve_channel(client, name: str) -> str | None:
    name = name.strip().lstrip("#").lower()
    if re.fullmatch(r"[CG][A-Z0-9]{6,}", name.upper()):
        return name.upper()
    cursor = None
    while True:
        r = await client.conversations_list(types="public_channel,private_channel", exclude_archived=True,
                                            limit=1000, cursor=cursor)
        for c in r["channels"]:
            if c["name"].lower() == name:
                return c["id"]
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return None


async def search_slack(client, here: str, query: str = "", from_user: str = "", channel_name: str = "") -> str:
    """Scan a channel's recent messages (and their threads) for words and/or an author."""
    note = ""
    try:
        channel = await _resolve_channel(client, channel_name) if channel_name else here
    except Exception:  # no channels:read scope: can't map names to ids, so search where we are
        channel, note = here, f" (couldn't look up #{channel_name.lstrip('#')} by name, so searched this channel)"
    if not channel:
        return f"no channel called #{channel_name.lstrip('#')} that i can see"
    words = [w for w in query.lower().split() if w]
    who = from_user.lower().lstrip("@")
    found, scanned, threads = [], 0, 0
    pages, cursor = 0, None
    try:
        while pages < config.SEARCH_PAGES:
            r = await client.conversations_history(channel=channel, limit=200, cursor=cursor)
            pages += 1
            for m in r["messages"]:
                batch = [m]
                if m.get("reply_count") and threads < config.SEARCH_THREADS:
                    threads += 1
                    try:
                        rr = await client.conversations_replies(channel=channel, ts=m["ts"], limit=200)
                        batch += rr["messages"][1:]
                    except Exception:
                        pass
                for msg in batch:
                    scanned += 1
                    uid = msg.get("user")
                    if not uid or not msg.get("text"):
                        continue
                    author = await name_of(client, uid)
                    if who and not any(who in a for a in _aliases.get(uid, {author.lower()})):
                        continue
                    text, _ = await clean_text(client, msg["text"])
                    if words and not all(w in text.lower() for w in words):
                        continue
                    found.append((float(msg["ts"]), author, text, msg))
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception as e:  # noqa: BLE001
        if not scanned:
            return f"couldn't read that channel (am i in it?): {e}"
    found.sort(key=lambda f: f[0], reverse=True)
    lines = []
    for ts, author, text, msg in found[: config.SEARCH_MAX_HITS]:
        when = time.strftime("%d %b %H:%M", time.localtime(ts))
        link = f"{WORKSPACE_URL}archives/{channel}/p{msg['ts'].replace('.', '')}"
        if msg.get("thread_ts") and msg["thread_ts"] != msg["ts"]:
            link += f"?thread_ts={msg['thread_ts']}&cid={channel}"
        lines.append(f"{when} · {author}: {text[:200]} · <{link}|link>")
    more = f" (showing newest {config.SEARCH_MAX_HITS})" if len(found) > config.SEARCH_MAX_HITS else ""
    head = f"{len(found)} matches in <#{channel}> out of the {scanned} newest messages{more}{note}"
    return head + ("\n" + "\n".join(lines) if lines else "")


async def recent_context(client, channel: str, ts: str, thread_ts: str | None,
                         limit: int = config.CONTEXT_MSGS, body_cap: int = 1500):
    """Last few messages around this one, as 'Name: text' lines + {uid: name}."""
    try:
        if thread_ts:
            msgs, cursor = [], None
            while True:  # a long thread spans pages; keep the newest `limit`
                r = await client.conversations_replies(channel=channel, ts=thread_ts, limit=200, cursor=cursor)
                msgs += r["messages"]
                cursor = (r.get("response_metadata") or {}).get("next_cursor")
                if not cursor or len(msgs) >= 1000:
                    break
        else:
            r = await client.conversations_history(channel=channel, latest=ts, inclusive=False, limit=limit)
            msgs = list(reversed(r["messages"]))
    except Exception:
        return "", {}
    msgs = [m for m in msgs if m.get("ts") != ts and m.get("text")][-limit:]
    lines, people = [], {}
    for m in msgs:
        if m.get("user") == BOT_ID or m.get("bot_id"):
            who = config.BOT_NAME if m.get("user") == BOT_ID else "some bot"
        else:
            who = await name_of(client, m.get("user", "?"))
            people[m.get("user", "?")] = who
        body, _ = await clean_text(client, m["text"])
        body = re.sub(r"<https?://[^>]+>", "[link]", body)
        lines.append(f"{who}: {body[:body_cap]}")
    return "\n".join(lines), people


_muted_until: dict[str, float] = {}
SHUSH_RE = re.compile(rf"^\W*(?:@?{re.escape(config.BOT_NAME)}\W*)?(?:shut\s*up|stfu|be\s+quiet|shush|go\s+to\s+sleep|mute)\b", re.I)
WAKE_RE = re.compile(rf"^\W*(?:@?{re.escape(config.BOT_NAME)}\W*)?(?:wake\s*up|unmute|come\s+back|you\s+can\s+talk)\b", re.I)


async def handle(event: dict, client, trigger: str = "mention"):
    text, mentioned = await clean_text(client, event.get("text", ""))
    if not text:
        return

    channel = event["channel"]
    if WAKE_RE.search(text) and _muted_until.get(channel, 0) > time.monotonic():
        _muted_until.pop(channel, None)
        await client.chat_postMessage(channel=channel, thread_ts=event.get("thread_ts") or event["ts"], text="i'm back")
        return
    if SHUSH_RE.search(text):
        _muted_until[channel] = time.monotonic() + config.MUTE_MINUTES * 60
        log(f"muted in {channel} for {config.MUTE_MINUTES}m")
        await _reaction(client, channel, event["ts"], "zipper_mouth_face", True)
        return
    if _muted_until.get(channel, 0) > time.monotonic():
        if trigger not in ("mention", "dm"):
            return
        _muted_until.pop(channel, None)  # a direct @ means they want him back
        log(f"unmuted in {channel} by mention")
    ts = event["ts"]
    thread_ts = event.get("thread_ts")
    thread = thread_ts or ts
    # top-level chatter shares one memory per channel; threads get their own
    key = f"{channel}:{thread_ts}" if thread_ts else channel

    uid = event.get("user", "?")
    # "damn" + "it did it" two seconds apart should get one reply, not two
    _latest[key] = ts
    if trigger in ("followup", "name"):
        await asyncio.sleep(config.FOLLOWUP_DEBOUNCE)
        if _latest.get(key) != ts:
            log(f"skipped {ts}: newer message in {key}")
            return
    async with _locks.setdefault(key, asyncio.Lock()):
        if trigger == "followup" and _latest.get(key) != ts:
            return  # someone spoke while we waited; answer them instead
        await _respond(event, client, trigger, text, mentioned, channel, ts, thread_ts, thread, key, uid)


_latest: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _respond(event, client, trigger, text, mentioned, channel, ts, thread_ts, thread, key, uid):
    eyes = trigger != "followup"
    if eyes:
        await _reaction(client, channel, ts, "eyes", True)
    live = LiveReply(client, channel, thread, eager=eyes)

    async def history(limit: int) -> str:
        lines, _ = await recent_context(client, channel, ts, thread_ts, limit=limit, body_cap=2000)
        return lines or "couldn't read the conversation"

    async def search(query: str = "", from_user: str = "", channel_name: str = "") -> str:
        return await search_slack(client, channel, query, from_user, channel_name)

    hooks = {"status": live.set_status, "stream": live.set_text, "history": history, "search": search,
             "channel": channel, "thread": thread}
    try:
        await live.start()
        speaker = await name_of(client, uid)
        recent, people = await recent_context(client, channel, ts, thread_ts)
        people = {**people, **mentioned}  # facts about mentioned people get loaded too
        reply = await brain.respond(text, key, uid, speaker, recent, people, trigger, hooks)
    except Exception:
        await live.discard()
        raise
    finally:
        if eyes:
            await _reaction(client, channel, ts, "eyes", False)

    if await live.finish(reply):  # empty = he decided this one didn't need him
        track_thread(channel, thread)


def log(msg: str) -> None:
    print(f"[pixol] {msg}", flush=True)


@app.event("app_mention")
async def on_mention(event, client):
    log(f"app_mention in {event.get('channel')}")
    await handle(event, client)


_last_keyword: dict[str, float] = {}

# threads pixol has replied in -> last activity (epoch). He keeps listening there
# so people can follow up without saying his name; the model SKIPs what isn't for him.
THREADS_FILE = config.BASE_DIR / "threads.json"


def _load_threads() -> dict[str, float]:
    try:
        return json.loads(THREADS_FILE.read_text())
    except Exception:
        return {}


_threads: dict[str, float] = _load_threads()


def track_thread(channel: str, thread_ts: str):
    now = time.time()
    _threads[f"{channel}:{thread_ts}"] = now
    for k in [k for k, t in _threads.items() if now - t > config.FOLLOWUP_HOURS * 3600]:
        del _threads[k]
    THREADS_FILE.write_text(json.dumps(_threads))


def in_my_thread(channel: str, thread_ts: str | None) -> bool:
    t = _threads.get(f"{channel}:{thread_ts}") if thread_ts else None
    return bool(t) and time.time() - t < config.FOLLOWUP_HOURS * 3600


def says_name(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in config.KEYWORDS)


@app.event("message")
async def on_message(event, client):
    if event.get("bot_id") or event.get("subtype"):
        return
    text = event.get("text") or ""
    if BOT_ID and f"<@{BOT_ID}>" in text:
        return  # app_mention already owns this one
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")

    if event.get("channel_type") == "im":
        trigger = "dm"
    elif says_name(text):
        trigger = "name"
        spot = f"{channel}:{thread_ts or ''}"
        now = time.monotonic()
        if now - _last_keyword.get(spot, -1e9) < config.KEYWORD_COOLDOWN:
            log(f"name in {channel} skipped (cooldown)")
            return
        _last_keyword[spot] = now
    elif in_my_thread(channel, thread_ts):
        trigger = "followup"
    else:
        return
    log(f"message in {channel} trigger={trigger}")
    await handle(event, client, trigger)


@app.error
async def on_error(error, body):
    log(f"error: {error}")
    print(body, flush=True)


async def main():
    global BOT_ID, WORKSPACE_URL
    info = await app.client.auth_test()
    BOT_ID = info.get("user_id")
    WORKSPACE_URL = info.get("url", "")
    log(f"logged in as {info.get('user')} ({BOT_ID}) · model {config.LLM_MODEL}")
    log(f"keywords: {config.KEYWORDS} · listening on Socket Mode")

    async def post_reminder(channel: str, thread: str, text: str):
        await app.client.chat_postMessage(channel=channel, thread_ts=thread, text=text)

    background = [asyncio.create_task(reminders.loop(post_reminder))]
    if config.SANDBOX_ENABLED:
        background.append(asyncio.create_task(sandbox.janitor()))
        log(f"sandbox on: {config.SANDBOX_IMAGE}, network={config.SANDBOX_NETWORK}")

    handler = AsyncSocketModeHandler(app, config.SLACK_APP_TOKEN)
    try:
        await handler.start_async()
    finally:
        await browser.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[pixol] bye", flush=True)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
