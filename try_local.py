"""Talk to pixol in your terminal — no Slack tokens needed.

    python try_local.py            # interactive
    python try_local.py "3:30 pm est in ist"
    python try_local.py "https://example.com"   # writes page.png
"""

import asyncio
import sys
from pathlib import Path

import brain
from browser import browser

SHOT = Path(__file__).resolve().parent / "page.png"
KEY = "local:cli"


async def ask(text: str) -> None:
    reply = await brain.respond(text, KEY)
    print(f"\n{pixol_prefix()} {reply.text}")
    if reply.png:
        SHOT.write_bytes(reply.png)
        print(f"  [screenshot saved -> {SHOT.name}, {len(reply.png) // 1024} KB]")
    print()


def pixol_prefix() -> str:
    return f"\n{brain.config.BOT_NAME}>"


async def main() -> None:
    try:
        if len(sys.argv) > 1:
            await ask(" ".join(sys.argv[1:]))
            return

        print(f"{pixol_prefix()} local mode — Ctrl-D or 'exit' to quit\n")
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye")
                return
            if not line:
                continue
            if line in {"exit", "quit", ":q"}:
                print("bye")
                return
            try:
                await ask(line)
            except Exception as e:  # noqa: BLE001
                print(f"\n{pixol_prefix()} error: {e}\n")
    finally:
        await browser.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
