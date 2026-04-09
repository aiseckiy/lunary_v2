"""
Kaspi Sync — запускается локально на Маке
Тянет заказы через GraphQL (как браузер) и отправляет на Railway.

Запуск: python3 kaspi_sync.py

Как получить куки:
1. Открой kaspi.kz/mc в браузере
2. DevTools → Network → любой graphql запрос → Headers → cookie
3. Скопируй значения mc-session и mc-sid в .env
"""
import os
import time
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

KASPI_SESSION = os.getenv("KASPI_SESSION")   # mc-session кука
KASPI_SID     = os.getenv("KASPI_SID")       # mc-sid кука
KASPI_SHOP_ID = os.getenv("KASPI_SHOP_ID", "30409502")
RAILWAY_URL   = os.getenv("RAILWAY_URL", "https://lunary.up.railway.app")
SYNC_INTERVAL = 300  # 5 минут

GRAPHQL_URL = "https://mc.shop.kaspi.kz/mc/facade/graphql"

QUERY = """
query getOrders($merchantUid: String!, $input: MerchantOrderInput!, $page: Int!, $size: Int) {
  merchant(id: $merchantUid) {
    orders {
      orders(input: $input, page: $page, size: $size) {
        total
        orders {
          code
          customer { firstName lastName }
          totalPrice
          creationTime
          status
          entries {
            product { name code }
            merchantProduct { code name barcode }
            quantity
            totalPrice
          }
        }
      }
    }
  }
}
"""

PRESETS = [
    ("NEW",        "ACCEPTED"),
    ("PICKUP",     "PICKUP"),
    ("DELIVERY",   "KASPI_DELIVERY"),
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_headers():
    cookie = f"mc-session={KASPI_SESSION}; mc-sid={KASPI_SID}"
    return {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://kaspi.kz",
        "Referer": "https://kaspi.kz/",
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "x-auth-version": "3",
    }

def fetch_orders(preset: str, state: str) -> list:
    payload = {
        "operationName": "getOrders",
        "query": QUERY,
        "variables": {
            "merchantUid": KASPI_SHOP_ID,
            "page": 0,
            "size": 100,
            "input": {"presetFilter": preset, "orderCode": "", "cityId": ""},
        }
    }
    try:
        r = requests.post(
            f"{GRAPHQL_URL}?opName=getOrders",
            headers=get_headers(),
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        data = r.json()

        if "errors" in data:
            log(f"⚠️ GraphQL ошибка ({preset}): {data['errors'][0].get('message', '')}")
            return []

        raw_orders = (
            data.get("data", {})
                .get("merchant", {})
                .get("orders", {})
                .get("orders", {})
                .get("orders", [])
        )

        orders = []
        for o in raw_orders:
            entries = []
            for e in o.get("entries", []):
                mp = e.get("merchantProduct") or e.get("product") or {}
                entries.append({
                    "name": mp.get("name", "—"),
                    "sku":  mp.get("code", "—"),
                    "qty":  e.get("quantity", 0),
                    "price": e.get("totalPrice", 0),
                })
            customer = o.get("customer") or {}
            orders.append({
                "id":       o.get("code"),
                "state":    state,
                "total":    o.get("totalPrice", 0),
                "customer": f"{customer.get('firstName','')} {customer.get('lastName','')}".strip(),
                "entries":  entries,
                "date":     str(o.get("creationTime", "")),
            })
        return orders

    except Exception as e:
        log(f"⚠️ Ошибка ({preset}): {e}")
        return []

def send_to_railway(orders: list):
    if not orders:
        return
    try:
        r = requests.post(
            f"{RAILWAY_URL}/api/kaspi/orders/sync",
            json={"orders": orders},
            timeout=30
        )
        result = r.json()
        log(f"✅ Railway: добавлено {result['added']}, обновлено {result['updated']}")
    except Exception as e:
        log(f"⚠️ Ошибка Railway: {e}")

def sync():
    log("🔄 Синхронизация с Kaspi...")
    all_orders = []
    for preset, state in PRESETS:
        orders = fetch_orders(preset, state)
        if orders:
            log(f"   {preset}: {len(orders)} заказов")
        all_orders.extend(orders)

    if all_orders:
        send_to_railway(all_orders)
        log(f"✅ Итого: {len(all_orders)} заказов")
    else:
        log("📭 Заказов не найдено")

def main():
    if not KASPI_SESSION or not KASPI_SID:
        print("❌ Заполни KASPI_SESSION и KASPI_SID в .env файле")
        print()
        print("Как получить:")
        print("1. Открой https://kaspi.kz/mc в браузере")
        print("2. DevTools (Cmd+Option+I) → Network → graphql запрос → Headers")
        print("3. Найди строку 'cookie:' и скопируй значения mc-session и mc-sid")
        return

    log(f"🚀 Kaspi Sync запущен. Интервал: {SYNC_INTERVAL // 60} мин")
    log(f"   Shop ID: {KASPI_SHOP_ID}")
    log(f"   Railway: {RAILWAY_URL}")
    print()

    sync()
    while True:
        time.sleep(SYNC_INTERVAL)
        sync()

if __name__ == "__main__":
    main()
