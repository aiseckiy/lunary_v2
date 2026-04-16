"""Kaspi: read-only endpoints + XML feed/export + CSV export + HTML pages.

Оставлено в api.py (не здесь):
- /api/kaspi/orders/sync (сложная логика списания остатков + TG уведомления)
- /api/kaspi/import-history* (global state + background thread)
- /api/kaspi/import-xml-products, /api/kaspi/import-archive (XML импорты)
"""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from helpers import filter_orders_by_date, get_integration
import crud
import kaspi as kaspi_module

router = APIRouter(tags=["kaspi"])


@router.get("/api/kaspi/orders")
def get_kaspi_orders_endpoint(state: str = "ACCEPTED", page: int = 0, size: int = 20):
    """Получить заказы из Kaspi API"""
    token = get_integration("kaspi_api_key", "KASPI_TOKEN")
    shop_id = get_integration("kaspi_shop_id", "KASPI_SHOP_ID")
    if not token or not shop_id:
        return {"orders": [], "total": 0, "error": "KASPI_TOKEN и KASPI_SHOP_ID не заданы в настройках"}
    return kaspi_module.get_kaspi_orders(state=state, page=page, size=size)


@router.get("/api/kaspi/states")
def get_kaspi_states():
    """Возможные статусы заказов Kaspi"""
    return ["ACCEPTED", "COMPLETED", "CANCELLED", "KASPI_DELIVERY", "PICKUP"]


@router.get("/api/kaspi/orders/local")
def kaspi_orders_local(
    state: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 2000,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    from database import KaspiOrder, Product

    STATE_MAP = {"Выдан": "ARCHIVE", "Отменен": "CANCELLED", "Возврат": "RETURN"}

    q = db.query(KaspiOrder)
    if state:
        if state in STATE_MAP.values():
            russian = [k for k, v in STATE_MAP.items() if v == state]
            q = q.filter(KaspiOrder.state.in_(russian + [state]))
        else:
            q = q.filter(KaspiOrder.state == state)

    all_orders = q.order_by(KaspiOrder.id.desc()).all()

    if date_from or date_to:
        all_orders = filter_orders_by_date(all_orders, date_from, date_to)

    total_count = len(all_orders)
    orders = all_orders[offset: offset + limit]

    raw_counts = db.query(KaspiOrder.state, func.count(KaspiOrder.id)).group_by(KaspiOrder.state).all()
    state_counts: dict = {}
    for s, c in raw_counts:
        key = STATE_MAP.get(s, s)
        state_counts[key] = state_counts.get(key, 0) + c

    def fmt(o):
        normalized = STATE_MAP.get(o.state, o.state)
        if o.entries and o.entries != "[]":
            entries = json.loads(o.entries)
        elif o.product_name:
            entries = [{"name": o.product_name, "sku": o.sku or "", "qty": o.quantity or 1, "basePrice": o.total}]
        else:
            entries = []
        for entry in entries:
            if entry.get("product_id"):
                continue
            mssku = entry.get("merchantSku", "")
            name = entry.get("name", "")
            product = None
            if mssku:
                product = db.query(Product).filter(
                    (Product.kaspi_sku == mssku) |
                    Product.kaspi_sku.like(f"{mssku}_%")
                ).first()
            if not product and name:
                product = db.query(Product).filter(Product.name.ilike(f"%{name[:30]}%")).first()
            if product:
                entry["product_id"] = product.id
        synced = None
        sync_ts = o.last_synced_at or o.created_at
        if sync_ts:
            try:
                from datetime import timezone, timedelta
                synced = sync_ts.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5))).strftime("%d.%m %H:%M")
            except Exception:
                pass
        return {
            "id": o.order_id,
            "state": normalized,
            "total": o.total or 0,
            "customer": o.customer or "",
            "entries": entries,
            "date": o.order_date or "",
            "synced_at": synced,
            "source": o.source or "kaspi_api",
            "delivery_method": o.delivery_method or "",
            "address": o.address or "",
            "payment_method": o.payment_method or "",
            "cancel_reason": o.cancel_reason or "",
        }

    return {
        "orders": [fmt(o) for o in orders],
        "total": total_count,
        "state_counts": state_counts,
        "error": None,
    }


@router.get("/api/kaspi/orders/{order_id}/entries")
def get_kaspi_order_entries(order_id: str, db: Session = Depends(get_db)):
    """Получить и сохранить состав конкретного заказа"""
    from database import KaspiOrder
    order = db.query(KaspiOrder).filter(KaspiOrder.order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404)
    if order.entries and order.entries not in ("[]", ""):
        return {"entries": json.loads(order.entries)}
    print(f"[entries] загружаем состав для order_id={order_id}", flush=True)
    try:
        entries = kaspi_module.get_order_entries(order_id)
        print(f"[entries] получено {len(entries) if entries else 0} позиций для {order_id}", flush=True)
    except Exception as e:
        print(f"[entries] ОШИБКА для {order_id}: {e}", flush=True)
        entries = []
    if entries:
        order.entries = json.dumps(entries, ensure_ascii=False)
        if not order.product_name:
            order.product_name = entries[0].get("name")
            order.sku = entries[0].get("merchantSku", "")
            order.quantity = sum(e.get("qty", 1) for e in entries if isinstance(e, dict))
        db.commit()
    return {"entries": entries}


@router.get("/api/kaspi/export-preview")
def kaspi_export_preview(db: Session = Depends(get_db)):
    """Предпросмотр данных перед экспортом Kaspi XML"""
    from database import SiteSetting, Product
    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    store_id = settings.get("kaspi_store_id", "30409502_PP1")

    products = db.query(Product).filter(Product.kaspi_sku.isnot(None)).all()
    rows = []
    for p in products:
        skus = [s.strip() for s in p.kaspi_sku.split(",") if s.strip()]
        stock = max(crud.get_stock(p.id, db), 0)
        multi_sku = len(skus) > 1
        for sku in skus:
            problems = []
            if not p.price:
                problems.append("нет цены")
            if not p.brand:
                problems.append("нет бренда")
            if not p.name:
                problems.append("нет названия")
            rows.append({
                "id": p.id,
                "sku": sku,
                "name": p.name or "",
                "brand": p.brand or "",
                "stock": stock,
                "price": p.price,
                "available": stock > 0,
                "multi_sku": multi_sku,
                "store_id": store_id,
                "problems": problems,
            })
    return {"rows": rows}


@router.get("/admin/export-preview")
def export_preview_page():
    return FileResponse("static/export_preview.html")


def _build_kaspi_xml(db) -> str:
    """Строит Kaspi Shopping XML из БД."""
    from datetime import datetime, timezone, timedelta
    import xml.etree.ElementTree as ET
    from database import SiteSetting, Product

    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    merchant_id = settings.get("kaspi_merchant_id", "30409502")
    store_id    = settings.get("kaspi_store_id",    "30409502_PP1")
    city_id     = settings.get("kaspi_city_id",     "750000000")

    tz_kz = timezone(timedelta(hours=5))
    now_str = datetime.now(tz_kz).strftime("%Y-%m-%d %H:%M")

    root = ET.Element("kaspi_catalog", attrib={"xmlns": "kaspiShopping", "date": now_str})
    ET.SubElement(root, "company").text = merchant_id
    ET.SubElement(root, "merchantid").text = merchant_id
    offers_el = ET.SubElement(root, "offers")

    products = db.query(Product).filter(Product.kaspi_sku.isnot(None)).all()
    for p in products:
        skus = [s.strip() for s in p.kaspi_sku.split(",") if s.strip()]
        stock = max(crud.get_stock(p.id, db), 0)
        for sku in skus:
            offer = ET.SubElement(offers_el, "offer", sku=sku)
            ET.SubElement(offer, "model").text = p.name or ""
            ET.SubElement(offer, "brand").text = p.brand or ""
            avails = ET.SubElement(offer, "availabilities")
            ET.SubElement(avails, "availability",
                          available="yes" if stock > 0 else "no",
                          storeId=store_id,
                          preOrder="0",
                          stockCount=str(float(stock)))
            if p.price:
                cityprices = ET.SubElement(offer, "cityprices")
                ET.SubElement(cityprices, "cityprice", cityId=city_id).text = str(p.price)

    ET.indent(root, space="    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


@router.get("/api/kaspi/feed.xml", include_in_schema=False)
def kaspi_feed_public(token: str = "", db: Session = Depends(get_db)):
    """Публичный Kaspi Price Feed — защищён токеном."""
    from database import SiteSetting
    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    feed_token = settings.get("kaspi_feed_token", "")
    if feed_token and token != feed_token:
        raise HTTPException(status_code=403, detail="Invalid token")

    xml_str = _build_kaspi_xml(db)
    return Response(content=xml_str, media_type="application/xml",
                    headers={"Cache-Control": "no-cache"})


@router.get("/api/kaspi/export-xml")
def kaspi_export_xml(db: Session = Depends(get_db)):
    """Скачать Kaspi XML для ручной загрузки в Merchant Portal."""
    xml_str = _build_kaspi_xml(db)
    return Response(content=xml_str, media_type="application/xml",
                    headers={"Content-Disposition": "attachment; filename=ACTIVE.xml"})


@router.get("/api/kaspi/orders/export")
def kaspi_orders_export(state: Optional[str] = None, db: Session = Depends(get_db)):
    """Экспорт заказов в CSV"""
    from database import KaspiOrder
    import csv, io

    STATE_MAP = {"Выдан": "ARCHIVE", "Отменен": "CANCELLED", "Возврат": "RETURN"}
    q = db.query(KaspiOrder)
    if state:
        if state in STATE_MAP.values():
            russian = [k for k, v in STATE_MAP.items() if v == state]
            q = q.filter(KaspiOrder.state.in_(russian + [state]))
        else:
            q = q.filter(KaspiOrder.state == state)

    orders = q.order_by(KaspiOrder.id.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Номер заказа", "Дата", "Статус", "Товар", "Артикул", "Кол-во", "Сумма ₸",
                     "Категория", "Способ доставки", "Адрес", "Способ оплаты", "Причина отмены",
                     "Стоимость доставки (продавец)", "Компенсация доставки", "Источник"])
    for o in orders:
        writer.writerow([
            o.order_id, o.order_date, STATE_MAP.get(o.state, o.state),
            o.product_name or "", o.sku or "", o.quantity or "",
            o.total or 0, o.category or "",
            o.delivery_method or "", o.address or "", o.payment_method or "",
            o.cancel_reason or "", o.delivery_cost_seller or 0,
            o.delivery_compensation or 0, o.source or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kaspi_orders.csv"}
    )


@router.post("/api/kaspi/sync-products")
def sync_kaspi_products_endpoint():
    """Синхронизировать товары из Kaspi в склад"""
    token = get_integration("kaspi_api_key", "KASPI_TOKEN")
    shop_id = get_integration("kaspi_shop_id", "KASPI_SHOP_ID")
    if not token or not shop_id:
        raise HTTPException(status_code=400, detail="KASPI_TOKEN и KASPI_SHOP_ID не заданы")
    result = kaspi_module.sync_kaspi_products()
    return {"message": result}


@router.get("/admin/kaspi", response_class=HTMLResponse)
def kaspi_page():
    with open("static/kaspi.html") as f:
        return f.read()


@router.get("/kaspi", response_class=HTMLResponse)
def kaspi_redirect():
    return RedirectResponse("/admin/kaspi", status_code=301)
