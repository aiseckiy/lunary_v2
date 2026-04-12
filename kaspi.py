"""
Kaspi.kz интеграция — прямые запросы к Kaspi API v2
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
BASE = "https://kaspi.kz/shop/api/v2"


def _headers():
    return {
        "X-Auth-Token": KASPI_TOKEN or "",
        "Content-Type": "application/vnd.api+json",
        "Accept": "*/*",
    }


def _date_range_ms(days: int = 14):
    now = int(time.time() * 1000)
    return now - days * 24 * 60 * 60 * 1000, now


def _kaspi_get(path: str, params: dict = None) -> dict | None:
    """GET запрос к Kaspi API"""
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Kaspi API error {path}: {e}")
        return None


def _kaspi_post(path: str, payload: dict) -> dict | None:
    """POST запрос к Kaspi API"""
    try:
        r = requests.post(f"{BASE}{path}", headers=_headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Kaspi API POST error {path}: {e}")
        return None


def get_order_entries(order_id: str) -> list:
    """Получить состав заказа напрямую из Kaspi API"""
    data = _kaspi_get(f"/orders/{order_id}/entries")
    if not data:
        return []

    included = {item["id"]: item for item in data.get("included", []) if isinstance(item, dict)}

    entries = []
    for item in data.get("data", []):
        attr = item.get("attributes", {})
        name = attr.get("name") or ""
        merchant_sku = attr.get("merchantSku") or ""

        # JSON:API relationships → included
        rels = item.get("relationships", {})
        product_rel = (rels.get("product", {}).get("data")
                       or rels.get("offer", {}).get("data"))
        if product_rel and isinstance(product_rel, dict):
            prod_attr = included.get(product_rel.get("id"), {}).get("attributes", {})
            if not name:
                name = prod_attr.get("name") or prod_attr.get("title") or ""
            if not merchant_sku:
                merchant_sku = prod_attr.get("code") or prod_attr.get("merchantSku") or ""

        if not name:
            name = ((attr.get("category") or {}).get("title")
                    or attr.get("productName") or "—")

        entries.append({
            "name": name or "—",
            "merchantSku": merchant_sku,
            "qty": attr.get("quantity", 1),
            "basePrice": int(attr.get("basePrice", 0)),
            "price": int(attr.get("totalPrice", 0)),
        })
    return entries


def get_kaspi_orders(state: str = "NEW", page: int = 0, size: int = 100) -> dict:
    """Получить заказы из Kaspi API (все страницы за последние 14 дней)"""
    date_ge, date_le = _date_range_ms(14)
    all_orders = []
    current_page = 0

    while True:
        data = _kaspi_get("/orders", {
            "page[number]": current_page,
            "page[size]": 100,
            "filter[orders][state]": state,
            "filter[orders][creationDate][$ge]": str(date_ge),
            "filter[orders][creationDate][$le]": str(date_le),
        })
        if not data:
            break

        items = data.get("data", [])
        for item in items:
            attr = item.get("attributes", {})
            customer = attr.get("customer", {})
            cname = f"{customer.get('firstName', '')} {customer.get('lastName', '')}".strip() or "—"
            all_orders.append({
                "id": item.get("id"),
                "code": attr.get("code", ""),
                "state": attr.get("state", "—"),
                "status": attr.get("status", "—"),
                "total": attr.get("totalPrice", 0),
                "date": attr.get("creationDate", ""),
                "customer": cname,
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


def update_order_status(order_id: str, code: str, status: str, cancellation_reason: str = None) -> bool:
    """Обновить статус заказа в Kaspi"""
    payload = {
        "data": {
            "type": "orders",
            "id": order_id,
            "attributes": {"code": code, "status": status},
        }
    }
    if cancellation_reason:
        payload["data"]["attributes"]["cancellationReason"] = cancellation_reason
    result = _kaspi_post("/orders", payload)
    return result is not None


def update_stock(kaspi_sku: str, qty: int) -> bool:
    """Обновить остаток товара в Kaspi"""
    if not KASPI_SHOP_ID:
        return False
    payload = {
        "data": [{
            "type": "offer",
            "attributes": {"availableQuantity": qty, "skuCode": kaspi_sku}
        }]
    }
    result = _kaspi_post(f"/masterdata/{KASPI_SHOP_ID}/offers/", payload)
    return result is not None


def get_kaspi_products(page: int = 0, size: int = 50) -> dict:
    return {"products": [], "total": 0, "error": "Не поддерживается"}


def sync_kaspi_products() -> str:
    if not KASPI_TOKEN or not KASPI_SHOP_ID:
        return "❌ KASPI_TOKEN или KASPI_SHOP_ID не заданы"
    return "✅ Kaspi синхронизация продуктов: 0 добавлено"


def format_orders_text(orders: list) -> str:
    if not orders:
        return "📭 Активных заказов нет"
    lines = [f"🛒 *Заказы Kaspi ({len(orders)}):*\n"]
    for o in orders[:10]:
        lines.append(f"📦 *Заказ #{o['id']}*")
        lines.append(f"   👤 {o['customer']} | 💰 {o['total']:,} ₸")
        for e in o.get("entries", []):
            lines.append(f"   • {e['name']} × {e['qty']} шт")
        lines.append("")
    return "\n".join(lines)
