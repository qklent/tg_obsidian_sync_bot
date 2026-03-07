import asyncio
import html as html_module
import random
import time
import uuid
from pathlib import Path

from aiogram import Bot, Router, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from bot.dedup import Deduplicator, DuplicatePair
from bot.llm import LLMClassifier
from bot.vault import VaultWriter
from bot.git_sync import GitSync, PendingMerge
from bot.web_fetch import augment_text_with_urls

router = Router()

_MAX_CONFLICT_PREVIEW = 500  # chars shown per side before truncating
_MAX_DEDUP_PREVIEW = 300
_MAX_NOTE_PREVIEW = 600
_SESSION_TIMEOUT = 1800  # 30 minutes

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_KANBAN_FOLDER = "tg_sync_bot"
_SKIP_FILES = {"board.md", "_template.md", "_codemap.md"}

# Active dedup sessions: {session_id: DedupSession}
_dedup_sessions: dict[str, dict] = {}

# Active inbox review sessions: {session_id: {note_path, folders, created_at}}
_inbox_sessions: dict[str, dict] = {}

# Active clarify sessions: {user_id: {task_path, task_content, questions, created_at}}
_clarify_sessions: dict[int, dict] = {}


async def _run_script(script: str, *args: str, cwd: str) -> tuple[int, str]:
    """Run a shell script with args and return (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        script, *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.bind(script=script, stderr=stderr.decode()).warning("script_stderr")
    return proc.returncode, stdout.decode()


def setup_handlers(
    bot: Bot,
    classifier: LLMClassifier,
    vault_writer: VaultWriter,
    git_sync: GitSync,
    vault_structure: dict,
    allowed_user_ids: list[int],
    project_dir: str = ".",
    deduplicator: Deduplicator | None = None,
):
    """Configure the router with all dependencies."""
    router.message.filter(F.from_user.id.in_(set(allowed_user_ids)))

    # --- conflict resolution wiring ---

    async def handle_conflict(pending: PendingMerge):
        total = len(pending.blocks)
        for idx, block in enumerate(pending.blocks, 1):
            current_preview = block.current.strip()
            incoming_preview = block.incoming.strip()

            if len(current_preview) > _MAX_CONFLICT_PREVIEW:
                current_preview = current_preview[:_MAX_CONFLICT_PREVIEW] + "…"
            if len(incoming_preview) > _MAX_CONFLICT_PREVIEW:
                incoming_preview = incoming_preview[:_MAX_CONFLICT_PREVIEW] + "…"

            text = (
                f"⚠️ <b>Merge conflict</b> in "
                f"<code>{html_module.escape(block.file_path)}</code> ({idx}/{total})\n\n"
                f"<b>Current (yours):</b>\n"
                f"<pre>{html_module.escape(current_preview)}</pre>\n\n"
                f"<b>Incoming (from remote):</b>\n"
                f"<pre>{html_module.escape(incoming_preview)}</pre>"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Keep Current",
                        callback_data=f"mc:{block.id}:cur",
                    ),
                    InlineKeyboardButton(
                        text="⬇️ Accept Incoming",
                        callback_data=f"mc:{block.id}:inc",
                    ),
                ]]
            )
            for user_id in allowed_user_ids:
                await bot.send_message(
                    user_id,
                    text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )

    git_sync.set_conflict_handler(handle_conflict)

    # --- clarify answer handler (called from handle_text when session is active) ---

    async def _handle_clarification_answer(message: Message, session: dict):
        user_id = message.from_user.id
        task_path = session["task_path"]
        status_msg = await message.answer("Generating refined spec (Claude Opus)...")

        returncode, output = await _run_script(
            "./scripts/clarify-finalize.sh", task_path, message.text, cwd=project_dir
        )

        if returncode != 0:
            await status_msg.edit_text("Failed to generate spec. Check logs.")
            return

        git_sync.mark_dirty()
        del _clarify_sessions[user_id]

        summary = output.strip()[:600] or "Spec generated and task queued."
        await status_msg.edit_text(
            f"✅ <b>{html_module.escape(Path(task_path).stem)}</b> refined and queued!\n\n"
            f"{html_module.escape(summary)}\n\n"
            f"<i>The pipeline will pick it up automatically.</i>",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("mc:"))
    async def handle_conflict_resolution(callback: CallbackQuery):
        _, block_id, choice = callback.data.split(":")
        resolution = "current" if choice == "cur" else "incoming"

        resolved = await git_sync.resolve_conflict_block(block_id, resolution)
        if not resolved:
            await callback.answer("Already resolved or expired.", show_alert=True)
            return

        label = "✅ Kept current" if choice == "cur" else "⬇️ Accepted incoming"
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(label)

    # --- deduplicate command ---

    @router.message(Command("deduplicate"))
    async def handle_deduplicate(message: Message):
        if deduplicator is None:
            await message.reply("Deduplication is not configured.")
            return

        threshold: float | None = None
        args = message.text.split()[1:]
        if args:
            try:
                threshold = float(args[0])
                if not (0.0 < threshold <= 1.0):
                    raise ValueError
            except ValueError:
                await message.reply("Invalid threshold. Use a number between 0 and 1, e.g. /deduplicate 0.9")
                return

        threshold_info = f" (threshold: {threshold})" if threshold is not None else ""
        status_msg = await message.answer(f"Scanning vault for duplicates{threshold_info}...")

        last_edit = [0.0]

        async def on_progress(done: int, total: int):
            now = time.monotonic()
            if now - last_edit[0] < 3 and done < total:
                return
            last_edit[0] = now
            try:
                await status_msg.edit_text(f"Embedding notes... ({done}/{total})")
            except Exception:
                pass

        try:
            pairs = await deduplicator.scan(on_progress, threshold=threshold)
        except Exception:
            logger.exception("dedup_scan_failed")
            await status_msg.edit_text("Dedup scan failed. Check logs.")
            return

        if not pairs:
            await status_msg.edit_text("No duplicates found!")
            return

        session_id = uuid.uuid4().hex[:8]
        _dedup_sessions[session_id] = {
            "pairs": pairs,
            "index": 0,
            "deleted": 0,
            "created_at": time.monotonic(),
        }

        await status_msg.edit_text(f"Found {len(pairs)} potential duplicate(s).")
        await _send_dedup_pair(message, session_id, 0)

    @router.callback_query(F.data.startswith("dd:"))
    async def handle_dedup_action(callback: CallbackQuery):
        parts = callback.data.split(":")
        if len(parts) != 4:
            await callback.answer("Invalid action", show_alert=True)
            return

        _, session_id, idx_str, action = parts
        idx = int(idx_str)

        session = _dedup_sessions.get(session_id)
        if not session:
            await callback.answer("Session expired.", show_alert=True)
            return

        if time.monotonic() - session["created_at"] > _SESSION_TIMEOUT:
            del _dedup_sessions[session_id]
            await callback.answer("Session expired (30 min timeout).", show_alert=True)
            return

        pair: DuplicatePair = session["pairs"][idx]

        if action == "del_a":
            deduplicator.delete_note(pair.path_a)
            session["deleted"] += 1
            git_sync.mark_dirty()
            await callback.answer("Deleted first note.")
        elif action == "del_b":
            deduplicator.delete_note(pair.path_b)
            session["deleted"] += 1
            git_sync.mark_dirty()
            await callback.answer("Deleted second note.")
        elif action == "skip":
            await callback.answer("Skipped.")

        await callback.message.edit_reply_markup(reply_markup=None)

        next_idx = idx + 1
        if next_idx < len(session["pairs"]):
            session["index"] = next_idx
            await _send_dedup_pair(callback.message, session_id, next_idx)
        else:
            deleted = session["deleted"]
            del _dedup_sessions[session_id]
            await callback.message.answer(
                f"Done! Reviewed {len(session['pairs'])} pair(s), deleted {deleted} note(s)."
            )

    async def _send_dedup_pair(target: Message, session_id: str, idx: int):
        session = _dedup_sessions[session_id]
        pair: DuplicatePair = session["pairs"][idx]
        total = len(session["pairs"])

        rel_a = pair.path_a.replace(str(deduplicator.vault_path) + "/", "")
        rel_b = pair.path_b.replace(str(deduplicator.vault_path) + "/", "")

        preview_a = html_module.escape(pair.preview_a[:_MAX_DEDUP_PREVIEW])
        preview_b = html_module.escape(pair.preview_b[:_MAX_DEDUP_PREVIEW])

        text = (
            f"<b>Duplicate {idx + 1}/{total}</b> "
            f"({pair.similarity:.0%} similar)\n\n"
            f"<b>A:</b> <code>{html_module.escape(rel_a)}</code>\n"
            f"<pre>{preview_a}</pre>\n\n"
            f"<b>B:</b> <code>{html_module.escape(rel_b)}</code>\n"
            f"<pre>{preview_b}</pre>"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🗑 Delete A",
                    callback_data=f"dd:{session_id}:{idx}:del_a",
                ),
                InlineKeyboardButton(
                    text="🗑 Delete B",
                    callback_data=f"dd:{session_id}:{idx}:del_b",
                ),
                InlineKeyboardButton(
                    text="⏭ Skip",
                    callback_data=f"dd:{session_id}:{idx}:skip",
                ),
            ]]
        )

        await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    # --- inbox review command ---

    async def _start_review(target: Message):
        notes = vault_writer.list_inbox_notes()
        if not notes:
            await target.answer("Inbox is empty! Nothing to review.")
            return

        note = random.choice(notes)
        session_id = uuid.uuid4().hex[:8]
        folders = _get_all_folders(vault_structure)

        _inbox_sessions[session_id] = {
            "note_path": str(note),
            "folders": folders,
            "created_at": time.monotonic(),
        }
        await _send_review_note(target, session_id, note)

    async def _send_review_note(target: Message, session_id: str, note_path):
        note_path = Path(note_path)
        session = _inbox_sessions[session_id]
        folders = session["folders"]

        content = note_path.read_text(encoding="utf-8")
        preview = _parse_note_preview(content, _MAX_NOTE_PREVIEW)

        # Folder buttons, 2 per row
        folder_buttons = [
            InlineKeyboardButton(
                text=f"📂 {folder}",
                callback_data=f"rv:{session_id}:move:{i}",
            )
            for i, folder in enumerate(folders)
        ]
        rows = [folder_buttons[i:i + 2] for i in range(0, len(folder_buttons), 2)]
        rows.append([
            InlineKeyboardButton(text="✅ Keep", callback_data=f"rv:{session_id}:keep"),
            InlineKeyboardButton(text="⏭ Skip", callback_data=f"rv:{session_id}:skip"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"rv:{session_id}:delete"),
        ])

        remaining = len(vault_writer.list_inbox_notes())
        text = (
            f"<b>📥 Inbox</b> · <code>{html_module.escape(note_path.name)}</code>"
            f" ({remaining} left)\n\n"
            f"<pre>{html_module.escape(preview)}</pre>"
        )
        await target.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode=ParseMode.HTML,
        )

    @router.message(Command("review"))
    async def handle_review(message: Message):
        await _start_review(message)

    @router.callback_query(F.data == "rv_next")
    async def handle_review_next(callback: CallbackQuery):
        await callback.answer()
        await _start_review(callback.message)

    @router.callback_query(F.data.startswith("rv:"))
    async def handle_review_action(callback: CallbackQuery):
        parts = callback.data.split(":", 3)
        session_id = parts[1]
        action = parts[2]

        session = _inbox_sessions.get(session_id)
        if not session:
            await callback.answer("Session expired.", show_alert=True)
            return
        if time.monotonic() - session["created_at"] > _SESSION_TIMEOUT:
            del _inbox_sessions[session_id]
            await callback.answer("Session expired (30 min timeout).", show_alert=True)
            return

        note_path = Path(session["note_path"])

        if action == "move":
            folder_idx = int(parts[3])
            dest_folder = session["folders"][folder_idx]
            vault_writer.move_note(note_path, dest_folder)
            git_sync.mark_dirty()
            del _inbox_sessions[session_id]
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer(f"Moved to {dest_folder}!")
            remaining = len(vault_writer.list_inbox_notes())
            next_btn = [[InlineKeyboardButton(text="Next note ➡️", callback_data="rv_next")]]
            await callback.message.answer(
                f"✅ Moved to <code>{html_module.escape(dest_folder)}</code>."
                + (f" {remaining} note(s) left in inbox." if remaining else " Inbox is now empty!"),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=next_btn) if remaining else None,
            )

        elif action == "keep":
            del _inbox_sessions[session_id]
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Kept in inbox.")
            remaining = len(vault_writer.list_inbox_notes())
            if remaining > 1:
                next_btn = [[InlineKeyboardButton(text="Next note ➡️", callback_data="rv_next")]]
                await callback.message.answer(
                    f"{remaining} note(s) left in inbox.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=next_btn),
                )

        elif action == "skip":
            del _inbox_sessions[session_id]
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Skipped.")
            remaining = len(vault_writer.list_inbox_notes())
            if remaining > 0:
                next_btn = [[InlineKeyboardButton(text="Next note ➡️", callback_data="rv_next")]]
                await callback.message.answer(
                    f"{remaining} note(s) left in inbox.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=next_btn),
                )

        elif action == "delete":
            note_path.unlink(missing_ok=True)
            git_sync.mark_dirty()
            del _inbox_sessions[session_id]
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("Deleted.")
            remaining = len(vault_writer.list_inbox_notes())
            next_btn = [[InlineKeyboardButton(text="Next note ➡️", callback_data="rv_next")]]
            await callback.message.answer(
                "🗑 Note deleted."
                + (f" {remaining} note(s) left in inbox." if remaining else " Inbox is now empty!"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=next_btn) if remaining else None,
            )

    # --- clarify command ---

    @router.message(Command("clarify"))
    async def handle_clarify(message: Message):
        args = message.text.split()[1:]
        filename = args[0] if args else None

        task_path = _find_planning_task(vault_writer.repo_path, filename)
        if not task_path:
            if filename:
                await message.reply(
                    f"Task not found: <code>{html_module.escape(filename)}</code>",
                    parse_mode="HTML",
                )
            else:
                await message.reply("No planning tasks found in tg_sync_bot/")
            return

        status_msg = await message.answer(
            f"Analyzing <code>{html_module.escape(task_path.name)}</code> (Claude Opus)...",
            parse_mode="HTML",
        )

        returncode, output = await _run_script(
            "./scripts/clarify-questions.sh", str(task_path), cwd=project_dir
        )

        if returncode != 0 or not output.strip():
            await status_msg.edit_text("Failed to generate questions. Check logs.")
            return

        _clarify_sessions[message.from_user.id] = {
            "task_path": str(task_path),
            "created_at": time.monotonic(),
        }

        await status_msg.edit_text(
            f"📋 <b>{html_module.escape(task_path.stem)}</b>\n\n"
            f"{html_module.escape(output.strip())}\n\n"
            f"<i>Reply with your answers (numbered to match the questions above).</i>",
            parse_mode="HTML",
        )

    # --- push command ---

    @router.message(Command("push"))
    async def handle_push(message: Message):
        status_msg = await message.answer("Pushing to remote...")
        ok, result_msg = await git_sync.push_now()
        icon = "✅" if ok else "❌"
        await status_msg.edit_text(f"{icon} {result_msg}")

    # --- message handlers ---

    @router.message(F.photo)
    async def handle_photo(message: Message):
        # Download the largest photo
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        data = file_bytes.read()

        ext = file.file_path.rsplit(".", 1)[-1] if "." in file.file_path else "jpg"
        attachment_name = f"{photo.file_unique_id}.{ext}"
        vault_path = vault_writer.save_attachment(data, attachment_name)

        caption = message.caption or "Photo with no caption"
        text_for_llm = f"{caption}\n\n[Attached image: {vault_path}]"

        await _classify_and_save(
            message,
            text_for_llm,
            classifier,
            vault_writer,
            git_sync,
            vault_structure,
            extra_content=f"\n\n![[{vault_path}]]",
        )

    @router.message(F.document)
    async def handle_document(message: Message):
        doc = message.document
        file = await bot.get_file(doc.file_id)
        file_bytes = await bot.download_file(file.file_path)
        data = file_bytes.read()

        attachment_name = doc.file_name or f"{doc.file_unique_id}"
        vault_path = vault_writer.save_attachment(data, attachment_name)

        caption = message.caption or f"Document: {attachment_name}"
        text_for_llm = f"{caption}\n\n[Attached file: {vault_path}]"

        await _classify_and_save(
            message,
            text_for_llm,
            classifier,
            vault_writer,
            git_sync,
            vault_structure,
            extra_content=f"\n\n![[{vault_path}]]",
        )

    @router.message(F.voice)
    async def handle_voice(message: Message):
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        file_bytes = await bot.download_file(file.file_path)
        data = file_bytes.read()

        attachment_name = f"{voice.file_unique_id}.ogg"
        vault_path = vault_writer.save_attachment(data, attachment_name)

        text_for_llm = "Voice message (audio attached, no transcription yet)"

        await _classify_and_save(
            message,
            text_for_llm,
            classifier,
            vault_writer,
            git_sync,
            vault_structure,
            extra_content=f"\n\n![[{vault_path}]]",
            extra_tags=["voice"],
        )

    @router.message(F.text)
    async def handle_text(message: Message):
        # Check for active clarify session first
        user_id = message.from_user.id
        session = _clarify_sessions.get(user_id)
        if session:
            if time.monotonic() - session["created_at"] > _SESSION_TIMEOUT:
                del _clarify_sessions[user_id]
                await message.reply("Clarification session expired. Use /clarify to start again.")
                return
            await _handle_clarification_answer(message, session)
            return

        text = message.text

        # Handle forwarded messages
        if message.forward_origin:
            source = _extract_forward_source(message)
            text = f"[Forwarded from {source}]\n\n{text}"

        # Fetch content from URLs to improve LLM classification
        text_for_llm = await augment_text_with_urls(text)

        await _classify_and_save(
            message, text_for_llm, classifier, vault_writer, git_sync, vault_structure
        )

    return router


def _get_all_folders(vault_structure: dict, exclude: str = "inbox") -> list[str]:
    """Flatten all folder paths from vault_structure, excluding the given folder."""
    result = []

    def traverse(folders: list[dict]):
        for f in folders:
            if f["path"] != exclude:
                result.append(f["path"])
            if "children" in f:
                traverse(f["children"])

    traverse(vault_structure["folders"])
    return result


def _parse_note_preview(content: str, max_chars: int = 600) -> str:
    """Extract a readable preview from a note, skipping YAML frontmatter."""
    lines = content.splitlines()
    body_lines = []
    in_frontmatter = False

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
            continue
        body_lines.append(line)

    body = "\n".join(body_lines).strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "…"
    return body


def _extract_forward_source(message: Message) -> str:
    origin = message.forward_origin
    origin_type = origin.type

    if origin_type == "user":
        user = origin.sender_user
        name = user.full_name
        return f"@{user.username}" if user.username else name
    elif origin_type == "channel":
        chat = origin.chat
        return f"channel: {chat.title}"
    elif origin_type == "hidden_user":
        return f"{origin.sender_user_name}"
    else:
        return "unknown source"


def _find_planning_task(vault_path: Path, filename: str | None = None) -> Path | None:
    """Find a task to clarify. Filename takes priority; otherwise pick highest-priority planning task."""
    notes_dir = vault_path / _KANBAN_FOLDER
    if not notes_dir.exists():
        return None

    if filename:
        if not filename.endswith(".md"):
            filename += ".md"
        candidate = notes_dir / filename
        return candidate if candidate.exists() else None

    planning_tasks: list[tuple[Path, str]] = []
    for task_file in notes_dir.glob("*.md"):
        if task_file.name in _SKIP_FILES:
            continue
        content = task_file.read_text(encoding="utf-8")
        status = _extract_frontmatter_field(content, "status")
        if status != "planning":
            continue
        priority = _extract_frontmatter_field(content, "priority") or "medium"
        planning_tasks.append((task_file, priority))

    if not planning_tasks:
        return None

    planning_tasks.sort(key=lambda x: _PRIORITY_ORDER.get(x[1], 1))
    return planning_tasks[0][0]


def _extract_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a field value from YAML frontmatter."""
    for line in content.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return None


async def _classify_and_save(
    message: Message,
    text_for_llm: str,
    classifier: LLMClassifier,
    vault_writer: VaultWriter,
    git_sync: GitSync,
    vault_structure: dict,
    extra_content: str = "",
    extra_tags: list[str] | None = None,
):
    try:
        result = await classifier.classify(text_for_llm, vault_structure)
    except Exception:
        logger.exception("llm_classification_failed")
        await message.reply("Failed to classify the message. Saved to inbox.")
        result = {
            "folder": "inbox",
            "filename": "unclassified-note",
            "tags": ["quick_note"],
            "title": "Unclassified Note",
            "content": text_for_llm,
        }

    content = result["content"] + extra_content
    tags = result["tags"]
    if extra_tags:
        tags = list(set(tags + extra_tags))

    note_path = vault_writer.write_note(
        folder=result["folder"],
        filename=result["filename"],
        title=result["title"],
        content=content,
        tags=tags,
        status=result.get("status"),
        priority=result.get("priority"),
        clarification_needed=result.get("clarification_needed"),
    )

    logger.bind(
        folder=result["folder"],
        filename=note_path.name,
        tags=tags,
    ).info("message_classified")

    async def on_git_error(err: str):
        await message.answer(f"Note saved, but git sync failed: {err}")

    git_sync.mark_dirty(on_error=on_git_error)

    relative_path = note_path.name
    folder = result["folder"]
    tags_str = " ".join(f"#{t}" for t in tags)
    await message.reply(f"Saved to `{folder}/{relative_path}` with tags: {tags_str}")
