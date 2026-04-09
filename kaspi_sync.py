"""
Kaspi Sync — запускается локально на Маке (в Казахстане)
Каждые 5 минут тянет заказы из Kaspi и отправляет на Railway.

Запуск: python3 kaspi_sync.py
"""
import os
import time
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

KASPI_TOKEN = os.getenv("KASPI_TOKEN")
KASPI_SHOP_ID = os.getenv("KASPI_SHOP_ID")
RAILWAY_URL = os.getenv("RAILWAY_URL", "https://lunary.up.railway.app")
SYNC_INTERVAL = 300  # 5 минут

STATES = ["ACCEPTED", "KASPI_DELIVERY", "PICKUP", "COMPLETED", "CANCELLED"]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def fetch_kaspi_orders(state: str) -> list:
    url = f"https://kaspi.kz/shop/api/v2/orders/merchant/{KASPI_SHOP_ID}/"
    headers = {"X-Auth-Token": KASPI_TOKEN, "Content-Type": "application/json"}
    params = {"page[number]": 0, "page[size]": 100, "filter[orders][state]": state}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30, verify=False)
        r.raise_for_status()
        data = r.json()
        orders = []
        for item in data.get("data", []):
            attr = item.get("attributes", {})
            entries = []
            for e in attr.get("entries", []):
                entries.append({
                    "name": e.get("name", "—"),
                    "sku": e.get("merchantSku", "—"),
                    "qty": e.get("quantity", 0),
                    "price": e.get("basePrice", 0),
                })
            orders.append({
                "id": item.get("id"),
                "state": attr.get("state", state),
                "total": attr.get("totalPrice", 0),
                "customer": attr.get("customer", {}).get("name", "—"),
                "entries": entries,
                "date": str(attr.get("creationDate", "")),
            })
        return orders
    except requests.exceptions.SSLError:
        # Kaspi иногда даёт SSL ошибки — пробуем без верификации
        return []
    except Exception as e:
        log(f"⚠️ Ошибка Kaspi ({state}): {e}")
        return []

def send_to_railway(orders: list) -> bool:
    if not orders:
        return True
    try:
        r = requests.post(
            f"{RAILWAY_URL}/api/kaspi/orders/sync",
            json=orders,
            timeout=30
        )
        r.raise_for_status()
        result = r.json()
        log(f"✅ Railway: добавлено {result['added']}, обновлено {result['updated']}")
        return True
    except Exception as e:
        log(f"⚠️ Ошибка Railway: {e}")
        return False

def sync():
    log("🔄 Синхронизация с Kaspi...")
    all_orders = []
    for state in STATES:
        orders = fetch_kaspi_orders(state)
        if orders:
            log(f"   {state}: {len(orders)} заказов")
        all_orders.extend(orders)

    if all_orders:
        send_to_railway(all_orders)
        log(f"✅ Всего синхронизировано: {len(all_orders)} заказов")
    else:
        log("📭 Заказов не найдено или ошибка Kaspi API")

def main():
    if not KASPI_TOKEN or not KASPI_SHOP_ID:
        print("❌ Заполни KASPI_TOKEN и KASPI_SHOP_ID в файле .env")
        return

    log(f"🚀 Kaspi Sync запущен. Интервал: {SYNC_INTERVAL // 60} мин")
    log(f"   Shop ID: {KASPI_SHOP_ID}")
    log(f"   Railway: {RAILWAY_URL}")
    log("")

    # Сразу синхронизируем при запуске
    sync()

    # Потом каждые 5 минут
    while True:
        time.sleep(SYNC_INTERVAL)
        sync()

if __name__ == "__main__":
    # Отключаем предупреждения SSL
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
