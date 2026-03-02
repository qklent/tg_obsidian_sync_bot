#!/bin/bash
# Regenerates notes/board.md from YAML frontmatter of all task files.
# This keeps the Obsidian Kanban board in sync with actual task states.
# Run after any status change.

set -euo pipefail
NOTES_DIR="${NOTES_DIR:-/home/qklent/programming/Notes/Notes}"
BOARD_FILE="$NOTES_DIR/board.md"

# Collect tasks by status
declare -A LANES
LANES[planning]=""
LANES[todo]=""
LANES[in-progress]=""
LANES[review]=""
LANES[done]=""
LANES[failed]=""

for task_file in "$NOTES_DIR"/*.md; do
    [[ "$task_file" == *"board.md" ]] && continue
    [[ "$task_file" == *"_template.md" ]] && continue
    [[ "$task_file" == *"_codemap.md" ]] && continue
    [ -f "$task_file" ] || continue

    status=$(grep -m1 "^status:" "$task_file" | awk '{print $2}' || echo "")
    title=$(grep -m1 "^# " "$task_file" | sed 's/^# //' || basename "$task_file" .md)
    filename=$(basename "$task_file")

    [ -z "$status" ] && continue

    LANES[$status]+="- [[$filename|$title]]
"
done

# Also check done/ directory
for task_file in "$NOTES_DIR"/done/*.md; do
    [ -f "$task_file" ] || continue
    title=$(grep -m1 "^# " "$task_file" | sed 's/^# //' || basename "$task_file" .md)
    filename=$(basename "$task_file")
    LANES[done]+="- [[done/$filename|$title]]
"
done

# Write board
cat > "$BOARD_FILE" << EOF
---
kanban-plugin: basic
---

## 📋 Planning

${LANES[planning]}
## 📥 Todo

${LANES[todo]}
## ⚙️ In Progress

${LANES[in-progress]}
## 👀 Review

${LANES[review]}
## ❌ Failed

${LANES[failed]}
## ✅ Done

${LANES[done]}
%% kanban:settings
{"kanban-plugin":"basic","lane-width":250,"show-checkboxes":false}
%%
EOF

echo "Board synced: $(date)"
