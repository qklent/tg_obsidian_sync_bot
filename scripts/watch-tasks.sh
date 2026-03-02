#!/bin/bash
# Auto-triggers pipeline when new .md files appear in notes/

NOTES_DIR="${NOTES_DIR:-/home/qklent/programming/Notes/Notes}"

echo "Watching $NOTES_DIR for new tasks..."

# Detect OS and use appropriate watcher
if command -v fswatch &> /dev/null; then
    # macOS
    fswatch -o -e "board.md" -e "_template.md" -e "_codemap.md" -e "done/" \
        --event Created "$NOTES_DIR" | while read; do
        echo "New task detected, waiting 5s for file to settle..."
        sleep 5
        ./scripts/run-task-pipeline.sh
    done
elif command -v inotifywait &> /dev/null; then
    # Linux
    while true; do
        inotifywait -q -e create -e moved_to \
            --exclude "(board|_template|_codemap|done)" "$NOTES_DIR"
        echo "New task detected, waiting 5s..."
        sleep 5
        ./scripts/run-task-pipeline.sh
    done
else
    echo "Neither fswatch nor inotifywait found."
    echo "Install: brew install fswatch (macOS) or apt install inotify-tools (Linux)"
    echo "Falling back to polling every 60s..."
    while true; do
        ./scripts/run-task-pipeline.sh
        sleep 60
    done
fi
