import asyncio
import logging

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from openai import AsyncOpenAI

from bot.config import load_settings, load_vault_structure
from bot.dedup import Deduplicator
from bot.llm import LLMClassifier
from bot.vault import VaultWriter
from bot.git_sync import GitSync
from bot.handlers import setup_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    load_dotenv()

    settings = load_settings()
    vault_structure = load_vault_structure()

    bot = Bot(token=settings["telegram"]["bot_token"])
    dp = Dispatcher()

    classifier = LLMClassifier(
        api_key=settings["openrouter"]["api_key"],
        model=settings["openrouter"]["model"],
    )

    vault_writer = VaultWriter(
        repo_path=settings["vault"]["repo_path"],
        attachments_dir=settings["vault"]["attachments_dir"],
    )

    git_sync = GitSync(
        repo_path=settings["vault"]["repo_path"],
        debounce_seconds=settings["git"]["commit_debounce_seconds"],
        pull_interval_seconds=settings["git"]["pull_interval_seconds"],
    )

    dedup_cfg = settings.get("dedup", {})
    deduplicator = Deduplicator(
        vault_path=settings["vault"]["repo_path"],
        openai_client=AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings["openrouter"]["api_key"],
        ),
        model=dedup_cfg.get("embedding_model", "openai/text-embedding-3-small"),
        cache_path=dedup_cfg.get("cache_path", ".tg_sync_cache/embeddings.json"),
        similarity_threshold=dedup_cfg.get("similarity_threshold", 0.90),
    )

    router = setup_handlers(
        bot=bot,
        classifier=classifier,
        vault_writer=vault_writer,
        git_sync=git_sync,
        vault_structure=vault_structure,
        allowed_user_ids=settings["telegram"]["allowed_user_ids"],
        deduplicator=deduplicator,
    )
    dp.include_router(router)

    # Start git background tasks
    sync_task = asyncio.create_task(git_sync.sync_loop())
    pull_task = asyncio.create_task(git_sync.pull_loop())

    logger.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        sync_task.cancel()
        pull_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
