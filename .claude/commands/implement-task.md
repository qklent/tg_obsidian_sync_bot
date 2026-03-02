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
