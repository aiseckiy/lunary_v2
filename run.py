"""
Запуск системы: python run.py
"""
import threading
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from api import app
from bot import run_bot
from database import init_db
from seed import seed


def run_api():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    print("🚀 Lunary OS запускается...\n")

    # Инициализация БД и загрузка начальных данных
    init_db()
    seed()

    # Запуск API в отдельном потоке
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    print("✅ API запущен на http://localhost:8000")
    print("✅ Дашборд: http://localhost:8000")
    print("✅ Сканер: http://localhost:8000/scanner\n")

    # Запуск бота в основном потоке
    print("🤖 Запуск Telegram бота...")
    run_bot()
