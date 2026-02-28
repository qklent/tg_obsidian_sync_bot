import html as html_module
import logging
import time
import uuid

from aiogram import Bot, Router, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.dedup import Deduplicator, DuplicatePair
from bot.llm import LLMClassifier
from bot.vault import VaultWriter
from bot.git_sync import GitSync, PendingMerge

logger = logging.getLogger(__name__)

router = Router()

_MAX_CONFLICT_PREVIEW = 500  # chars shown per side before truncating
_MAX_DEDUP_PREVIEW = 300
_SESSION_TIMEOUT = 1800  # 30 minutes

# Active dedup sessions: {session_id: DedupSession}
_dedup_sessions: dict[str, dict] = {}


def setup_handlers(
    bot: Bot,
    classifier: LLMClassifier,
    vault_writer: VaultWriter,
    git_sync: GitSync,
    vault_structure: dict,
    allowed_user_ids: list[int],
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
            logger.exception("Dedup scan failed")
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
        text = message.text

        # Handle forwarded messages
        if message.forward_origin:
            source = _extract_forward_source(message)
            text = f"[Forwarded from {source}]\n\n{text}"

        await _classify_and_save(
            message, text, classifier, vault_writer, git_sync, vault_structure
        )

    return router


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
        logger.exception("LLM classification failed")
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
    )

    async def on_git_error(err: str):
        await message.answer(f"Note saved, but git sync failed: {err}")

    git_sync.mark_dirty(on_error=on_git_error)

    relative_path = note_path.name
    folder = result["folder"]
    tags_str = " ".join(f"#{t}" for t in tags)
    await message.reply(f"Saved to `{folder}/{relative_path}` with tags: {tags_str}")
