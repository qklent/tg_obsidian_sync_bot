#!/bin/bash
# Runs multiple tasks in parallel using git worktrees.
# Each task gets its own worktree so Claude instances don't conflict.

set -euo pipefail

# Load .env if present
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

NOTES_DIR="${NOTES_DIR:-/home/qklent/programming/Notes/Notes/tg_sync_bot}"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
WORKTREE_BASE="../pipeline-worktrees"

mkdir -p "$WORKTREE_BASE"

# Structured JSON logging helper (includes worker_id field)
log_json() {
    local level="$1" step="$2" msg="$3" worker="${4:-}"
    printf '{"timestamp":"%s","level":"%s","task":"%s","step":"%s","worker_id":"%s","message":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "" "$step" "$worker" "$msg"
}

# Collect todo tasks
todo_tasks=()
for task_file in "$NOTES_DIR"/*.md; do
    [[ "$task_file" == *"board.md" ]] && continue
    [[ "$task_file" == *"_template.md" ]] && continue
    [[ "$task_file" == *"_codemap.md" ]] && continue
    [ -f "$task_file" ] || continue

    status=$(grep -m1 "^status:" "$task_file" | awk '{print $2}' || echo "")
    [ "$status" = "todo" ] && todo_tasks+=("$task_file")
done

log_json "INFO" "startup" "Found ${#todo_tasks[@]} todo tasks, running up to $MAX_PARALLEL in parallel"

running=0
worker_id=0
for task_file in "${todo_tasks[@]}"; do
    task_name=$(basename "$task_file" .md)
    worktree_dir="$WORKTREE_BASE/$task_name"
    branch="feature/$task_name"
    (( ++worker_id ))
    wid="worker-$worker_id"

    # Create worktree
    git branch "$branch" 2>/dev/null || true
    git worktree add "$worktree_dir" "$branch" 2>/dev/null || {
        log_json "WARN" "worktree" "Worktree already exists for $task_name, skipping" "$wid"
        continue
    }

    log_json "INFO" "start" "Launching $task_name" "$wid"

    # Run pipeline in worktree (background)
    (
        cd "$worktree_dir"
        NOTES_DIR="$NOTES_DIR" PROJECT_DIR="$worktree_dir" TASK_ID="$task_name" \
            ./scripts/run-task-pipeline.sh

        # Cleanup worktree when done
        cd -
        git worktree remove "$worktree_dir" 2>/dev/null || true
        log_json "INFO" "done" "Finished $task_name" "$wid"
    ) &

    (( ++running ))
    if [ "$running" -ge "$MAX_PARALLEL" ]; then
        wait -n  # Wait for any one to finish
        (( running-- )) || true
    fi
done

wait  # Wait for all remaining
log_json "INFO" "summary" "All parallel tasks complete"
