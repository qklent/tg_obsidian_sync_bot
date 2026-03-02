---
name: codemap-updater
description: Generates/updates a compact codemap of the project for agent orientation
---

# Codemap Updater

Generate a compact codemap at `notes/_codemap.md` that allows agents to quickly orient in the codebase without burning context on full exploration.

## What to include:

1. **Directory structure overview** — tree of key directories and files
2. **Key modules and their responsibilities** — one-line summary per module
3. **Important interfaces/types** — key classes, dataclasses, TypedDicts
4. **Dependency graph between modules** — which modules import/call which
5. **Entry points** — how the application starts and processes messages

## Process:

1. Use Glob to discover all source files (`bot/**/*.py`, `config/**/*.yaml`, etc.)
2. Read each file to extract module purpose, key classes/functions, and imports
3. Build the dependency graph from import statements
4. Write the codemap to `notes/_codemap.md`

## Output format:

```markdown
# Project Codemap
*Auto-generated — do not edit manually*

## Directory Structure
(tree output)

## Module Responsibilities
| Module | Purpose | Key exports |
|--------|---------|-------------|
| ...    | ...     | ...         |

## Dependency Graph
(module → dependencies)

## Key Interfaces
(important classes, types, config shapes)

## Entry Points
(how the app boots and processes messages)
```
