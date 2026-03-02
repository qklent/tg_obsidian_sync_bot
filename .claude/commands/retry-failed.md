---
description: Retry all failed/stuck in-progress tasks
---
List all .md files in /home/qklent/programming/Notes/Notes/ with status: in-progress.

For each:
- Check retry_count vs max_retries in YAML frontmatter
- If retry_count < max_retries:
  * Increment retry_count
  * Reset status to "todo"
  * Report: retrying task
- If retry_count >= max_retries:
  * Set status to "failed"
  * Report: task exceeded max retries, needs manual intervention

Run scripts/sync-board.sh to update the Kanban board.
Output summary of retried and failed tasks.
