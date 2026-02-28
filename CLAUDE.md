# CLAUDE.md

## Project Overview

Telegram bot that classifies incoming messages using an LLM and saves them as Markdown notes into an Obsidian vault, synced via Git to a GitHub repository.

## Tech Stack

- **Language:** Python 3.12
- **Telegram framework:** aiogram 3.x (async)
- **LLM:** OpenAI SDK pointed at OpenRouter (Gemini 2.5 Flash Lite)
- **Git operations:** dulwich (pure Python, fork-safe for Docker)
- **Templates:** Jinja2 for note rendering
- **Deduplication:** faiss-cpu + OpenAI embeddings
- **Config:** YAML with `${ENV_VAR}` interpolation
- **Deployment:** Docker + docker-compose

## Project Structure

```
bot/
  main.py          # Entry point: loads config, wires components, starts polling
  config.py        # YAML config loader with env var resolution
  handlers.py      # Telegram message/command handlers
  llm.py           # LLM classification via OpenRouter
  git_sync.py      # Git add/commit/push/pull, debounce, conflict resolution
  vault.py         # Markdown file writing and attachment management
  dedup.py         # Duplicate detection via embeddings + FAISS
  note_template.md.j2  # Jinja2 template for notes
config/
  settings.yaml         # Bot token, LLM model, git debounce, dedup config
  vault_structure.yaml  # Folder tree + tag definitions (fed to LLM)
```

## Running Locally

```bash
pip install -r requirements.txt

# Required env vars (or put them in .env):
export TG_BOT_TOKEN=...
export OPENROUTER_API_KEY=...
export GITHUB_TOKEN=...

python -m bot.main
```

## Running with Docker

```bash
docker-compose up --build -d
docker-compose logs -f bot
```

## Key Commands (Telegram)

- `/push` — manually trigger git commit + push (bypasses debounce)
- `/deduplicate [threshold]` — scan vault for duplicate notes (default threshold: 0.90)

## Architecture Notes

- **Async-first:** all I/O (Telegram, LLM, Git) is async via asyncio
- **No database:** state lives in git repo + markdown files + embedding cache JSON
- **Debounced commits:** notes batch for 30s before committing (configurable via `git.commit_debounce_seconds`)
- **Background loops:** `sync_loop()` commits dirty state every 30s; `pull_loop()` pulls from remote every 60s
- **LLM fallback:** if classification fails, notes go to `inbox/` with generic tags
- **Conflict resolution:** interactive Telegram inline buttons for merge conflicts (30-min timeout)

## Code Conventions

- All modules use Python `logging` (INFO level)
- Error handling: LLM failures fall back gracefully; git errors notify the user via Telegram
- No test suite or linter is currently configured
