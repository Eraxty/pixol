# pixol

A Slack helper bot with its own browser and its own Linux box. The model picks
its own tools in a loop, and replies stream into Slack live, with a status line
("searching…", "running python…") while it works.

Triggers: `@pixol` mention, the word `pixol` in a channel, any DM, or a
follow-up in a thread he's already in.

## Stack

| file | job |
| --- | --- |
| `app.py` | Slack Socket Mode listener, routing, debounce, background loops |
| `live.py` | the live message: status, streamed text, final split reply + file uploads |
| `brain.py` | memory, prompts, the agent loop + tools, streaming LLM calls |
| `sandbox.py` | one Docker container per thread for `run_code` / `share_file` |
| `github.py` | read-only GitHub API: repo + README, file tree, files, issues |
| `reminders.py` | reminders persisted to `reminders.json`, posted back in the thread |
| `browser.py` | persistent headless Chromium: page read, screenshot, search |
| `config.py` | env loading + validation |

Tools the model can call: `web_search`, `open_page`, `github`, `read_slack`,
`remember`, `reminder`, and (with Docker) `run_code` + `share_file`. Time-zone
conversion and remember/forget commands stay deterministic, never guessed.

### Sandbox

Each thread gets a `python:3.12-slim` container (`pixol-sb-*`), created on first
use, stopped after 15 idle minutes and deleted after 12 hours, so files survive
between messages. No network by default (`SANDBOX_NETWORK=bridge` allows pip),
512 MB RAM, 1 CPU, 256 processes, 60 s per run, all capabilities dropped.
`SANDBOX_ENABLED=false` turns it off.

## 1. Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → *From scratch*.
2. **Socket Mode** → enable → create an app-level token with `connections:write`.
   That is your `SLACK_APP_TOKEN` (`xapp-...`).
3. **OAuth & Permissions** → Bot Token Scopes:
   `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`,
   `im:history`, `reactions:write`, `files:write`. Install to Workspace.
   That gives `SLACK_BOT_TOKEN` (`xoxb-...`).
4. **Event Subscriptions** → enable → subscribe to bot events:
   `app_mention`, `message.channels`, `message.groups`, `message.im`.
5. **Manage Domains** is not needed (Socket Mode, no public URL).

## 2. Configure

```bash
cp .env.example .env   # then fill in the two Slack tokens
```

## 3. LLM

Defaults to a local Ollama at `http://localhost:11434/v1` with `qwen3:4b`.
Any OpenAI-compatible endpoint works — point `LLM_BASE_URL` at it.

> Thinking models spend tokens reasoning *before* they answer. Keep
> `LLM_MAX_TOKENS` well above your longest answer (default `4000`), otherwise
> the model hits the cap mid-thought and returns an empty reply.

## 4. Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python app.py
```

## Notes

- Memory is per `channel:thread` and persists to `memory.json`.
- Screenshots are capped at `SHOT_MAX_H` px; page text at `PAGE_TEXT_LIMIT`.
- Search falls through Brave → Bing → mwmbl automatically when one blocks.
