"""Per-thread Docker sandboxes: pixol's own little Linux box for running code.

One container per Slack thread, created lazily, stopped when idle and removed
after SANDBOX_TTL_HOURS, so files and installed packages survive between
messages in the same thread. No network by default, capped CPU/memory/pids.
"""

import asyncio
import hashlib
import shlex
import time
from pathlib import PurePosixPath

import config

LABEL = "pixol.sandbox"
_last_used: dict[str, float] = {}  # container name -> monotonic time
_locks: dict[str, asyncio.Lock] = {}


def _name(key: str) -> str:
    return "pixol-sb-" + hashlib.sha1(key.encode()).hexdigest()[:12]


async def _docker(*args: str, stdin: bytes | None = None, timeout: float = 60) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"timed out"
    return proc.returncode or 0, out, err


async def _ensure(key: str) -> str:
    """Start (or wake) this thread's container and return its name."""
    name = _name(key)
    async with _locks.setdefault(name, asyncio.Lock()):
        code, out, _ = await _docker("inspect", "-f", "{{.State.Running}}", name)
        if code == 0 and out.strip() == b"false":
            await _docker("start", name)
        elif code != 0:
            code, _, err = await _docker(
                "run", "-d", "--name", name, "--label", LABEL,
                "--network", config.SANDBOX_NETWORK,
                "--memory", config.SANDBOX_MEMORY, "--memory-swap", config.SANDBOX_MEMORY,
                "--cpus", str(config.SANDBOX_CPUS), "--pids-limit", "256",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--workdir", "/work", "--hostname", "pixol",
                "-e", "PYTHONUNBUFFERED=1", "-e", "MPLBACKEND=Agg",
                config.SANDBOX_IMAGE, "sh", "-c", "mkdir -p /work && exec sleep infinity",
                timeout=120,
            )
            if code != 0:
                raise RuntimeError(f"couldn't start sandbox: {err.decode(errors='replace')[:300]}")
    _last_used[name] = time.monotonic()
    return name


def _clip(b: bytes, limit: int) -> str:
    s = b.decode(errors="replace")
    return s if len(s) <= limit else s[: limit // 2] + f"\n…[{len(s) - limit} chars cut]…\n" + s[-limit // 2 :]


async def run(key: str, code: str, language: str = "python") -> str:
    """Run code in the thread's sandbox; returns exit code + output for the model."""
    name = await _ensure(key)
    cmd = ["python3", "-"] if language == "python" else ["bash", "-s"]
    t = config.SANDBOX_TIMEOUT
    rc, out, err = await _docker("exec", "-i", name, "timeout", "-k", "2", str(t), *cmd,
                                 stdin=code.encode(), timeout=t + 10)
    _last_used[name] = time.monotonic()
    parts = [f"exit code {rc}" + (" (timed out)" if rc in (124, 137) else "")]
    if out:
        parts.append("stdout:\n" + _clip(out, config.SANDBOX_OUTPUT_LIMIT))
    if err:
        parts.append("stderr:\n" + _clip(err, config.SANDBOX_OUTPUT_LIMIT // 2))
    return "\n".join(parts)


async def read_file(key: str, path: str) -> tuple[str, bytes]:
    """Pull a file out of the sandbox (for uploading to Slack)."""
    name = await _ensure(key)
    p = PurePosixPath(path if path.startswith("/") else f"/work/{path}")
    rc, out, err = await _docker("exec", name, "sh", "-c",
                                 f"test -f {shlex.quote(str(p))} && head -c {config.SANDBOX_MAX_FILE + 1} "
                                 f"{shlex.quote(str(p))}")
    if rc != 0:
        raise FileNotFoundError(f"no file at {p}")
    if len(out) > config.SANDBOX_MAX_FILE:
        raise ValueError(f"{p.name} is bigger than {config.SANDBOX_MAX_FILE // 1_000_000}MB")
    return p.name, out


async def janitor():
    """Stop idle sandboxes, remove ones untouched for SANDBOX_TTL_HOURS. Runs forever."""
    # sandboxes from before a restart keep their files; start their clocks now
    _, out, _ = await _docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"label={LABEL}")
    for name in out.decode().split():
        _last_used.setdefault(name, time.monotonic())
    stopped: dict[str, float] = {}  # name -> last-use time it was stopped at
    while True:
        now = time.monotonic()
        for name, t in list(_last_used.items()):
            idle = now - t
            if idle > config.SANDBOX_TTL_HOURS * 3600:
                await _docker("rm", "-f", name)
                _last_used.pop(name, None)
                stopped.pop(name, None)
            elif idle > config.SANDBOX_IDLE_STOP and stopped.get(name) != t:
                await _docker("stop", "-t", "2", name)
                stopped[name] = t
        await asyncio.sleep(300)
