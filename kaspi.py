"""
Kaspi.kz интеграция через Supabase Edge Function (прокси)
"""
import os
import logging
import requests
import time
from database import SessionLocal
import crud

logger = logging.getLogger(__name__)

KASPI_TOKEN = os.getenv("KASPI_TOKEN")
KASPI_SHOP_ID = os.getenv("KASPI_SHOP_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://cmbdlnpvsbxplispwlvr.supabase.co")
PROXY_URL = f"{SUPABASE_URL}/functions/v1/kaspi-proxy"


def _proxy(action: str, params: dict = None) -> dict:
    """Запрос к Kaspi через Supabase Edge Function"""
    try:
        r = requests.post(PROXY_URL, json={"action": action, "params": params or {}}, timeout=30)
        data = r.json()
        if data.get("success"):
            return data.get("data")
        logger.error(f"Kaspi proxy error: {data}")
        return None
    except Exception as e:
        logger.error(f"Kaspi proxy exception: {e}")
        return None


def _date_range_ms(days: int = 14):
    """Возвращает диапазон дат в миллисекундах (последние N дней)"""
    now = int(time.time() * 1000)
    start = now - days * 24 * 60 * 60 * 1000
    return start, now


def get_order_entries(order_id: str) -> list:
    """Получить состав заказа"""
    data = _proxy("get_order_entries", {"orderId": order_id})
    if not data:
        return []
    entries = []
    for item in data.get("data", []):
        attr = item.get("attributes", {})
        entries.append({
            "name": attr.get("name") or attr.get("category", {}).get("title", "—"),
            "merchantSku": attr.get("merchantSku", ""),
            "qty": attr.get("quantity", 1),
            "basePrice": int(attr.get("basePrice", 0)),
            "price": int(attr.get("totalPrice", 0)),
        })
    return entries


# ── Товары ────────────────────────────────────────────────────

def get_kaspi_products(page: int = 0, size: int = 50) -> dict:
    """Получить список товаров из Kaspi"""
    return {"products": [], "total": 0, "error": "Не поддерживается"}


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

def get_kaspi_orders(state: str = "NEW", page: int = 0, size: int = 100) -> dict:
    """Получить все заказы из Kaspi через Supabase прокси (все страницы)"""
    date_ge, date_le = _date_range_ms(14)
    all_orders = []
    current_page = 0

    while True:
        data = _proxy("get_orders", {
            "state": state,
            "page": current_page,
            "size": 100,
            "creationDateGe": str(date_ge),
            "creationDateLe": str(date_le),
        })
        if not data:
            break

        items = data.get("data", [])
        for item in items:
            attr = item.get("attributes", {})
            customer = attr.get("customer", {})
            name = f"{customer.get('firstName', '')} {customer.get('lastName', '')}".strip() or "—"
            all_orders.append({
                "id": item.get("id"),
                "code": attr.get("code", ""),
                "state": attr.get("state", "—"),
                "status": attr.get("status", "—"),
                "total": attr.get("totalPrice", 0),
                "date": attr.get("creationDate", ""),
                "customer": name,
                "phone": customer.get("cellPhone", ""),
                "deliveryMode": attr.get("deliveryMode", ""),
                "paymentMode": attr.get("paymentMode", ""),
                "deliveryAddress": attr.get("deliveryAddress"),
                "plannedDeliveryDate": attr.get("plannedDeliveryDate"),
                "entries": [],
            })

        total_pages = data.get("meta", {}).get("pageCount", 1)
        if current_page + 1 >= total_pages or len(items) < 100:
            break
        current_page += 1

    return {"orders": all_orders, "total": len(all_orders), "error": None}


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
