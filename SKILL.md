---
name: browser-use
description: Drive a real web browser step-by-step with Kiro as the decision-maker — no external LLM API key required. Use when the user wants to browse the web, navigate to pages, read page content, click links or buttons, fill in and submit forms, extract data from a site, or take screenshots of web pages through automation. Kiro reads the page state and decides each action itself.
---

# Browser Use (Kiro-driven)

Control a persistent Chrome browser one action at a time. **Kiro is the brain**:
the helper script reports the current page state as JSON, Kiro decides the next
action, calls the script again, and repeats until the task is done. No OpenAI/
Anthropic key is needed because Kiro — not `browser_use.Agent` — does the reasoning.

## When to use
- "Go to <site> and find/click/read ..."
- "Fill in this form and submit it"
- "Extract the <data> from this page"
- "Take a screenshot of <page>"
- Any small-to-medium interactive web task that benefits from judgement per step.

## When NOT to use
- Large-scale scraping / hundreds of repetitive steps (each step is a tool round-trip
  through Kiro — slow). Write a dedicated script instead.
- Tasks needing a logged-in human session you cannot reproduce headlessly.

## How it runs
Every command is a single process that connects to one long-lived headless Chrome
over CDP, performs ONE action, prints ONE JSON line to stdout, and disconnects
WITHOUT closing the browser. State therefore persists between calls.

Runner — point these at your install (see README for setup):

```
PY=<path-to-venv>/bin/python          # e.g. ~/.venv-browseruse/bin/python
STEP=<install-dir>/scripts/browser_step.py
```

### Commands
| Command | Purpose |
|---|---|
| `$PY $STEP start` | Launch the persistent headless Chrome (CDP on 127.0.0.1:9222). |
| `$PY $STEP state` | Print current `url`, `title`, and indexed interactive `elements`. |
| `$PY $STEP goto <url>` | Navigate, then print state. First (cold) nav can take ~30s. |
| `$PY $STEP click <index>` | Click the element with that index, then print state. |
| `$PY $STEP type <index> <text...>` | Type text into the element (clears first), then print state. |
| `$PY $STEP search <index\|#id> <text...>` | Type text then press Enter in one step. Accepts a stable `#element_id` so it survives DOM re-indexing (best for search boxes). |
| `$PY $STEP scroll <up\|down> [amount]` | Scroll, then print state. |
| `$PY $STEP screenshot [path]` | Save a PNG; print its path. |
| `$PY $STEP stop` | Kill Chrome and delete its temp profile. |

### Output contract
stdout is always a single JSON object. On success: `{"ok": true, ...}`. On failure:
`{"ok": false, "error": "..."}`. Library/browser logs go to stderr — ignore them
unless debugging. The `elements` field lists interactive nodes as
`[<index>]<tag/> text`; use those indices for `click` / `type`.

## The step loop Kiro follows
1. `start` (once per task).
2. `goto <url>` to reach the starting page.
3. Read the `elements` in the JSON. Decide the next action.
4. Run `click` / `type` / `search` / `scroll`; each returns fresh state.
5. Repeat 3–4 until the goal is met. Indices can change after the DOM updates, so
   always decide from the latest returned state, not a remembered one. For search
   boxes prefer `search #id` — the stable id avoids the volatile submit-button index.
6. Report the result to the user. `stop` when the task is finished.

## Notes & limits
- Indices are re-derived from the live DOM on every action; if a `click` returns
  "No element with index N", the page changed — re-read state and pick again.
- The browser is headless with `--no-sandbox` (server environment).
- A network-exposed debugging port is bound to loopback only (127.0.0.1:9222).
- Anti-bot walls (CAPTCHA / Cloudflare "verify you are human") will block some
  sites (e.g. Baidu, Bing from datacenter IPs). Do NOT attempt to solve them.
- This drives real navigation; treat destructive actions (purchases, deletes,
  posting) as high-risk and confirm with the user before performing them.
