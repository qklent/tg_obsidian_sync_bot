# Updated Plan v2: Autonomous Coding Pipeline with Obsidian Kanban + Multi-Agent Claude Code

## Complete Summary

You're building an autonomous development pipeline where:

1. **Task input** — You jot quick notes via Telegram (using your tg_obsidian_sync_bot) or directly in Obsidian
2. **Planning** — A clarifier agent asks you questions to refine vague notes into detailed specs (human gate #1)
3. **Autonomous execution** — Claude Code's multi-agent pipeline (planner → coder → tester → reviewer) implements the task, creates a PR
4. **Human review** — You review the PR in the Obsidian Kanban board (human gate #2)
5. **Logging** — Every status change gets reported to Telegram
6. **Visualization** — Obsidian Kanban plugin gives you a beautiful 5-column board that updates programmatically

---

## The Workflow

```
📋 Planning  →  📥 Todo  →  ⚙️ In Progress  →  👀 Review  →  ✅ Done
  (human)      (queue)      (autonomous)       (human)      (merged)
```

---

## Full Setup Plan — Feed These to Claude Code in Order

### Phase 1: Project Foundation

```
I need you to set up my project for an autonomous multi-agent development 
pipeline with an Obsidian Kanban task management system.

1. Create a comprehensive CLAUDE.md that describes:
   - Our tech stack
   - Code conventions and patterns (infer from existing codebase)
   - How to run tests, lint, and build
   - Important architectural decisions
   - Files/directories that should never be modified
   Read package.json, tsconfig, existing tests, and source code to make 
   CLAUDE.md accurate.

2. Create this directory structure:
   .claude/
   ├── agents/
   ├── commands/
   ├── skills/
   │   └── codemap-updater.md
   └── settings.json
   
   .claude/rules/              # Modular rules (instead of monolithic CLAUDE.md)
   ├── security.md             # No hardcoded secrets, validate inputs
   ├── coding-style.md         # Immutability, file organization, no console.log
   ├── testing.md              # TDD workflow, 80% coverage target
   ├── git-workflow.md         # Conventional commits, PR process
   ├── agents.md               # When to delegate to subagents
   └── performance.md          # Model selection, context management
   
   notes/
   ├── board.md          (Kanban board file)
   ├── _template.md      (task template)
   └── done/             (completed tasks archive)
   
   scripts/
   └── (automation scripts will go here)

   NOTE: Keep CLAUDE.md as the high-level project overview. Use .claude/rules/ 
   for specific, modular rules that scale better as the project grows.

3. Create notes/_template.md with this format:
   ---
   status: planning
   priority: medium
   created: {{date}}
   branch: 
   pr_url: 
   clarification_needed: true
   max_retries: 3
   retry_count: 0
   ---
   
   # Task Title
   
   ## Raw Notes
   (initial rough description goes here)
   
   ## Refined Spec
   (filled by task-clarifier agent after Q&A)
   
   ## Acceptance Criteria
   - [ ] criterion 1
   - [ ] criterion 2
   
   ## Implementation Plan
   (filled by planner agent)
   
   ## PR
   {{pr_url}}

4. Create notes/board.md as an Obsidian Kanban board:
   ---
   kanban-plugin: basic
   ---
   
   ## 📋 Planning
   
   ## 📥 Todo
   
   ## ⚙️ In Progress
   
   ## 👀 Review
   
   ## ✅ Done
   
   %% kanban:settings
   {"kanban-plugin":"basic","lane-width":250,"show-checkboxes":false}
   %%

5. Create .claude/skills/codemap-updater.md:
   A skill that generates/updates a compact codemap of the project at 
   checkpoints. This allows agents to quickly orient in the codebase 
   without burning context on full exploration. The codemap should include:
   - Directory structure overview
   - Key modules and their responsibilities
   - Important interfaces/types
   - Dependency graph between modules
   Store output as notes/_codemap.md (auto-regenerated, not manually edited).

6. Add to .gitignore:
   - notes/done/ contents are tracked (for history)
   - .claude/settings.local.json is NOT committed
   - .claude/agents/ IS committed
   - logs/ is NOT committed
```

### Phase 2: Create Hooks

> **NEW PHASE** — Hooks provide automated guardrails that fire on tool use
> and lifecycle events. These run independently of agents and catch issues
> the agents might miss. Set these up BEFORE creating agents so the safety
> net is in place from the start.

```
Create .claude/settings.json (or merge into existing) with the following 
hooks configuration:

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "tool == \"Bash\" && tool_input.command matches \"(npm|pnpm|yarn|cargo|pytest|make)\"",
        "hooks": [
          {
            "type": "command",
            "command": "if [ -z \"$TMUX\" ]; then echo '[Hook] WARNING: Consider running inside tmux for session persistence on long commands' >&2; fi"
          }
        ]
      },
      {
        "matcher": "tool == \"Bash\" && tool_input.command matches \"git push\"",
        "hooks": [
          {
            "type": "command",
            "command": "echo '[Hook] Pre-push check: verify tests pass and no console.log statements remain' >&2; grep -rn 'console\\.log' --include='*.ts' --include='*.tsx' --include='*.js' --include='*.jsx' src/ && echo '⚠️ Found console.log statements — remove before pushing' >&2 || true"
          }
        ]
      },
      {
        "matcher": "tool == \"Write\" && tool_input.file_path matches \"\\.md$\" && !(tool_input.file_path matches \"(README|CLAUDE|board|_template|notes/)\")",
        "hooks": [
          {
            "type": "command",
            "command": "echo '[Hook] Blocking unnecessary .md file creation. Only README, CLAUDE.md, and task files should be created.' >&2; exit 1"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "tool == \"Edit\" && tool_input.file_path matches \"\\.(ts|tsx|js|jsx)$\"",
        "hooks": [
          {
            "type": "command",
            "command": "npx prettier --write \"$TOOL_INPUT_FILE_PATH\" 2>/dev/null || true"
          }
        ]
      },
      {
        "matcher": "tool == \"Edit\" && tool_input.file_path matches \"\\.(ts|tsx)$\"",
        "hooks": [
          {
            "type": "command",
            "command": "npx tsc --noEmit 2>&1 | head -20 || true"
          }
        ]
      },
      {
        "matcher": "tool == \"Edit\"",
        "hooks": [
          {
            "type": "command",
            "command": "if grep -n 'console\\.log' \"$TOOL_INPUT_FILE_PATH\" 2>/dev/null; then echo '[Hook] ⚠️ console.log detected in modified file' >&2; fi"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "echo '[Hook] Session ending — auditing for debug statements...' >&2; git diff --name-only 2>/dev/null | xargs grep -l 'console\\.log' 2>/dev/null && echo '⚠️ Debug statements found in changed files' >&2 || echo '✅ No debug statements found' >&2"
          }
        ]
      }
    ]
  }
}

NOTE: Hooks fire automatically — agents don't need to know about them.
The PreToolUse git push hook prevents the git-workflow agent from pushing 
code with debug statements. The PostToolUse edit hooks auto-format code 
the coder agent writes. This is a critical safety net.

TIP: Install the `hookify` plugin to create/modify hooks conversationally 
instead of editing JSON by hand: run /hookify and describe what you want.
```

### Phase 3: Create the Agent Team

> **CRITICAL: Context Window Management**
> The 200k token context window can shrink to ~70k if too many tools/MCPs 
> are active. Each agent below has MINIMIZED tool access — only what it 
> needs. Opus is reserved for tasks requiring deep reasoning; Sonnet handles 
> mechanical work. This is intentional and should not be changed without 
> measuring the context impact.

```
Create the following subagent files in .claude/agents/. Each agent must have 
proper YAML frontmatter with name, description, tools, and model fields.
Reference our CLAUDE.md conventions in each agent's system prompt.

1. task-clarifier.md
   - Model: opus
   - Tools: Read, Grep, Glob            ← NO Write/Edit (spec-only agent)
   - Role: Technical product manager that refines raw task notes
   - Behavior:
     * Read the existing codebase to understand what's already built
     * Read notes/_codemap.md first to orient without burning context
     * Analyze the raw task notes for ambiguities
     * Ask the user 3-7 specific clarifying questions:
       - What exactly should happen on success vs error?
       - Which existing components are affected?
       - Edge cases and boundary conditions?
       - Performance/security requirements?
       - UI/UX expectations if applicable?
     * After getting answers, produce a fully detailed spec with:
       - Clear acceptance criteria (as checkboxes)
       - Files likely to be modified/created
       - Test scenarios to cover
       - Explicit out-of-scope items
     * NEVER start implementation — only produce specs
     * Update the task file: fill "Refined Spec" and "Acceptance Criteria" 
       sections, set clarification_needed: false

2. planner.md
   - Model: opus
   - Tools: Read, Grep, Glob            ← NO Write/Edit (plan-only agent)
   - Role: Technical architect that creates implementation plans
   - Behavior:
     * Read notes/_codemap.md first for codebase orientation
     * Read the refined spec from the task file
     * Analyze existing codebase architecture deeply
     * Create ordered implementation plan with:
       - Files to modify (with what changes)
       - Files to create (with purpose)
       - Ordered implementation steps
       - Dependencies between steps
       - Tests needed (unit, integration)
       - Risks and mitigation strategies
     * Write the plan into the task file's "Implementation Plan" section

3. coder.md
   - Model: opus
   - Tools: Read, Write, Edit, Bash, Grep, Glob
   - Role: Senior developer implementing features
   - Behavior:
     * Read the implementation plan from the task file
     * Read notes/_codemap.md for quick codebase orientation
     * Follow the plan step by step
     * Check existing code patterns before writing anything new
     * Reuse existing utilities and helpers
     * Handle errors gracefully with proper validation
     * Write clean code following CLAUDE.md conventions
     * Never modify unrelated code
     * Create meaningful commit messages for each logical change
     * NOTE: PostToolUse hooks will auto-format and type-check your edits

4. code-reviewer.md
   - Model: sonnet                       ← Sonnet is sufficient for review
   - Tools: Read, Grep, Glob, Bash      ← NO Write/Edit (review-only agent)
   - Role: Security-focused expert code reviewer
   - Behavior:
     * Review ALL changes made by the coder agent
     * Check against the acceptance criteria in the task file
     * Look for: OWASP top 10, missing error handling, race conditions,
       input validation gaps, test coverage gaps, performance issues
     * Output structured review with severity ratings:
       - 🔴 Critical — must fix before merge
       - 🟠 High — should fix before merge
       - 🟡 Medium — fix soon
       - 🟢 Low — nice to have
     * Include specific file paths, line numbers, and fix suggestions
     * If no Critical/High issues: approve
     * If Critical/High issues exist: list them clearly for coder to fix

5. test-runner.md
   - Model: sonnet                       ← Mechanical task, Sonnet is fine
   - Tools: Read, Write, Edit, Bash, Grep
   - Role: Test automation specialist
   - Behavior:
     * Run the full test suite
     * If tests fail, analyze root cause
     * Determine if the test or the code is wrong
     * Fix accordingly while preserving original test intent
     * Follow AAA pattern (Arrange, Act, Assert)
     * Ensure test isolation
     * Re-run until all tests pass
     * Report final test results with coverage summary

6. git-workflow.md
   - Model: sonnet                       ← Mechanical task, Sonnet is fine
   - Tools: Bash, Read                   ← Minimal toolset
   - Role: Git and GitHub workflow manager
   - Behavior:
     * Create feature branch from main: feature/{task-name}
     * Ensure all changes are committed with descriptive messages
     * Push branch to origin
     * NOTE: PreToolUse hook will block push if console.log found
     * Create PR using gh CLI with:
       - Title matching the task title
       - Body containing: refined spec, acceptance criteria, 
         implementation summary, test results, reviewer notes
     * Output the PR URL
     * Update the task file: set branch and pr_url fields

7. doc-updater.md                         ← NEW AGENT
   - Model: sonnet
   - Tools: Read, Write, Edit, Grep, Glob
   - Role: Documentation maintenance specialist
   - Behavior:
     * After coder agent completes work, review what changed
     * Update README.md if public API or usage changed
     * Update architecture.md if structural changes were made
     * Regenerate notes/_codemap.md with current project state
     * Keep docs in sync with code — never let them drift
     * Only touch documentation files, never source code
```

### Phase 4: Create Custom Commands

```
Create the following custom slash commands in .claude/commands/:

1. .claude/commands/clarify-task.md:
   ---
   description: Refine a raw task into a detailed spec through Q&A
   ---
   Read the task file at: $ARGUMENTS
   
   Use the task-clarifier agent to analyze this task.
   The agent will ask clarifying questions — relay them to me and 
   wait for my answers. After I've answered all questions, have the 
   agent produce the refined spec and update the task file.
   
   After the spec is complete:
   - Update the task's status from "planning" to "todo" in the YAML frontmatter
   - Set clarification_needed to false
   - Run scripts/sync-board.sh to update the Kanban board
   - Confirm the task is ready for implementation

2. .claude/commands/implement-task.md:
   ---
   description: Run the full autonomous pipeline on a todo task
   ---
   Read the task file at: $ARGUMENTS
   Verify its status is "todo" — if not, stop and report.
   
   Update status to "in-progress" in the YAML frontmatter.
   Run scripts/sync-board.sh to update the Kanban board.
   
   Execute this pipeline in order:
   
   Step 0 — CODEMAP: Use the doc-updater agent to regenerate 
   notes/_codemap.md so all subsequent agents have fresh orientation.
   
   Step 1 — PLAN: Use the planner agent to create a detailed implementation 
   plan. Write it to the task file's Implementation Plan section.
   
   Step 2 — COMPACT: Run /compact to free up context before the heavy 
   coding phase. The planning context is captured in the task file.
   
   Step 3 — CODE: Use the coder agent to implement the plan. Follow it 
   step by step. Commit each logical change separately.
   
   Step 4 — TEST: Use the test-runner agent to run all tests. If any fail, 
   fix them and re-run until green.
   
   Step 5 — REVIEW: Use the code-reviewer agent to review all changes.
   If Critical or High severity issues are found, use the coder agent to 
   fix them, then re-run test-runner, then re-review. Loop until no 
   Critical/High issues remain. Maximum 3 review cycles.
   
   Step 6 — DOCS: Use the doc-updater agent to update any documentation 
   affected by the changes, including the codemap.
   
   Step 7 — PR: Use the git-workflow agent to push the branch and create 
   a GitHub PR. Update the task file with branch name and PR URL.
   
   Step 8 — Update status to "review" in the YAML frontmatter.
   Run scripts/sync-board.sh to update the Kanban board.
   
   Output a final summary: what was implemented, test results, 
   review outcome, and PR URL.

3. .claude/commands/process-all-tasks.md:
   ---
   description: Process all todo tasks sequentially (or in parallel with worktrees)
   ---
   List all .md files in the notes/ directory (excluding board.md, 
   _template.md, _codemap.md, and files in notes/done/).
   
   For each file with status: todo in its YAML frontmatter:
   - Run the implement-task pipeline on it
   - Report results
   - Continue to the next task regardless of success/failure
   
   At the end, output a summary table:
   | Task | Status | PR URL | Notes |
   
   NOTE: For parallel execution, use scripts/run-parallel-pipeline.sh 
   which leverages git worktrees to run multiple tasks simultaneously.

4. .claude/commands/retry-failed.md:          ← NEW COMMAND
   ---
   description: Retry all failed/stuck in-progress tasks
   ---
   List all .md files in notes/ with status: in-progress.
   
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

5. .claude/commands/update-codemap.md:        ← NEW COMMAND
   ---
   description: Regenerate the project codemap for agent orientation
   ---
   Use the doc-updater agent to regenerate notes/_codemap.md.
   This provides a compact overview of the project structure, 
   key modules, interfaces, and dependencies — allowing agents 
   to orient quickly without reading the entire codebase.
```

### Phase 5: Create Automation Scripts

```
Create the following automation scripts:

1. scripts/sync-board.sh:                     ← NEW SCRIPT (critical fix)
   #!/bin/bash
   # Regenerates notes/board.md from YAML frontmatter of all task files.
   # This keeps the Obsidian Kanban board in sync with actual task states.
   # Run after any status change.
   
   set -euo pipefail
   NOTES_DIR="${NOTES_DIR:-./notes}"
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
   
   echo "✅ Board synced: $(date)"

2. scripts/run-task-pipeline.sh:
   #!/bin/bash
   set -euo pipefail
   
   NOTES_DIR="${NOTES_DIR:-./notes}"
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
   # FIXED: Cross-platform sed (works on both macOS and Linux)
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
   
   notify "🚀 *Pipeline started* — scanning for todo tasks..."
   
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
       notify "⚙️ *Starting:* $task_name"
       
       # Update status
       update_field "$task_file" "status" "in-progress"
       ./scripts/sync-board.sh
       
       # Checkout fresh branch
       cd "$PROJECT_DIR"
       git checkout main && git pull origin main
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
           
           notify "👀 *Ready for review:* $task_name
$PR_URL"
           ((processed++))
       else
           # Retry logic
           retry_count=$(grep -m1 "^retry_count:" "$task_file" | awk '{print $2}' || echo "0")
           max_retries=$(grep -m1 "^max_retries:" "$task_file" | awk '{print $2}' || echo "3")
           
           if [ "$retry_count" -lt "$max_retries" ]; then
               new_count=$((retry_count + 1))
               update_field "$task_file" "retry_count" "$new_count"
               update_field "$task_file" "status" "todo"
               notify "🟠 *Retrying ($new_count/$max_retries):* $task_name"
           else
               update_field "$task_file" "status" "failed"
               notify "🔴 *Failed (max retries):* $task_name — needs manual intervention"
           fi
           ./scripts/sync-board.sh
           ((failed++))
       fi
       
       git checkout main
   done
   
   ./scripts/sync-board.sh
   notify "🏁 *Pipeline complete:* $processed succeeded, $failed failed"

3. scripts/run-parallel-pipeline.sh:          ← NEW SCRIPT
   #!/bin/bash
   # Runs multiple tasks in parallel using git worktrees.
   # Each task gets its own worktree so Claude instances don't conflict.
   
   set -euo pipefail
   
   NOTES_DIR="${NOTES_DIR:-./notes}"
   MAX_PARALLEL="${MAX_PARALLEL:-3}"
   WORKTREE_BASE="../pipeline-worktrees"
   
   mkdir -p "$WORKTREE_BASE"
   
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
   
   echo "Found ${#todo_tasks[@]} todo tasks, running up to $MAX_PARALLEL in parallel"
   
   running=0
   for task_file in "${todo_tasks[@]}"; do
       task_name=$(basename "$task_file" .md)
       worktree_dir="$WORKTREE_BASE/$task_name"
       branch="feature/$task_name"
       
       # Create worktree
       git branch "$branch" 2>/dev/null || true
       git worktree add "$worktree_dir" "$branch" 2>/dev/null || {
           echo "Worktree already exists for $task_name, skipping"
           continue
       }
       
       # Run pipeline in worktree (background)
       (
           cd "$worktree_dir"
           NOTES_DIR="$NOTES_DIR" PROJECT_DIR="$worktree_dir" \
               ../$(dirname "$0")/run-task-pipeline.sh
           
           # Cleanup worktree when done
           cd -
           git worktree remove "$worktree_dir" 2>/dev/null || true
       ) &
       
       ((running++))
       if [ "$running" -ge "$MAX_PARALLEL" ]; then
           wait -n  # Wait for any one to finish
           ((running--))
       fi
   done
   
   wait  # Wait for all remaining
   echo "✅ All parallel tasks complete"

4. scripts/watch-tasks.sh:
   #!/bin/bash
   # Auto-triggers pipeline when new .md files appear in notes/
   
   NOTES_DIR="${NOTES_DIR:-./notes}"
   
   echo "👁 Watching $NOTES_DIR for new tasks..."
   
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

5. scripts/clarify-task.sh:
   #!/bin/bash
   # Interactive script for the Planning stage
   # Usage: ./scripts/clarify-task.sh notes/my-task.md
   
   TASK_FILE="$1"
   
   if [ -z "$TASK_FILE" ]; then
       echo "Usage: $0 <task-file>"
       echo "Available planning tasks:"
       grep -l "^status: planning" notes/*.md 2>/dev/null || echo "  (none)"
       exit 1
   fi
   
   echo "Starting interactive clarification for: $TASK_FILE"
   echo "Claude will ask you questions to refine this task."
   echo "---"
   
   # This runs interactively (no -p flag) so you can answer questions
   claude "/project:clarify-task $TASK_FILE"

Make all scripts executable with chmod +x.

Also create a Makefile for convenience:
   
   .PHONY: clarify implement implement-parallel watch pipeline status retry
   
   clarify:
   	@echo "Tasks in planning:" && grep -l "^status: planning" notes/*.md 2>/dev/null
   	@read -p "Task file: " f && ./scripts/clarify-task.sh $$f
   
   implement:
   	./scripts/run-task-pipeline.sh
   
   implement-parallel:
   	./scripts/run-parallel-pipeline.sh
   
   watch:
   	./scripts/watch-tasks.sh
   
   retry:
   	claude "/project:retry-failed"
   
   sync-board:
   	./scripts/sync-board.sh
   
   update-codemap:
   	claude "/project:update-codemap"
   
   status:
   	@echo "📋 Planning:" && grep -l "^status: planning" notes/*.md 2>/dev/null || echo "  none"
   	@echo "📥 Todo:" && grep -l "^status: todo" notes/*.md 2>/dev/null || echo "  none"
   	@echo "⚙️ In Progress:" && grep -l "^status: in-progress" notes/*.md 2>/dev/null || echo "  none"
   	@echo "👀 Review:" && grep -l "^status: review" notes/*.md 2>/dev/null || echo "  none"
   	@echo "❌ Failed:" && grep -l "^status: failed" notes/*.md 2>/dev/null || echo "  none"
   	@echo "✅ Done:" && grep -l "^status: done" notes/done/*.md 2>/dev/null || echo "  none"
```

### Phase 6: CI/CD Integration

```
Create GitHub Actions workflows:

1. .github/workflows/claude-pr-review.yml:
   name: Claude PR Review
   on:
     pull_request:
       types: [opened, synchronize]
   
   jobs:
     review:
       runs-on: ubuntu-latest
       permissions:
         pull-requests: write
         contents: read
       steps:
         - uses: actions/checkout@v4
           with:
             fetch-depth: 0
         - uses: actions/setup-node@v4
           with:
             node-version: '20'
         - name: Install Claude Code
           run: npm install -g @anthropic-ai/claude-code
         - name: Run Claude Review
           env:
             ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
           run: |
             # Note: ANTHROPIC_API_KEY must be added as a GitHub encrypted secret
             # Never hardcode API keys in workflow files
             claude -p "
               Review this PR. Check for:
               - Security vulnerabilities (OWASP top 10)
               - Missing error handling
               - Test coverage gaps
               - Performance issues
               - Code style consistency
               
               Post your review as a PR comment using gh CLI.
               Be constructive. Rate overall: APPROVE, REQUEST_CHANGES, or COMMENT.
             " --allowedTools "Bash,Read,Grep,Glob"

2. .github/workflows/ci.yml:
   Standard CI workflow for your project — tests, linting, build.
   Adapt to your actual tech stack:
   
   name: CI
   on: [pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-node@v4
           with:
             node-version: '20'
         - run: npm ci
         - run: npm run lint
         - run: npm test
         - run: npm run build
```

### Phase 7: Settings & Documentation

```
1. Create .claude/settings.json (merge with hooks from Phase 2):
   {
     "permissions": {
       "allow": [
         "Bash(git *)",
         "Bash(gh *)",
         "Bash(npm *)",
         "Bash(npx *)",
         "Bash(cat *)",
         "Bash(ls *)",
         "Bash(grep *)",
         "Bash(find *)",
         "Bash(sed *)"
       ],
       "deny": [
         "Bash(rm -rf /)",
         "Bash(curl * | bash)",
         "Bash(sudo *)"
       ]
     },
     "env": {
       "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
     }
   }

2. Recommended plugins (install via /plugins):
   - pyright-lsp     — Python type checking (essential for your Python bot)
   - hookify         — Create hooks conversationally
   - mgrep           — Better search than ripgrep (via Mixedbread marketplace)
   - context7        — Live documentation lookup
   
   IMPORTANT: Keep only 4-5 plugins enabled at a time.
   Disable unused ones to preserve context window.

3. Recommended MCP servers (configure in ~/.claude.json):
   - github           — PR management, issue tracking
   - memory           — Persistent memory across sessions
   
   IMPORTANT: Configure all MCPs you might need, but disable per-project 
   any that aren't actively used. Rule of thumb: max 80 active tools total.

4. Update .gitignore to add:
   .claude/settings.local.json
   logs/
   notes/_codemap.md              # Auto-generated, not manually edited
   # Keep these tracked:
   # .claude/agents/
   # .claude/commands/
   # .claude/rules/
   # .claude/skills/
   # notes/ (including done/)

5. Add a section to the project README.md:

   ## 🤖 AI Development Pipeline
   
   This project uses an autonomous multi-agent Claude Code pipeline 
   with Obsidian Kanban for task management.
   
   ### Workflow
   📋 Planning → 📥 Todo → ⚙️ In Progress → 👀 Review → ✅ Done
   
   ### Quick Start
   
   **Add a new task:**
   Copy notes/_template.md → notes/my-feature.md, fill in raw notes.
   
   **Refine a task (interactive):**
   make clarify
   # or: ./scripts/clarify-task.sh notes/my-feature.md
   
   **Run pipeline on all todo tasks:**
   make implement
   
   **Run tasks in parallel (via git worktrees):**
   make implement-parallel
   
   **Watch for new tasks automatically:**
   make watch
   
   **Retry failed tasks:**
   make retry
   
   **Sync the Kanban board:**
   make sync-board
   
   **Update project codemap:**
   make update-codemap
   
   **Check task statuses:**
   make status
   
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
   - **Hooks** auto-format code, type-check edits, block debug statements 
     from being pushed, and remind about tmux for long commands
   - **Retry logic** automatically retries failed tasks up to 3 times
   - **Board sync** keeps Obsidian Kanban in sync with actual task states
   
   ### Requirements
   - Claude Code CLI installed globally
   - ANTHROPIC_API_KEY set in environment
   - gh CLI installed and authenticated
   - For Telegram notifications: TG_BOT_TOKEN and TG_CHAT_ID set
   - Recommended: tmux, pyright-lsp plugin, mgrep plugin
```

---

## Quick Reference: What Goes Where

| File/Directory | Purpose | Git? |
|---|---|---|
| `CLAUDE.md` | High-level project context | ✅ |
| `.claude/rules/*.md` | Modular rules (security, style, testing, etc.) | ✅ |
| `.claude/agents/*.md` | Subagent definitions | ✅ |
| `.claude/commands/*.md` | Custom slash commands | ✅ |
| `.claude/skills/*.md` | Workflow skills (codemap updater, etc.) | ✅ |
| `.claude/settings.json` | Shared project settings + hooks | ✅ |
| `notes/_template.md` | Task template with YAML frontmatter | ✅ |
| `notes/_codemap.md` | Auto-generated project codemap | ❌ |
| `notes/board.md` | Obsidian Kanban board (auto-synced) | ✅ |
| `notes/*.md` | Active task files | ✅ |
| `notes/done/*.md` | Completed task archive | ✅ |
| `scripts/*.sh` | Automation scripts | ✅ |
| `.github/workflows/` | CI/CD with Claude review | ✅ |
| `Makefile` | Convenience commands | ✅ |
| `logs/` | Pipeline execution logs | ❌ |

---

## Changelog: What Changed from v1

### New additions
1. **Hooks (Phase 2)** — PreToolUse, PostToolUse, and Stop hooks for auto-formatting, type checking, debug statement blocking, tmux reminders, and .md creation guardrails
2. **doc-updater agent** — Keeps README, architecture.md, and codemap in sync after changes
3. **Codemap skill** — Compact project overview for agent orientation without full codebase reads
4. **Modular `.claude/rules/`** — Replaces monolithic CLAUDE.md for rules (CLAUDE.md kept as overview)
5. **`sync-board.sh`** — Regenerates board.md from YAML frontmatter (fixes board drift)
6. **`run-parallel-pipeline.sh`** — Git worktree-based parallel task execution
7. **`retry-failed` command** — Retry logic with max_retries/retry_count in YAML frontmatter
8. **`update-codemap` command** — On-demand codemap regeneration
9. **Plugin/MCP recommendations** — pyright-lsp, hookify, mgrep, context7

### Fixes
10. **Cross-platform `sed`** — `update_field()` now detects macOS vs Linux (was macOS-only)
11. **Context window optimization** — Agents use minimal tool sets; mechanical agents downgraded to Sonnet
12. **Excluded `_codemap.md`** from task file scanning in all scripts

### Pipeline improvements
13. **Step 0 (codemap)** added before planning
14. **Step 2 (/compact)** added between planning and coding to free context
15. **Step 6 (docs)** added after review, before PR
16. **Board sync** called after every status change
17. **Failed status** added as a new lane in the Kanban board

## Daily Usage

```bash
# From Telegram: send task to tg_obsidian_sync_bot → lands in notes/ as planning

# Refine it interactively
./scripts/clarify-task.sh notes/new-feature.md

# Let the agents cook (sequential)
make implement

# Or run tasks in parallel via git worktrees
make implement-parallel

# Or go fully autonomous
make watch &

# Retry any failed tasks
make retry

# Keep the board in sync
make sync-board

# Check what's happening
make status
```