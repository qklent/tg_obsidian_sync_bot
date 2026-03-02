#!/bin/bash
set -euo pipefail

NOTES_DIR="${NOTES_DIR:-/home/qklent/programming/Notes/Notes/tg_sync_bot}"
PROJECT_DIR="${PROJECT_DIR:-.}"
TG_BOT_TOKEN="${TG_BOT_TOKEN:-}"
TG_CHAT_ID="${TG_CHAT_ID:-}"
LOG_FILE="./logs/pipeline-$(date +%Y%m%d-%H%M%S).log"

mkdir -p ./logs

# Telegram notification function
notify() {
    local message="$1"
    echo "[$(date '+%H:%M:%S')] $message" | tee -a "$LOG_FILE"
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TG_CHAT_ID" \
            -d text="$message" \
            -d parse_mode="Markdown" > /dev/null 2>&1 || true
    fi
}

# Update YAML frontmatter field in a task file
update_field() {
    local file="$1" field="$2" value="$3"
    if grep -q "^${field}:" "$file"; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^${field}:.*|${field}: ${value}|" "$file"
        else
            sed -i "s|^${field}:.*|${field}: ${value}|" "$file"
        fi
    fi
}

notify "Pipeline started — scanning for todo tasks..."

processed=0
failed=0

for task_file in "$NOTES_DIR"/*.md; do
    [[ "$task_file" == *"board.md" ]] && continue
    [[ "$task_file" == *"_template.md" ]] && continue
    [[ "$task_file" == *"_codemap.md" ]] && continue
    [ -f "$task_file" ] || continue

    status=$(grep -m1 "^status:" "$task_file" | awk '{print $2}' || echo "")
    [ "$status" = "todo" ] || continue

    task_name=$(basename "$task_file" .md)
    notify "Starting: $task_name"

    # Update status
    update_field "$task_file" "status" "in-progress"
    ./scripts/sync-board.sh

    # Checkout fresh branch
    cd "$PROJECT_DIR"
    git checkout master && git pull origin master
    git checkout -b "feature/$task_name" 2>/dev/null || git checkout "feature/$task_name"

    # Run Claude Code pipeline
    if claude -p "
        Read the task file at $task_file.

        Execute this pipeline:
        0. Use doc-updater agent to regenerate notes/_codemap.md
        1. Use planner agent to create implementation plan — write it to the task file
        2. Run /compact to free context before coding
        3. Use coder agent to implement the plan step by step
        4. Use test-runner agent to run tests and fix failures
        5. Use code-reviewer agent to review — if Critical/High issues found,
           have coder fix them, re-test, re-review (max 3 cycles)
        6. Use doc-updater agent to update docs and codemap
        7. Use git-workflow agent to commit, push branch, create PR via gh CLI
        8. Update the task file: set pr_url and branch fields

        Output the PR URL as the last line.
    " --allowedTools "Bash,Read,Write,Edit,Grep,Glob" \
      --permission-mode acceptEdits 2>&1 | tee -a "$LOG_FILE"; then

        # Extract PR URL
        PR_URL=$(grep -oP 'https://github.com/[^\s]+/pull/\d+' "$LOG_FILE" | tail -1 || echo "unknown")
        update_field "$task_file" "status" "review"
        update_field "$task_file" "pr_url" "$PR_URL"
        ./scripts/sync-board.sh

        notify "Ready for review: $task_name — $PR_URL"
        ((processed++))
    else
        # Retry logic
        retry_count=$(grep -m1 "^retry_count:" "$task_file" | awk '{print $2}' || echo "0")
        max_retries=$(grep -m1 "^max_retries:" "$task_file" | awk '{print $2}' || echo "3")

        if [ "$retry_count" -lt "$max_retries" ]; then
            new_count=$((retry_count + 1))
            update_field "$task_file" "retry_count" "$new_count"
            update_field "$task_file" "status" "todo"
            notify "Retrying ($new_count/$max_retries): $task_name"
        else
            update_field "$task_file" "status" "failed"
            notify "Failed (max retries): $task_name — needs manual intervention"
        fi
        ./scripts/sync-board.sh
        ((failed++))
    fi

    git checkout master
done

./scripts/sync-board.sh
notify "Pipeline complete: $processed succeeded, $failed failed"
