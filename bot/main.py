import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiohttp.web import AppRunner, TCPSite
from dotenv import load_dotenv
from loguru import logger

from openai import AsyncOpenAI

from bot.api import create_api_app
from bot.config import load_settings, load_vault_structure
from bot.dedup import Deduplicator
from bot.llm import LLMClassifier
from bot.project_sync import ProjectSync
from bot.vault import VaultWriter
from bot.git_sync import GitSync
from bot.handlers import setup_handlers
from bot.logging_config import setup_logging


async def main():
    load_dotenv()

    github_token = os.environ.get("GITHUB_TOKEN", "")
    setup_logging(secrets=[github_token] if github_token else None)

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
        project_dir=settings.get("pipeline", {}).get("project_dir", "."),
        deduplicator=deduplicator,
    )
    dp.include_router(router)

    # Start git background tasks
    sync_task = asyncio.create_task(git_sync.sync_loop())
    pull_task = asyncio.create_task(git_sync.pull_loop())

    # Start HTTP API server
    api_cfg = settings.get("api", {})
    api_secret = api_cfg.get("secret", "")
    api_port = api_cfg.get("port", 8443)

    project_sync = ProjectSync(
        vault_path=settings["vault"]["repo_path"],
        board_path=api_cfg.get("board_path", "tg_sync_bot/board.md"),
        projects_dir=api_cfg.get("projects_dir", "projects"),
    )

    api_app = create_api_app(
        git_sync=git_sync,
        project_sync=project_sync,
        api_secret=api_secret,
        bot=bot,
        notify_chat_id=api_cfg.get("notify_chat_id"),
    )
    runner = AppRunner(api_app)
    await runner.setup()
    site = TCPSite(runner, "0.0.0.0", api_port)
    await site.start()
    logger.info("API server started on port {}", api_port)

    await bot.set_my_commands([
        BotCommand(command="clarify", description="Clarify a planning task (optional: filename)"),
        BotCommand(command="deduplicate", description="Scan vault for duplicates (optional: threshold, e.g. 0.9)"),
        BotCommand(command="review", description="Review and file notes from your inbox"),
        BotCommand(command="push", description="Manually trigger git push"),
        BotCommand(command="query", description="Ask a question across your vault notes"),
        BotCommand(command="index", description="Regenerate the vault index.md catalog"),
    ])

    logger.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        sync_task.cancel()
        pull_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
