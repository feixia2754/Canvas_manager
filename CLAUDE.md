# CLAUDE.md — Canvas Manager

## Project overview

Canvas Manager is a CLI tool that aggregates Canvas LMS deadlines, Google Calendar events, and iCal imports into a unified deadline feed with email/SMS reminders via the Gmail API.

Entry point: `canvas_manager.main:cli` (Click group).  
Install: `pip install -e .`  
Run: `canvas-manager <command>`

---

## Architecture

```
canvas_manager/
  main.py          — Click CLI; commands: setup, sync, list, remind, import-ical, setup-cron
  config.py        — All env-var reads; single source of truth for configuration
  canvas_client.py — Canvas REST API (requests); returns normalized deadline dicts
  gcal_client.py   — Google Calendar API; returns normalized deadline dicts
  notifier.py      — Gmail API; HTML+plain email, SMS via carrier gateway
  ical_parser.py   — .ics file parsing and fuzzy merge with Canvas deadlines
  ai/              — RESERVED — see section below
```

### Deadline dict (the canonical data structure)

Every source (Canvas, GCal, iCal) normalizes to this exact shape before any processing:

```python
{
    "name":      str,       # assignment/event title
    "due_at":    datetime,  # UTC-aware
    "course":    str,       # course code or "Unknown"
    "url":       str,       # canonical link
    "source":    str,       # "canvas" | "gcal" | "ical"
    "submitted": bool,      # always present; False for non-Canvas sources
}
```

Never add ad-hoc keys to this dict without updating every consumer.

---

## Module responsibilities

| Module | Owns | Never does |
|---|---|---|
| `config.py` | Reading `.env` via python-dotenv | API calls, I/O |
| `canvas_client.py` | Canvas REST calls | Config reads, display |
| `gcal_client.py` | Google Calendar calls, OAuth token | Config reads, display |
| `notifier.py` | Gmail send, SMS gateway routing | Fetching deadlines |
| `ical_parser.py` | iCal parsing, fuzzy dedup | Network calls |
| `main.py` | CLI UX, cache read/write, orchestration | Business logic |

---

## Package conventions

### Environment / config
- All configuration comes from `.env` via `config.py`. No module reads `os.environ` directly except `config.py`.
- Validate credentials live (during `setup`) rather than at import time.

### File paths
- Always compute paths relative to `Path(__file__).parent` (or the caller's `__file__`), never relative to cwd. This keeps cache and token files location-stable regardless of where the user runs the CLI.

### Date/time
- Store and pass all datetimes as UTC-aware `datetime` objects.
- Convert to local time only at display boundaries (table, email, SMS text).
- Parse Canvas ISO strings with `datetime.fromisoformat`; handle trailing `Z` explicitly.

### HTTP / APIs
- Use `requests` for Canvas REST; raise on non-2xx with a descriptive message.
- Use the `google-api-python-client` library for Gmail and Google Calendar; never roll raw HTTP for Google APIs.
- Respect API pagination where applicable (Canvas `per_page=50`).

### CLI (Click)
- Every command gets its own `@cli.command()` function in `main.py`.
- Use `rich` for all terminal output (tables, colored text). Never use bare `print` for user-facing output.
- `--preview` flags must never make network calls or mutate state.

### Notifications
- Build email as both HTML and `text/plain` parts.
- Keep SMS body under 320 characters; truncate assignment names before carrier gateways do.

### Testing
- Use `click.testing.CliRunner` for CLI integration tests.
- Use a seeded JSON cache fixture instead of live API calls in tests.
- Skip live crontab tests in CI via `pytest.mark.skipif(os.getenv("CI"))`.

---

## Never / always rules

- **Never** commit `.env`, `token.json`, `credentials.json`, or cache files — all are in `.gitignore`.
- **Never** store credentials or tokens anywhere except the files listed above.
- **Always** sort deadline lists by `due_at` ascending before display or sending.
- **Always** deduplicate across sources before caching (2-hour tolerance + 60% word-overlap threshold in `ical_parser.merge_with_canvas`).
- **Never** call `sys.exit()` inside library modules; raise exceptions and let `main.py` handle them.
- **Never** use `print()` in library modules; use Python `logging` or surface errors via exceptions.
- **Never** hardcode any URL, token, or credential — always route through `config.py`.
- **Always** keep `submitted` field present on every deadline dict, even for non-Canvas sources (default `False`).

---

## canvas_manager/ai/ — RESERVED for AI features

This sub-package is reserved for upcoming AI-powered features using the **Anthropic SDK** (`anthropic` PyPI package). No other code should live here.

### Conventions for all code in `canvas_manager/ai/`

1. **Client instantiation** — always instantiate `anthropic.Anthropic()` once per module (or accept it as a dependency-injected argument); never create a new client per call.

2. **Temperature** — always pass `temperature=0` on every API call. These features are deterministic tools, not creative generation.

3. **Structured output** — always use tool use / JSON-schema-constrained output to get structured data back. Never parse free-form text with regex or string splitting.
   ```python
   response = client.messages.create(
       model="claude-sonnet-4-6",
       temperature=0,
       tools=[{"name": "...", "input_schema": {...}}],
       tool_choice={"type": "tool", "name": "..."},
       ...
   )
   result = response.content[0].input  # typed dict matching the schema
   ```

4. **Model** — default to `claude-sonnet-4-6`; only switch models after an explicit decision recorded here.

5. **Prompt caching** — add `"cache_control": {"type": "ephemeral"}` to static system-prompt blocks to reduce latency and cost on repeated calls.

6. **Error handling** — catch `anthropic.APIError` at the boundary in `main.py`, not inside `ai/` modules. Let AI modules raise; let the CLI handle gracefully.

7. **No side effects** — AI modules must not mutate the cache, send notifications, or make Canvas/GCal API calls. They receive data and return structured results only.

8. **Typing** — annotate all public functions with full type hints. Return `TypedDict` or `dataclasses.dataclass` instances, not raw dicts.
