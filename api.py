from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os

from database import get_db, init_db
import crud
import kaspi as kaspi_module

app = FastAPI(title="Lunary OS", version="1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Pydantic схемы ──────────────────────────────────────────
class ProductCreate(BaseModel):
    name: str
    sku: str
    barcode: Optional[str] = None
    category: str = "Общее"
    unit: str = "шт"
    min_stock: int = 5
    brand: Optional[str] = None
    price: Optional[int] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    min_stock: Optional[int] = None
    brand: Optional[str] = None
    price: Optional[int] = None


class MovementCreate(BaseModel):
    product_id: int
    quantity: int
    type: str  # income, sale, writeoff, return
    source: str = "manual"
    note: Optional[str] = None


class StockAdjust(BaseModel):
    quantity: int
    type: str
    source: str = "manual"
    note: Optional[str] = None


class AISuggestRequest(BaseModel):
    name: str
    barcode: Optional[str] = None


# ─── Startup ─────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    _auto_import_if_empty()


def _auto_import_if_empty():
    """Если база пустая — автоматически импортируем товары из export_products.json"""
    import json, os
    from database import Product as _P, SessionLocal
    db = SessionLocal()
    try:
        count = db.query(_P).count()
        if count > 0:
            return
        path = os.path.join(os.path.dirname(__file__), 'export_products.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        added = 0
        for p in data:
            sku = p['sku'].upper()
            if db.query(_P).filter(_P.sku == sku).first():
                continue
            new_p = crud.create_product(
                name=p['name'], sku=sku, db=db,
                barcode=p.get('barcode'), category=p.get('category', 'Общее'),
                unit=p.get('unit', 'шт'), min_stock=p.get('min_stock', 5),
                brand=p.get('brand'), price=p.get('price')
            )
            if p.get('stock', 0) > 0:
                crud.set_initial_stock(new_p.id, p['stock'], db)
            added += 1
        print(f"✅ Автоимпорт: {added} товаров загружено из export_products.json")
    except Exception as e:
        print(f"⚠️ Автоимпорт ошибка: {e}")
    finally:
        db.close()


# ─── Товары ──────────────────────────────────────────────────
@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    stocks = crud.get_all_stocks(db)
    return [
        {
            "id": s["product"].id,
            "name": s["product"].name,
            "sku": s["product"].sku,
            "barcode": s["product"].barcode,
            "category": s["product"].category,
            "unit": s["product"].unit,
            "min_stock": s["product"].min_stock,
            "stock": s["stock"],
            "low": s["stock"] <= s["product"].min_stock,
            "brand": s["product"].brand or "",
            "price": s["product"].price
        }
        for s in stocks
    ]


@app.post("/api/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    from database import Product as _P
    sku = data.sku.upper()
    if db.query(_P).filter(_P.sku == sku).first():
        raise HTTPException(status_code=409, detail="Артикул уже занят")
    p = crud.create_product(
        name=data.name, sku=sku, db=db,
        barcode=data.barcode, category=data.category,
        unit=data.unit, min_stock=data.min_stock, brand=data.brand, price=data.price
    )
    return {"id": p.id, "name": p.name, "sku": p.sku}


@app.get("/api/products/search")
def search_products(q: str, db: Session = Depends(get_db)):
    products = crud.find_product(q, db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "barcode": p.barcode,
            "stock": crud.get_stock(p.id, db),
            "unit": p.unit
        }
        for p in products
    ]


@app.get("/api/products/barcode/{barcode}")
def get_by_barcode(barcode: str, db: Session = Depends(get_db)):
    p = crud.get_product_by_barcode(barcode, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return {
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "barcode": p.barcode,
        "stock": crud.get_stock(p.id, db),
        "unit": p.unit,
        "min_stock": p.min_stock
    }


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    db.query(_M).filter(_M.product_id == product_id).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.put("/api/products/{product_id}")
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if "sku" in updates:
        updates["sku"] = updates["sku"].upper()
    p = crud.update_product(product_id, db, **updates)
    return {"id": p.id, "name": p.name, "sku": p.sku}


@app.get("/api/products/{product_id}/stock")
def get_stock(product_id: int, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    stock = crud.get_stock(product_id, db)
    return {"product_id": product_id, "name": p.name, "stock": stock, "unit": p.unit}


@app.post("/api/products/{product_id}/movement")
def add_movement(product_id: int, data: StockAdjust, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")

    if data.type not in ("income", "sale", "writeoff", "return", "adjustment"):
        raise HTTPException(status_code=400, detail="Неверный тип движения")

    m = crud.add_movement(product_id, data.quantity, data.type, db, data.source, data.note)
    new_stock = crud.get_stock(product_id, db)
    return {
        "movement_id": m.id,
        "product": p.name,
        "type": data.type,
        "quantity": data.quantity,
        "new_stock": new_stock
    }


@app.get("/api/products/{product_id}/history")
def get_history(product_id: int, db: Session = Depends(get_db)):
    movements = crud.get_movements(product_id, db)
    p = crud.get_product_by_id(product_id, db)
    type_labels = {
        "income": "📦 Приход",
        "sale": "🛒 Продажа",
        "writeoff": "🗑 Списание",
        "return": "↩️ Возврат",
        "adjustment": "✏️ Корректировка"
    }
    return [
        {
            "id": m.id,
            "type": m.type,
            "type_label": type_labels.get(m.type, m.type),
            "quantity": m.quantity,
            "source": m.source,
            "note": m.note,
            "date": m.created_at.strftime("%d.%m.%Y %H:%M")
        }
        for m in movements
    ]


# ─── Журнал всех операций ────────────────────────────────────
@app.get("/api/history")
def get_all_history(
    limit: int = 100,
    offset: int = 0,
    type: Optional[str] = None,
    source: Optional[str] = None,
    product_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    from database import Movement, Product
    from sqlalchemy import desc

    q = db.query(Movement, Product).join(Product, Movement.product_id == Product.id)
    if type:
        q = q.filter(Movement.type == type)
    if source:
        q = q.filter(Movement.source == source)
    if product_id:
        q = q.filter(Movement.product_id == product_id)

    total = q.count()
    rows = q.order_by(desc(Movement.created_at)).offset(offset).limit(limit).all()

    type_labels = {"income": "Приход", "sale": "Продажа", "writeoff": "Списание",
                   "return": "Возврат", "adjustment": "Корректировка"}
    return {
        "total": total,
        "items": [
            {
                "id": m.id,
                "product_id": p.id,
                "product_name": p.name,
                "product_sku": p.sku,
                "type": m.type,
                "type_label": type_labels.get(m.type, m.type),
                "quantity": m.quantity,
                "source": m.source or "manual",
                "note": m.note or "",
                "date": m.created_at.strftime("%d.%m.%Y"),
                "time": m.created_at.strftime("%H:%M"),
                "datetime_iso": m.created_at.isoformat(),
            }
            for m, p in rows
        ]
    }


@app.delete("/api/history/{movement_id}")
def delete_movement(movement_id: int, db: Session = Depends(get_db)):
    from database import Movement
    m = db.query(Movement).filter(Movement.id == movement_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ─── Алерты ──────────────────────────────────────────────────
@app.get("/api/alerts/low-stock")
def low_stock(db: Session = Depends(get_db)):
    items = crud.get_low_stock_products(db)
    return [
        {"id": p.id, "name": p.name, "stock": stock, "min_stock": p.min_stock}
        for p, stock in items
    ]


# ─── Дашборд ─────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("static/index.html") as f:
        return f.read()


@app.get("/scanner", response_class=HTMLResponse)
def scanner():
    with open("static/scanner.html") as f:
        return f.read()


@app.get("/history", response_class=HTMLResponse)
def history_page():
    with open("static/history.html") as f:
        return f.read()


# ─── AI автозаполнение ───────────────────────────────────────
@app.post("/api/ai/suggest")
def ai_suggest(data: AISuggestRequest):
    """AI подсказывает категорию, бренд, единицу и артикул по названию товара"""
    import os, json
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI не настроен")
    client = OpenAI(api_key=api_key)

    prompt = (
        f"Товар: «{data.name}»\n"
        "Это товар из строительной химии / инструментов / крепежа.\n"
        "Определи и верни JSON с полями:\n"
        "- category: одно из [Герметики, Пены монтажные, Дюбели и крепёж, Инструменты, Химия, Лента и скотч, Клей, Краски, Другое]\n"
        "- brand: бренд если есть в названии, иначе ''\n"
        "- unit: одно из [шт, кг, л, м, уп, рул]\n"
        "- sku_hint: короткий артикул латиницей (макс 15 символов), например TYT_SIL_280\n"
        "Верни только JSON без пояснений."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=200
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "category": result.get("category", "Другое"),
            "brand": result.get("brand", ""),
            "unit": result.get("unit", "шт"),
            "sku_hint": result.get("sku_hint", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Kaspi заказы ────────────────────────────────────────────
@app.get("/api/kaspi/orders")
def get_kaspi_orders_endpoint(state: str = "ACCEPTED", page: int = 0, size: int = 20):
    """Получить заказы из Kaspi API"""
    token = os.getenv("KASPI_TOKEN")
    shop_id = os.getenv("KASPI_SHOP_ID")
    if not token or not shop_id:
        return {"orders": [], "total": 0, "error": "KASPI_TOKEN и KASPI_SHOP_ID не заданы в настройках"}
    result = kaspi_module.get_kaspi_orders(state=state, page=page, size=size)
    return result


@app.get("/api/kaspi/states")
def get_kaspi_states():
    """Возможные статусы заказов Kaspi"""
    return ["ACCEPTED", "COMPLETED", "CANCELLED", "KASPI_DELIVERY", "PICKUP"]


@app.get("/api/kaspi/test")
def kaspi_test():
    """Тест прямого запроса к Kaspi API с Railway"""
    import requests as req
    token = os.getenv("KASPI_TOKEN")
    headers = {"X-Auth-Token": token, "Content-Type": "application/vnd.api+json", "Accept": "*/*"}
    params = {"page[number]": 0, "page[size]": 5, "filter[orders][state]": "NEW"}
    try:
        r = req.get("https://kaspi.kz/shop/api/v2/orders", headers=headers, params=params, timeout=20, verify=False)
        return {"status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        return {"error": str(e)}


class KaspiOrdersPayload(BaseModel):
    orders: list


def _send_tg_notification(text: str):
    """Отправить сообщение в Telegram через Bot API напрямую (без polling)."""
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = os.getenv("ADMIN_CHAT_ID")
    if not bot_token or not chat_id:
        return
    try:
        import urllib.request
        import urllib.parse
        params = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage?{params}"
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        print(f"⚠️ TG уведомление ошибка: {e}")


def _format_order_notification(o: dict) -> str:
    STATE_LABELS = {
        "ACCEPTED": "🟡 Новый заказ",
        "KASPI_DELIVERY": "🚚 Доставка Kaspi",
        "PICKUP": "🏪 Самовывоз",
        "COMPLETED": "✅ Выполнен",
        "CANCELLED": "❌ Отменён",
    }
    state = o.get("state", "")
    label = STATE_LABELS.get(state, state)
    customer = o.get("customer") or "Покупатель"
    total = int(o.get("total", 0))
    entries = o.get("entries", [])

    lines = [
        f"<b>{label}</b>",
        f"🛒 Заказ #{o.get('id')}",
        f"👤 {customer}",
        "",
    ]
    for e in entries:
        lines.append(f"  • {e.get('name', '—')} — {e.get('qty', 1)} шт × {int(e.get('price', 0) / max(e.get('qty', 1), 1)):,} ₸".replace(",", " "))
    lines.append("")
    lines.append(f"<b>Итого: {total:,} ₸</b>".replace(",", " "))
    return "\n".join(lines)


@app.post("/api/kaspi/orders/sync")
def kaspi_orders_sync(payload: KaspiOrdersPayload, db: Session = Depends(get_db)):
    """Принимает заказы от локального sync скрипта и сохраняет в БД"""
    orders = payload.orders
    from database import KaspiOrder
    import json
    added = 0
    updated = 0
    new_orders = []
    for o in orders:
        existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == str(o["id"])).first()
        if existing:
            existing.state = o.get("state", existing.state)
            updated += 1
        else:
            db.add(KaspiOrder(
                order_id=str(o["id"]),
                state=o.get("state", ""),
                total=int(o.get("total", 0)),
                customer=o.get("customer", ""),
                entries=json.dumps(o.get("entries", []), ensure_ascii=False),
                order_date=str(o.get("date", ""))
            ))
            added += 1
            new_orders.append(o)
    db.commit()

    # Уведомления о новых заказах (только ACCEPTED/PICKUP/KASPI_DELIVERY)
    notify_states = {"ACCEPTED", "PICKUP", "KASPI_DELIVERY"}
    for o in new_orders:
        if o.get("state") in notify_states:
            _send_tg_notification(_format_order_notification(o))

    return {"added": added, "updated": updated}


@app.get("/api/kaspi/orders/local")
def kaspi_orders_local(state: Optional[str] = None, db: Session = Depends(get_db)):
    """Возвращает заказы из локальной БД (сохранённые sync скриптом)"""
    from database import KaspiOrder
    import json
    q = db.query(KaspiOrder)
    if state:
        q = q.filter(KaspiOrder.state == state)
    orders = q.order_by(KaspiOrder.id.desc()).limit(200).all()
    return {
        "orders": [
            {
                "id": o.order_id,
                "state": o.state,
                "total": o.total,
                "customer": o.customer,
                "entries": json.loads(o.entries or "[]"),
                "date": o.order_date,
                "synced_at": o.created_at.strftime("%d.%m %H:%M")
            }
            for o in orders
        ],
        "total": len(orders),
        "error": None
    }


@app.get("/api/kaspi/debug")
def kaspi_debug():
    """Отладка Kaspi API — показывает сырой ответ"""
    import requests, os
    token = os.getenv("KASPI_TOKEN", "")
    shop_id = os.getenv("KASPI_SHOP_ID", "")
    if not token or not shop_id:
        return {"error": "токены не заданы", "token_set": bool(token), "shop_id_set": bool(shop_id)}
    url = f"https://kaspi.kz/shop/api/v2/orders/merchant/{shop_id}/"
    try:
        r = requests.get(url, headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                        params={"page[number]": 0, "page[size]": 5, "filter[orders][state]": "ACCEPTED"},
                        timeout=15, verify=False)
        return {"status_code": r.status_code, "url": url, "shop_id": shop_id, "response": r.text[:500]}
    except Exception as e:
        return {"error": str(e), "url": url}


@app.post("/api/kaspi/sync-products")
def sync_kaspi_products_endpoint():
    """Синхронизировать товары из Kaspi в склад"""
    token = os.getenv("KASPI_TOKEN")
    shop_id = os.getenv("KASPI_SHOP_ID")
    if not token or not shop_id:
        raise HTTPException(status_code=400, detail="KASPI_TOKEN и KASPI_SHOP_ID не заданы")
    result = kaspi_module.sync_kaspi_products()
    return {"message": result}


@app.get("/kaspi", response_class=HTMLResponse)
def kaspi_page():
    with open("static/kaspi.html") as f:
        return f.read()


@app.post("/api/import-products")
def import_products(db: Session = Depends(get_db)):
    """Одноразовый импорт товаров из export_products.json"""
    import json, os
    path = os.path.join(os.path.dirname(__file__), 'export_products.json')
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="export_products.json не найден")
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    from database import Product as _P
    added = skipped = 0
    for p in data:
        sku = p['sku'].upper()
        if db.query(_P).filter(_P.sku == sku).first():
            skipped += 1
            continue
        new_p = crud.create_product(
            name=p['name'], sku=sku, db=db,
            barcode=p.get('barcode'), category=p.get('category', 'Общее'),
            unit=p.get('unit', 'шт'), min_stock=p.get('min_stock', 5),
            brand=p.get('brand')
        )
        if p.get('stock', 0) > 0:
            crud.set_initial_stock(new_p.id, p['stock'], db)
        added += 1
    return {"added": added, "skipped": skipped}
