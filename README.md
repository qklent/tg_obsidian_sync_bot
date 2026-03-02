# tg_obsidian_sync

Telegram bot that classifies incoming messages using an LLM and saves them as Markdown notes into an Obsidian vault, synced via Git to a GitHub repository.

## Tech Stack

- **Language:** Python 3.12
- **Telegram:** aiogram 3.x (async)
- **LLM:** OpenAI SDK via OpenRouter (Gemini 2.5 Flash Lite)
- **Git:** dulwich (pure Python, Docker-safe)
- **Templates:** Jinja2
- **Dedup:** faiss-cpu + OpenAI embeddings
- **Config:** YAML with `${ENV_VAR}` interpolation
- **Deploy:** Docker + docker-compose

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

## Telegram Commands

- `/push` — manually trigger git commit + push
- `/deduplicate [threshold]` — scan vault for duplicate notes (default: 0.90)

## AI Development Pipeline

This project uses an autonomous multi-agent Claude Code pipeline with Obsidian Kanban for task management.

### Workflow
Planning -> Todo -> In Progress -> Review -> Done

### Quick Start

**Add a new task:**
Copy `notes/_template.md` to `notes/my-feature.md`, fill in raw notes.

**Refine a task (interactive):**
```bash
make clarify
# or: ./scripts/clarify-task.sh notes/my-feature.md
```

**Run pipeline on all todo tasks:**
```bash
make implement
```

**Run tasks in parallel (via git worktrees):**
```bash
make implement-parallel
```

**Watch for new tasks automatically:**
```bash
make watch
```

**Retry failed tasks:**
```bash
make retry
```

**Sync the Kanban board:**
```bash
make sync-board
```

**Update project codemap:**
```bash
make update-codemap
```

**Check task statuses:**
```bash
make status
```

### Agents

| Agent | Model | Role |
|-------|-------|------|
| task-clarifier | Opus | Refines vague notes into detailed specs |
| planner | Opus | Creates implementation plans |
| coder | Opus | Implements features |
| code-reviewer | Sonnet | Reviews for security and quality |
| test-runner | Sonnet | Runs and fixes tests |
| git-workflow | Sonnet | Manages branches and PRs |
| doc-updater | Sonnet | Keeps docs and codemap in sync |

### Safety Nets

- **Hooks** auto-format code, type-check edits, block debug statements from being pushed
- **Retry logic** automatically retries failed tasks up to 3 times
- **Board sync** keeps Obsidian Kanban in sync with actual task states

### Requirements

- Claude Code CLI installed globally
- `ANTHROPIC_API_KEY` set in environment
- `gh` CLI installed and authenticated
- For Telegram notifications: `TG_BOT_TOKEN` and `TG_CHAT_ID` set
