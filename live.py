"""A Slack message that fills in while pixol works: status lines while tools run,
then the answer streaming in, then the final (split) reply plus any files."""

import time

import brain
import config


def _looks_like_skip(text: str) -> bool:
    t = text.strip().upper().rstrip(".!")
    return len(t) <= 4 and "SKIP".startswith(t)


class LiveReply:
    def __init__(self, client, channel: str, thread: str, eager: bool):
        self.client, self.channel, self.thread = client, channel, thread
        self.eager = eager  # mentions/DMs show "thinking…" at once; follow-ups stay quiet until they talk
        self.ts: str | None = None
        self.text = ""
        self.status = "thinking" if eager else ""
        self._last = 0.0

    async def start(self):
        if self.eager:
            await self._push(force=True)

    async def set_status(self, status: str):
        self.status = status
        await self._push(force=True)

    async def set_text(self, text: str):
        self.text, self.status = text, ""
        await self._push()

    def _render(self) -> str:
        body = brain.preview(self.text) if self.text else ""
        if len(body) > 3400:
            body = body[:3400] + "\n…"
            if body.count("```") % 2:
                body += "\n```"
        status = f"_{self.status}…_" if self.status else ""
        return "\n\n".join(p for p in (body, status) if p) or "_thinking…_"

    async def _push(self, force: bool = False):
        if not self.ts and not self.status and (not self.text or _looks_like_skip(self.text)):
            return  # might still turn out to be a SKIP; don't post anything yet
        now = time.monotonic()
        if not force and now - self._last < config.STREAM_EVERY:
            return  # finish() always writes the final text, so dropped frames don't matter
        self._last = now
        try:
            if self.ts:
                await self.client.chat_update(channel=self.channel, ts=self.ts, text=self._render())
            else:
                r = await self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread,
                                                       text=self._render())
                self.ts = r["ts"]
        except Exception as e:  # noqa: BLE001
            print(f"[pixol] live update failed: {e}", flush=True)

    async def finish(self, reply) -> bool:
        """Replace the live message with the real reply. Returns True if anything was posted."""
        uploads = list(reply.files)
        if reply.png:
            uploads.append((f"{config.BOT_NAME}.png", reply.png))
        parts = brain.split_for_slack(reply.text) if reply.text else []
        if not parts and not uploads:
            await self.discard()
            return False
        if parts:
            if self.ts:
                await self.client.chat_update(channel=self.channel, ts=self.ts, text=parts[0])
            else:
                await self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread, text=parts[0])
            for part in parts[1:]:
                await self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread, text=part)
        else:
            await self.discard()
        if uploads:
            try:
                await self.client.files_upload_v2(
                    channel=self.channel, thread_ts=self.thread, request_file_info=False,
                    file_uploads=[{"file": data, "filename": name, "title": name} for name, data in uploads],
                )
            except Exception as e:  # noqa: BLE001
                print(f"[pixol] upload failed: {e}", flush=True)
                await self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread,
                                                   text=f"_(couldn't upload {', '.join(n for n, _ in uploads)})_")
        return True

    async def discard(self):
        if self.ts:
            try:
                await self.client.chat_delete(channel=self.channel, ts=self.ts)
            except Exception:
                pass
            self.ts = None
