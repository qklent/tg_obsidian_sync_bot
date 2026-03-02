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
