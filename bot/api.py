"""HTTP API server for remote project synchronization.

Provides endpoints for creating/updating tasks, syncing changelogs, and health
checks. Runs as an aiohttp web app alongside the Telegram bot.
"""

import hmac
import logging
import time
from typing import Any

from aiohttp import web

from bot.api_models import ApiResponse, ContentSyncRequest, TaskCreateRequest, TaskUpdateRequest
from bot.git_sync import GitSync
from bot.project_sync import ProjectSync

logger = logging.getLogger(__name__)

_start_time = time.monotonic()


def _json(response: ApiResponse, status: int = 200) -> web.Response:
    """Shorthand for returning a JSON response."""
    import json
    return web.Response(
        text=json.dumps(response.to_dict()),
        content_type="application/json",
        status=status,
    )


def _ok(data: dict | None = None) -> web.Response:
    return _json(ApiResponse(ok=True, data=data))


def _error(message: str, status: int = 400) -> web.Response:
    return _json(ApiResponse(ok=False, error=message), status=status)


@web.middleware
async def auth_middleware(request: web.Request, handler: Any) -> web.Response:
    """Bearer token authentication middleware.

    Skips auth for the health endpoint.
    """
    if request.path == "/api/health":
        return await handler(request)

    api_secret: str = request.app["api_secret"]
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return _error("Missing or invalid Authorization header", status=401)

    token = auth_header[7:]  # strip "Bearer "
    if not hmac.compare_digest(token, api_secret):
        return _error("Invalid API secret", status=403)

    return await handler(request)


async def handle_health(request: web.Request) -> web.Response:
    git_sync: GitSync = request.app["git_sync"]
    uptime = int(time.monotonic() - _start_time)
    return _ok({
        "git_dirty": git_sync._dirty,
        "uptime_seconds": uptime,
    })


async def handle_task_create(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    required = ("project", "task_id", "title")
    missing = [f for f in required if f not in body]
    if missing:
        return _error(f"Missing required fields: {missing}")

    req = TaskCreateRequest(
        project=body["project"],
        task_id=body["task_id"],
        title=body["title"],
        description=body.get("description", ""),
        status=body.get("status", "todo"),
        priority=body.get("priority", "medium"),
        tags=body.get("tags", []),
    )

    project_sync: ProjectSync = request.app["project_sync"]
    git_sync: GitSync = request.app["git_sync"]

    try:
        note_path = project_sync.create_task(req)
    except Exception as e:
        logger.exception("task_create_failed")
        return _error(f"Failed to create task: {e}", status=500)

    git_sync.mark_dirty()

    # Send Telegram notification
    await _notify(request, f"📋 Task created: **{req.task_id}** {req.title}\nProject: {req.project} | Status: {req.status}")

    return _ok({"path": str(note_path.relative_to(project_sync.vault_path))})


async def handle_task_update(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    required = ("project", "task_id", "new_status")
    missing = [f for f in required if f not in body]
    if missing:
        return _error(f"Missing required fields: {missing}")

    req = TaskUpdateRequest(
        project=body["project"],
        task_id=body["task_id"],
        new_status=body["new_status"],
        comment=body.get("comment", ""),
    )

    project_sync: ProjectSync = request.app["project_sync"]
    git_sync: GitSync = request.app["git_sync"]

    try:
        note_path = project_sync.update_task(req)
    except Exception as e:
        logger.exception("task_update_failed")
        return _error(f"Failed to update task: {e}", status=500)

    if note_path is None:
        return _error(f"Task {req.task_id} not found in project {req.project}", status=404)

    git_sync.mark_dirty()

    emoji = {"done": "✅", "in-progress": "⚙️", "review": "👀", "failed": "❌", "todo": "📥", "planning": "📋"}.get(req.new_status, "🔄")
    await _notify(request, f"{emoji} Task updated: **{req.task_id}** → {req.new_status}\nProject: {req.project}")

    return _ok({"path": str(note_path.relative_to(project_sync.vault_path))})


async def handle_changelog_sync(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    if "project" not in body or "changelog_content" not in body:
        return _error("Missing required fields: project, changelog_content")

    req = ContentSyncRequest(project=body["project"], changelog_content=body["changelog_content"])
    project_sync: ProjectSync = request.app["project_sync"]
    git_sync: GitSync = request.app["git_sync"]

    try:
        path = project_sync.sync_changelog(req)
    except Exception as e:
        logger.exception("changelog_sync_failed")
        return _error(f"Failed to sync changelog: {e}", status=500)

    git_sync.mark_dirty()
    await _notify(request, f"📝 Changelog synced for **{req.project}**")

    return _ok({"path": str(path.relative_to(project_sync.vault_path))})


async def handle_changes_sync(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body")

    if "project" not in body or "changes_content" not in body:
        return _error("Missing required fields: project, changes_content")

    req = ContentSyncRequest(project=body["project"], changes_content=body["changes_content"])
    project_sync: ProjectSync = request.app["project_sync"]
    git_sync: GitSync = request.app["git_sync"]

    try:
        path = project_sync.sync_changes(req)
    except Exception as e:
        logger.exception("changes_sync_failed")
        return _error(f"Failed to sync changes: {e}", status=500)

    git_sync.mark_dirty()
    await _notify(request, f"📝 Changes synced for **{req.project}**")

    return _ok({"path": str(path.relative_to(project_sync.vault_path))})


async def _notify(request: web.Request, text: str) -> None:
    """Send a Telegram notification if bot and chat_id are configured."""
    bot = request.app.get("bot")
    chat_id = request.app.get("notify_chat_id")
    if bot and chat_id:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception:
            logger.exception("telegram_notification_failed")


def create_api_app(
    git_sync: GitSync,
    project_sync: ProjectSync,
    api_secret: str,
    bot: object | None = None,
    notify_chat_id: int | None = None,
) -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application(middlewares=[auth_middleware])

    # Store dependencies
    app["git_sync"] = git_sync
    app["project_sync"] = project_sync
    app["api_secret"] = api_secret
    app["bot"] = bot
    app["notify_chat_id"] = notify_chat_id

    # Register routes
    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/task/create", handle_task_create)
    app.router.add_post("/api/task/update", handle_task_update)
    app.router.add_post("/api/changelog/sync", handle_changelog_sync)
    app.router.add_post("/api/changes/sync", handle_changes_sync)

    return app
