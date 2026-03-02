# Coding Style Rules

- Prefer immutable data structures where practical
- Use Python `logging` module (never `print()` or `console.log` for debugging)
- Follow existing async patterns — all I/O must be async
- Keep modules focused: one responsibility per file
- Reuse existing utilities in `bot/` before creating new ones
- Type hints on all public function signatures
- No unused imports or variables
- Error handling: fail gracefully with user-facing Telegram messages, log full tracebacks internally
