import logging

from aiogram import Bot, Router, F
from aiogram.types import Message

from bot.llm import LLMClassifier
from bot.vault import VaultWriter
from bot.git_sync import GitSync

logger = logging.getLogger(__name__)

router = Router()


def setup_handlers(
    bot: Bot,
    classifier: LLMClassifier,
    vault_writer: VaultWriter,
    git_sync: GitSync,
    vault_structure: dict,
    allowed_user_ids: list[int],
):
    """Configure the router with all dependencies."""
    router.message.filter(F.from_user.id.in_(set(allowed_user_ids)))

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
