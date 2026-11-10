"""Check that pixol is actually wired up. Run: python check.py"""

import asyncio
import sys

from slack_sdk import WebClient

import config

REQUIRED = {
    "app_mentions:read": "reply to @mentions",
    "chat:write": "send messages",
    "channels:history": "read public channel messages",
    "reactions:write": "add the thinking reaction",
    "im:history": "read direct messages",
    "groups:history": "read private channel messages",
    "files:write": "upload page screenshots",
}

# Set to a channel name to have the doctor report on that specific channel.
BOT_CHANNEL = "pixol-test"


def ok(msg):
    print(f"  \033[32mPASS\033[0m  {msg}")


def bad(msg):
    print(f"  \033[31mFAIL\033[0m  {msg}")


def warn(msg):
    print(f"  \033[33mWARN\033[0m  {msg}")


def slack(method, token, **params):
    """Returns (data, scopes_header) using the official SDK."""
    client = WebClient(token=token, timeout=20)
    fn = getattr(client, method.replace(".", "_"), None)
    if fn is None:
        fn = client
        for part in method.split("."):
            fn = getattr(fn, part)
    try:
        resp = fn(**params)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}, ""
    headers = getattr(resp, "headers", None) or {}
    scopes = ""
    for k, v in headers.items():
        if k.lower() == "x-oauth-scopes":
            scopes = v
    return dict(resp.data), scopes


def main():
    print("\npixol doctor\n")

    print("tokens")
    try:
        config.validate()
        ok("both tokens present and correct prefix")
    except SystemExit as e:
        bad(str(e).splitlines()[0])
        return 1

    auth, scopes_raw = slack("auth_test", config.SLACK_BOT_TOKEN)
    if not auth.get("ok"):
        bad(f"bot token rejected by Slack: {auth.get('error')}")
        return 1
    ok(f"@{auth['user']} in {auth['team']}  (user {auth['user_id']})")

    print("\nscopes")
    have = {s for s in scopes_raw.split(",") if s}
    if not have:
        bad("token exposes no scopes header — cannot verify, treat as broken")
    else:
        for scope, why in REQUIRED.items():
            (ok if scope in have else bad)(f"{scope:20} {why}")
        extra = have - set(REQUIRED)
        if extra:
            print(f"        (also granted: {', '.join(sorted(extra))})")
        missing = [s for s in REQUIRED if s not in have]
        if missing:
            print("\n  Add the missing scopes, then REINSTALL to Workspace:")
            print("  OAuth & Permissions -> Scopes -> Bot Token Scopes -> Install to Workspace")
            print("  Then paste the NEW bot token (it changes on reinstall).")

    print("\nchannels")
    data, _ = slack(
        "conversations.list",
        config.SLACK_BOT_TOKEN,
        types="public_channel",
        limit=1000,
        exclude_archived="true",
    )
    if not data.get("ok"):
        warn(f"cannot list channels: {data.get('error')}")
    else:
        channels = data["channels"]
        joined = [c for c in channels if c.get("is_member")]
        print(f"  {len(channels)} PUBLIC channels visible, {len(joined)} joined")
        for c in joined:
            print(f"      \033[32mjoined\033[0m   #{c['name']}  {c['id']}")
        if not joined:
            print("      (private channels are not listed — this needs 'groups:read')")
            print("      if #pixols is private, verify membership by sending it a DM test instead")
        if BOT_CHANNEL:
            match = [c for c in channels if c["name"] == BOT_CHANNEL.lstrip("#")]
            if match:
                c = match[0]
                state = "joined" if c.get("is_member") else "NOT joined"
                print(f"      target #{c['name']}: {state}  {c['id']}")
            else:
                print(f"      no PUBLIC channel named '{BOT_CHANNEL}' — expected if it is private")

    print("\ndependencies")
    try:
        from playwright.async_api import async_playwright

        async def _pw():
            pw = await async_playwright().start()
            b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            await b.close()
            await pw.stop()

        asyncio.run(_pw())
        ok("playwright + chromium working")
    except Exception as e:
        bad(f"chromium unavailable: {e}")
        print("       fix: playwright install chromium")

    async def _models():
        import aiohttp

        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{config.LLM_BASE_URL}/models",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                return [m["id"] for m in (await r.json()).get("data", [])]

    try:
        models = asyncio.run(_models())
        ok(f"LLM reachable at {config.LLM_BASE_URL}")
        if config.LLM_MODEL in models:
            ok(f"model '{config.LLM_MODEL}' present, thinking={'on' if config.LLM_THINK else 'off'}")
        else:
            warn(f"model '{config.LLM_MODEL}' not in {models} — pull it or set LLM_MODEL")
    except Exception as e:
        bad(f"LLM unreachable: {e}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
