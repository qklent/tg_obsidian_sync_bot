#!/usr/bin/env python3
"""Stdlib-only HTTP client for the Obsidian sync API.

Designed for RunPod pods where bot dependencies are not installed.
Uses only Python standard library (urllib, json, os, sys).

Usage:
    export OBSIDIAN_API_URL=http://82.25.60.81:8443
    export OBSIDIAN_API_KEY=your-secret

    python sync_client.py health
    python sync_client.py task create <project> <task_id> <title> [description] [--status S] [--priority P] [--tags t1,t2]
    python sync_client.py task update <project> <task_id> <new_status> [comment]
    python sync_client.py changelog sync <project> <path_to_changelog>
    python sync_client.py changes sync <project> <path_to_changes>
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _get_config() -> tuple[str, str]:
    url = os.environ.get("OBSIDIAN_API_URL", "").rstrip("/")
    key = os.environ.get("OBSIDIAN_API_KEY", "")
    if not url:
        print("Error: OBSIDIAN_API_URL not set", file=sys.stderr)
        sys.exit(1)
    if not key:
        print("Error: OBSIDIAN_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return url, key


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url, key = _get_config()
    full_url = f"{url}{path}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        full_url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        try:
            return json.loads(body_text)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"HTTP {e.code}: {body_text}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Connection failed: {e.reason}"}


def cmd_health() -> None:
    result = _request("GET", "/api/health")
    print(json.dumps(result, indent=2))


def cmd_task_create(args: list[str]) -> None:
    if len(args) < 3:
        print("Usage: sync_client.py task create <project> <task_id> <title> [description] [--status S] [--priority P] [--tags t1,t2]", file=sys.stderr)
        sys.exit(1)

    project, task_id, title = args[0], args[1], args[2]
    description = ""
    status = "todo"
    priority = "medium"
    tags: list[str] = []

    i = 3
    # Optional positional: description
    if i < len(args) and not args[i].startswith("--"):
        description = args[i]
        i += 1

    # Named flags
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]
            i += 2
        elif args[i] == "--priority" and i + 1 < len(args):
            priority = args[i + 1]
            i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            tags = [t.strip() for t in args[i + 1].split(",")]
            i += 2
        else:
            i += 1

    body = {
        "project": project,
        "task_id": task_id,
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "tags": tags,
    }
    result = _request("POST", "/api/task/create", body)
    print(json.dumps(result, indent=2))


def cmd_task_update(args: list[str]) -> None:
    if len(args) < 3:
        print("Usage: sync_client.py task update <project> <task_id> <new_status> [comment]", file=sys.stderr)
        sys.exit(1)

    body: dict = {
        "project": args[0],
        "task_id": args[1],
        "new_status": args[2],
    }
    if len(args) > 3:
        body["comment"] = " ".join(args[3:])

    result = _request("POST", "/api/task/update", body)
    print(json.dumps(result, indent=2))


def cmd_changelog_sync(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: sync_client.py changelog sync <project> <path_to_changelog>", file=sys.stderr)
        sys.exit(1)

    project = args[0]
    file_path = args[1]

    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    body = {"project": project, "changelog_content": content}
    result = _request("POST", "/api/changelog/sync", body)
    print(json.dumps(result, indent=2))


def cmd_changes_sync(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: sync_client.py changes sync <project> <path_to_changes>", file=sys.stderr)
        sys.exit(1)

    project = args[0]
    file_path = args[1]

    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    body = {"project": project, "changes_content": content}
    result = _request("POST", "/api/changes/sync", body)
    print(json.dumps(result, indent=2))


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "health":
        cmd_health()
    elif cmd == "task" and len(args) >= 2:
        subcmd = args[1]
        if subcmd == "create":
            cmd_task_create(args[2:])
        elif subcmd == "update":
            cmd_task_update(args[2:])
        else:
            print(f"Unknown task subcommand: {subcmd}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "changelog" and len(args) >= 2 and args[1] == "sync":
        cmd_changelog_sync(args[2:])
    elif cmd == "changes" and len(args) >= 2 and args[1] == "sync":
        cmd_changes_sync(args[2:])
    else:
        print(f"Unknown command: {' '.join(args)}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
