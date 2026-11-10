"""Read-only GitHub lookups over the REST API (repo info + README, file tree, files, issues)."""

import re

import aiohttp

import config

API = "https://api.github.com"
REPO_RE = re.compile(r"^(?:https?://github\.com/)?([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def _headers(raw: bool = False) -> dict:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
         "User-Agent": "pixol-bot"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


async def _get(session: aiohttp.ClientSession, path: str, raw: bool = False):
    async with session.get(API + path, headers=_headers(raw)) as r:
        if r.status == 404:
            raise LookupError("not found (or private)")
        if r.status == 403:
            raise RuntimeError("GitHub rate limit hit, try again later")
        r.raise_for_status()
        return await r.text() if raw else await r.json()


async def lookup(kind: str, repo: str, path: str = "") -> str:
    m = REPO_RE.match(repo.strip())
    if not m:
        return "repo must look like owner/name"
    repo = m.group(1)
    limit = config.PAGE_TEXT_LIMIT
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        if kind == "repo":
            info = await _get(s, f"/repos/{repo}")
            head = (f"{info['full_name']}: {info.get('description') or 'no description'}\n"
                    f"★{info['stargazers_count']} · {info.get('language')} · default branch "
                    f"{info['default_branch']} · updated {info['pushed_at']}\n{info['html_url']}")
            try:
                readme = await _get(s, f"/repos/{repo}/readme", raw=True)
            except LookupError:
                readme = "(no README)"
            return f"{head}\n--- README ---\n{readme[:limit]}"
        if kind == "tree":
            branch = (await _get(s, f"/repos/{repo}"))["default_branch"]
            tree = await _get(s, f"/repos/{repo}/git/trees/{branch}?recursive=1")
            paths = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
            more = f"\n…and {len(paths) - 400} more" if len(paths) > 400 else ""
            return "\n".join(paths[:400]) + more
        if kind == "file":
            if not path:
                return "need a path for kind=file"
            text = await _get(s, f"/repos/{repo}/contents/{path.lstrip('/')}", raw=True)
            return text if len(text) <= limit else text[:limit] + f"\n…[cut, {len(text)} chars total]"
        if kind == "issues":
            items = await _get(s, f"/repos/{repo}/issues?state=open&per_page=20")
            if not items:
                return "no open issues or PRs"
            return "\n".join(f"#{i['number']} {'[PR] ' if 'pull_request' in i else ''}{i['title']}"
                             f" ({i['user']['login']})" for i in items)
    return f"unknown kind {kind}"
