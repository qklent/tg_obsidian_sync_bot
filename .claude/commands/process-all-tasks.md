---
description: Process all todo tasks sequentially
---
List all .md files in /home/qklent/programming/Notes/Notes/tg_sync_bot/ (excluding board.md,
_template.md, _codemap.md, and files in done/).

For each file with status: todo in its YAML frontmatter:
- Run the implement-task pipeline on it
- Report results
- Continue to the next task regardless of success/failure

At the end, output a summary table:
| Task | Status | PR URL | Notes |

NOTE: For parallel execution, use scripts/run-parallel-pipeline.sh
which leverages git worktrees to run multiple tasks simultaneously.
