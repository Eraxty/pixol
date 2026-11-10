"""Pixol's browser: one persistent Chromium, isolated context per job."""

import asyncio
import base64
import re
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout
from playwright.async_api import async_playwright

import config

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SEARCH_ENGINES: list[tuple[str, str]] = [
    ("brave", "https://search.brave.com/search?q="),
    ("bing", "https://www.bing.com/search?q="),
    ("mwmbl", "https://mwmbl.org/?q="),
]

_ws_re = re.compile(r"[ \t]+")
_multi_nl = re.compile(r"\n{3,}")
_bing_redirect = re.compile(r"^https?://www\.bing\.com/ck/a\?", re.I)

_EXTRACT_JS = """
() => {
  const pick = document.querySelector('article, main, [role="main"], #content, .post-content');
  const root = (pick && pick.innerText && pick.innerText.length > 400) ? pick : document.body;
  return (root && root.innerText) ? root.innerText : '';
}
"""


class PageResult:
    def __init__(self, url, final_url, title, text, png):
        self.url = url
        self.final_url = final_url
        self.title = title
        self.text = text
        self.png = png


class Browser:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._start_lock = asyncio.Lock()

    async def start(self):
        if self._browser:
            return
        async with self._start_lock:
            if self._browser:
                return
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )

    async def stop(self):
        for obj in (self._browser, self._pw):
            try:
                if obj:
                    await obj.close()
            except Exception:
                pass
        self._browser = None
        self._pw = None

    async def _context(self):
        await self.start()
        return await self._browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 1600},
            locale="en-US",
            timezone_id="UTC",
            java_script_enabled=True,
            bypass_csp=True,
        )

    async def search(self, query: str, limit: int | None = None):
        limit = limit or config.SEARCH_LIMIT
        query = (query or "").strip()
        if not query:
            return []
        for name, base in SEARCH_ENGINES:
            results = await self._scrape_search(base, _PARSERS[name], query, limit)
            if results:
                return results[:limit]
        return []

    async def _scrape_search(self, base: str, parse, query: str, limit: int):
        ctx = await self._context()
        try:
            page = await ctx.new_page()
            await page.goto(
                base + quote_plus(query),
                wait_until="domcontentloaded",
                timeout=config.NAV_TIMEOUT_MS,
            )
            await _quiet_idle(page)
            html = await page.content()
        except (PWError, PWTimeout):
            return []
        finally:
            await ctx.close()

        try:
            return _dedupe(parse(BeautifulSoup(html, "html.parser")))[:limit]
        except Exception:
            return []

    async def read(self, url: str) -> PageResult:
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        ctx = await self._context()
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
            await _quiet_idle(page)

            title = (await page.title() or "").strip()
            text = await page.evaluate(_EXTRACT_JS)
            text = _clean_text(text)

            final_url = page.url
            png = await self._shot(page)
        finally:
            await ctx.close()

        if not title:
            title = urlparse(final_url).netloc or url
        return PageResult(url, final_url, title, text[: config.PAGE_TEXT_LIMIT], png)

    async def _shot(self, page) -> bytes | None:
        try:
            height = await page.evaluate("() => document.body.scrollHeight || 0")
            if height and height <= config.SHOT_MAX_H:
                return await page.screenshot(full_page=True, type="png")
            await page.set_viewport_size(
                {"width": 1280, "height": min(config.SHOT_MAX_H, max(height or 0, 900))}
            )
            await page.wait_for_timeout(250)
            return await page.screenshot(type="png")
        except Exception:
            return None


async def _quiet_idle(page, ms=4000):
    try:
        await page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass
    try:
        await page.wait_for_timeout(400)
    except Exception:
        pass


def _clean_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        line = _ws_re.sub(" ", line).strip()
        if line:
            lines.append(line)
    return _multi_nl.sub("\n\n", "\n".join(lines))


def _flat(s: str) -> str:
    return " ".join((s or "").split())


def _bing_unwrap(href: str) -> str:
    if not href or not _bing_redirect.match(href):
        return href
    val = (parse_qs(urlparse(href).query).get("u") or [""])[0]
    if not val.startswith("a1"):
        return href
    raw = val[2:] + "=" * (-(len(val) - 2) % 4)
    try:
        return base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    except Exception:
        return href


def _parse_brave(soup: BeautifulSoup) -> list[dict]:
    out = []
    for box in soup.select('div.snippet[data-type="web"]'):
        a = box.select_one("a[href]")
        if a is None:
            continue
        t = box.select_one(".search-snippet-title")
        if t is not None:
            title = _flat(t.get("title") or t.get_text(" ", strip=True))
        else:
            title = ""
        if not title:
            title = _flat(a.get_text(" ", strip=True))
        d = box.select_one(".content")
        snip = _flat(d.get_text(" ", strip=True)) if d is not None else ""
        out.append({"title": title, "url": a.get("href") or "", "snippet": snip[:400]})
    return out


def _parse_bing(soup: BeautifulSoup) -> list[dict]:
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a[href]")
        if a is None:
            continue
        p = li.select_one(".b_caption p") or li.select_one("p")
        snip = _flat(p.get_text(" ", strip=True)) if p is not None else ""
        out.append({
            "title": _flat(a.get_text(" ", strip=True)),
            "url": _bing_unwrap(a.get("href") or ""),
            "snippet": snip[:400],
        })
    return out


def _parse_mwmbl(soup: BeautifulSoup) -> list[dict]:
    out = []
    for a in soup.select("main a.group[href]"):
        if not a.get("href", "").startswith("http"):
            continue
        title = _flat(a.get_text(" ", strip=True))
        out.append({"title": title, "url": a.get("href") or "", "snippet": ""})
    return out


_PARSERS = {"brave": _parse_brave, "bing": _parse_bing, "mwmbl": _parse_mwmbl}

_JUNK_HOSTS = (
    "duckduckgo.com", "google.com", "bing.com", "brave.com",
    "mwmbl.org", "mojeek.com", "startpage.com", "marginalia",
)


def _is_junk(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(j in host for j in _JUNK_HOSTS)


def _dedupe(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        url = (it.get("url") or "").strip()
        if not url.startswith("http") or url in seen or _is_junk(url):
            continue
        if not (it.get("title") or "").strip():
            continue
        seen.add(url)
        out.append(it)
    return out


browser = Browser()
