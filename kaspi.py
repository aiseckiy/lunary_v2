"""
Kaspi.kz интеграция — только чтение (просмотр товаров и заказов)
"""
import os
import logging
import requests
from database import SessionLocal
import crud

logger = logging.getLogger(__name__)

KASPI_TOKEN = os.getenv("KASPI_TOKEN")
KASPI_SHOP_ID = os.getenv("KASPI_SHOP_ID")
BASE_URL = "https://kaspi.kz/shop/api/v2"


def _headers():
    return {
        "X-Auth-Token": KASPI_TOKEN,
        "Content-Type": "application/json"
    }


def _get(path: str, params: dict = None):
    """GET-запрос к Kaspi API"""
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=30, verify=False)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        logger.error(f"Kaspi API timeout: {url}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"Kaspi API error {e.response.status_code}: {url}")
        return None
    except Exception as e:
        logger.error(f"Kaspi API exception: {e}")
        return None


# ── Товары ────────────────────────────────────────────────────

def get_kaspi_products(page: int = 0, size: int = 50) -> dict:
    """Получить список товаров из Kaspi"""
    data = _get(f"/masterdata/{KASPI_SHOP_ID}/skus", {
        "page[number]": page,
        "page[size]": size
    })
    if not data:
        return {"products": [], "total": 0, "error": "Нет ответа от Kaspi API"}

    products = []
    for item in data.get("data", []):
        attr = item.get("attributes", {})
        products.append({
            "kaspi_id": item.get("id"),
            "name": attr.get("name", "—"),
            "sku": attr.get("code", "—"),
            "barcode": attr.get("ean", None),
            "category": "Kaspi",
        })

    total = data.get("meta", {}).get("total", len(products))
    return {"products": products, "total": total, "error": None}


def sync_kaspi_products() -> str:
    """Синхронизировать товары из Kaspi в локальную БД (только новые)"""
    if not KASPI_TOKEN or not KASPI_SHOP_ID:
        return "❌ KASPI_TOKEN или KASPI_SHOP_ID не заданы"

    db = SessionLocal()
    added = 0
    skipped = 0
    page = 0

    try:
        while True:
            result = get_kaspi_products(page=page, size=50)
            if result["error"]:
                return f"❌ Ошибка Kaspi API: {result['error']}"

            products = result["products"]
            if not products:
                break

            for p in products:
                # Проверяем по штрихкоду или артикулу
                existing = None
                if p["barcode"]:
                    existing = crud.get_product_by_barcode(p["barcode"], db)
                if not existing:
                    found = crud.find_product(p["sku"], db)
                    existing = found[0] if found else None

                if existing:
                    skipped += 1
                    continue

                # Добавляем новый товар с категорией "Kaspi"
                crud.create_product(
                    name=p["name"],
                    sku=p["sku"],
                    db=db,
                    barcode=p["barcode"],
                    category="Kaspi",
                    unit="шт",
                    min_stock=5
                )
                added += 1

            if len(products) < 50:
                break
            page += 1

        return f"✅ Kaspi синхронизация: добавлено {added}, уже были {skipped}"
    finally:
        db.close()


# ── Заказы ────────────────────────────────────────────────────

def get_kaspi_orders(state: str = "ACCEPTED", page: int = 0, size: int = 20) -> dict:
    """Получить заказы из Kaspi"""
    data = _get(f"/orders/merchant/{KASPI_SHOP_ID}/", {
        "page[number]": page,
        "page[size]": size,
        "filter[orders][state]": state
    })
    if not data:
        return {"orders": [], "total": 0, "error": "Нет ответа от Kaspi API"}

    orders = []
    for item in data.get("data", []):
        attr = item.get("attributes", {})
        entries = []
        for entry in attr.get("entries", []):
            entries.append({
                "name": entry.get("name", "—"),
                "sku": entry.get("merchantSku", "—"),
                "qty": entry.get("quantity", 0),
                "price": entry.get("basePrice", 0),
            })
        orders.append({
            "id": item.get("id"),
            "state": attr.get("state", "—"),
            "total": attr.get("totalPrice", 0),
            "date": attr.get("creationDate", "—"),
            "customer": attr.get("customer", {}).get("name", "—"),
            "entries": entries,
        })

    total = data.get("meta", {}).get("total", len(orders))
    return {"orders": orders, "total": total, "error": None}


def format_orders_text(orders: list) -> str:
    """Форматирует заказы для Telegram"""
    if not orders:
        return "📭 Активных заказов нет"

    lines = [f"🛒 *Заказы Kaspi ({len(orders)}):*\n"]
    for o in orders[:10]:
        lines.append(f"📦 *Заказ #{o['id']}*")
        lines.append(f"   👤 {o['customer']} | 💰 {o['total']:,} ₸")
        for e in o["entries"]:
            lines.append(f"   • {e['name']} × {e['qty']} шт")
        lines.append("")

    return "\n".join(lines)
