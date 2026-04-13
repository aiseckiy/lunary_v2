"""
Запуск системы: python run.py
"""
import threading
import signal
import sys
import logging
import uvicorn
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from api import app
from bot import run_bot
from database import init_db
from seed import seed


def run_api():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", loop="none")


if __name__ == "__main__":
    print("🚀 Lunary OS запускается...\n")

    init_db()
    seed()

    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    print("✅ API запущен на порту 8000")

    print("🤖 Запуск Telegram бота (ожидание 8 сек для rolling deploy)...")
    import time; time.sleep(8)
    run_bot()
