#!/bin/bash
# Interactive script for the Planning stage
# Usage: ./scripts/clarify-task.sh notes/my-task.md

TASK_FILE="$1"

if [ -z "$TASK_FILE" ]; then
    echo "Usage: $0 <task-file>"
    echo "Available planning tasks:"
    grep -l "^status: planning" /home/qklent/programming/Notes/Notes/tg_sync_bot/*.md 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "Starting interactive clarification for: $TASK_FILE"
echo "Claude will ask you questions to refine this task."
echo "---"

# This runs interactively (no -p flag) so you can answer questions
claude "/project:clarify-task $TASK_FILE"
