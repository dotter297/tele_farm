from aiogram import Bot, Dispatcher
import asyncio
from aiogram.fsm.storage.memory import MemoryStorage
from bot.handlers import router
from bot.session_manager import router as session_router
from bot.logger import logger
from bot.admin_panel import router as admin_router
import os
from bot.proxy_manager import router as proxy_router
from bot.join import router as join_router`
from bot.admin_panel import router as admin_router
from bot.statistic import router as statistic_router



TOKEN = os.getenv("BOT_TOKEN")  # ✅ Бот получает токен из .env

async def main():
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN":
        raise ValueError("❌ Ошибка: Укажите корректный токен бота в .env файле!")

    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(proxy_router)
    dp.include_router(router)
    dp.include_router(session_router)
    dp.include_router(join_router)
    dp.include_router(statistic_router)
    logger.info("🤖 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
