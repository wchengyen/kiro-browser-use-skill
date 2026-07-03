# Kiro Browser-Use Skill

Drive a **real web browser step-by-step with [Kiro](https://kiro.dev) as the
decision-maker** — no external LLM API key required.

This is a [Kiro CLI skill](https://kiro.dev): a `SKILL.md` plus a small helper
script that lets Kiro browse the web, click, fill forms, extract data, and take
screenshots by **reading the page state and deciding each action itself**.

## Why this exists

The [`browser-use`](https://github.com/browser-use/browser-use) library ships an
autonomous `Agent` that needs its **own** LLM (an OpenAI/Anthropic API key) to
decide what to click next. That means paying for and wiring up a second model.

This skill flips that around:

| | Standard `browser_use.Agent` | This skill (Kiro-driven) |
|---|---|---|
| Who decides the next action | `browser_use.Agent` via its own LLM | **Kiro** (the assistant already in your terminal) |
| External LLM API key | **Required** | **Not required** |
| browser-use's role | brain **and** hands | **hands only** (navigate / DOM / click / type) |
| Control loop | inside the library | Kiro's own tool-use loop |

browser-use is used purely as the **hands** — CDP navigation, DOM extraction, and
clicks/typing via its event bus. **Kiro is the brain**: the helper script prints
the current page state as JSON, Kiro reads it, decides the next step, and calls the
script again. Because Kiro does the reasoning, there is no second API key to buy.

## How it works

```
        ┌─────────────────────────────────────────────────────┐
        │  Kiro (the brain — no extra API key)                 │
        │  reads JSON state ─▶ decides next action             │
        └───────────────┬─────────────────────▲───────────────┘
                        │ runs one command      │ JSON: url, title,
                        ▼                        │ indexed elements
        ┌─────────────────────────────────────────────────────┐
        │  scripts/browser_step.py  (one action per process)   │
        │  connects over CDP, acts, prints ONE JSON line       │
        └───────────────┬─────────────────────▲───────────────┘
                        │ Chrome DevTools Proto │
                        ▼                        │
        ┌─────────────────────────────────────────────────────┐
        │  Persistent headless Chrome (127.0.0.1:9222)         │
        │  stays alive between steps → page state persists     │
        └─────────────────────────────────────────────────────┘
```

Each invocation of `browser_step.py` is a **separate process** that connects to one
**long-lived** headless Chrome over the Chrome DevTools Protocol, performs a single
action, prints exactly one JSON object to stdout, and disconnects **without** closing
the browser. State therefore persists across steps.

## Install

Requires Python 3.11+ and a Chrome/Chromium binary on the machine.

```bash
# 1. Create an isolated environment for browser-use
python3 -m venv ~/.venv-browseruse
~/.venv-browseruse/bin/pip install -r requirements.txt

# 2. (If no system Chrome) install a browser for it
~/.venv-browseruse/bin/python -m playwright install chromium --with-deps

# 3. Install the skill for Kiro CLI (global) — or copy into a project's .kiro/skills/
mkdir -p ~/.kiro/skills/browser-use/scripts
cp SKILL.md                ~/.kiro/skills/browser-use/SKILL.md
cp scripts/browser_step.py ~/.kiro/skills/browser-use/scripts/browser_step.py
```

Kiro CLI auto-discovers skills under `~/.kiro/skills/**/SKILL.md`. The `description`
in the frontmatter is what tells Kiro when to reach for it (browsing, clicking,
filling forms, extracting data, screenshots).

> Update the `PY` / `STEP` paths in `SKILL.md` to match where you installed the
> venv and script.

## Usage (what Kiro runs)

```bash
PY=~/.venv-browseruse/bin/python
STEP=~/.kiro/skills/browser-use/scripts/browser_step.py

$PY $STEP start                       # launch persistent headless Chrome
$PY $STEP goto example.com            # navigate, prints url/title/elements
$PY $STEP click 20                    # click element by index
$PY $STEP type 7 "hello"              # type into element by index
$PY $STEP search "#sb_form_q" "kiro"  # type + Enter (id-stable, for search boxes)
$PY $STEP scroll down 800
$PY $STEP screenshot out.png
$PY $STEP stop                        # kill Chrome, clean up
```

Every command prints a single JSON object. The `elements` field lists interactive
nodes as `[<index>]<tag/> text`; Kiro uses those indices for the next `click`/`type`.

### The step loop Kiro follows

1. `start` once.
2. `goto <url>`.
3. Read `elements` in the returned JSON; decide the next action.
4. `click` / `type` / `search` / `scroll` — each returns fresh state.
5. Repeat 3–4 until done. Indices can change after the DOM updates, so always
   decide from the **latest** returned state. For search boxes prefer
   `search #id`, whose stable id survives DOM re-indexing.
6. Report the result; `stop`.

## Worked example

Task: *open the runoob LangChain tutorial, search "langgraph", report the top 3 hits.*

```bash
$PY $STEP start
$PY $STEP goto "https://www.runoob.com/langchain/langchain-tutorial.html"
# Kiro reads the state, sees the search input id=s, and submits the query:
$PY $STEP goto "https://www.runoob.com/?s=langgraph"   # results page
$PY $STEP state                                         # Kiro reads the result list
$PY $STEP stop
```

Kiro then reports the top three result titles/snippets from the returned state.

## Command reference

| Command | Purpose |
|---|---|
| `start` | Launch the persistent headless Chrome (CDP on 127.0.0.1:9222). |
| `state` | Print current `url`, `title`, and indexed interactive `elements`. |
| `goto <url>` | Navigate, then print state. First (cold) nav can take ~30s. |
| `click <index>` | Click the element with that index, then print state. |
| `type <index> <text...>` | Type text into the element (clears first), then print state. |
| `search <index\|#id> <text...>` | Type text then press Enter in one step (id-stable). |
| `scroll <up\|down> [amount]` | Scroll, then print state. |
| `screenshot [path]` | Save a PNG; print its path. |
| `stop` | Kill Chrome and delete its temp profile. |

## Limits & safety

- **Slow for bulk work** — each step is a round-trip through Kiro. Great for
  small/medium interactive tasks; write a dedicated script for mass scraping.
- **Anti-bot walls** — CAPTCHA / Cloudflare "verify you are human" challenges will
  block some sites (e.g. Baidu, Bing from datacenter IPs). This skill does **not**
  solve or bypass them.
- **Headless `--no-sandbox`** — tuned for server environments. The CDP debug port is
  bound to loopback only (127.0.0.1:9222).
- **Destructive actions** (purchases, deletes, posting) are treated as high-risk;
  Kiro should confirm with you before performing them.

## License

MIT — see [LICENSE](LICENSE).
