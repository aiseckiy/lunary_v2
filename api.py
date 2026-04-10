from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os

from database import get_db, init_db
import crud
import kaspi as kaspi_module
from datetime import datetime




def _parse_order_date(date_str) -> datetime | None:
    """Парсит дату заказа из dd.mm.yyyy или Unix ms timestamp"""
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        if '.' in s:
            return datetime.strptime(s, "%d.%m.%Y")
        ts = int(float(s))
        if ts > 1_000_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts)
    except Exception:
        return None

import secrets
from fastapi import Request, Cookie
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Сессионный токен — генерируется при старте, хранится в памяти
_SESSION_TOKEN = secrets.token_urlsafe(32)

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
    _slowapi_ok = True
except ImportError:
    _slowapi_ok = False

app = FastAPI(title="Lunary OS", version="1.0")

if _slowapi_ok:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ─── Auth middleware ──────────────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        password = os.getenv("ADMIN_PASSWORD", "")
        if not password:
            return await call_next(request)  # Пароль не задан — открыто

        path = request.url.path
        # Публичные пути
        if path in ("/login", "/api/auth/login") or path.startswith("/static/"):
            return await call_next(request)

        session = request.cookies.get("lunary_session", "")
        if session != _SESSION_TOKEN:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            next_url = path
            return RedirectResponse(f"/login?next={next_url}", status_code=302)

        return await call_next(request)

app.add_middleware(AuthMiddleware)
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
    _start_kaspi_sync_loop()


def _start_kaspi_sync_loop():
    """Фоновая синхронизация заказов Kaspi каждые 5 минут"""
    import threading, time, json
    from database import KaspiOrder, SessionLocal as SL

    STATES = ["NEW", "PICKUP", "DELIVERY", "KASPI_DELIVERY", "ARCHIVE", "CANCELLED", "SIGN_REQUIRED"]

    def sync():
        while True:
            db = SL()
            try:
                all_orders = []
                for state in STATES:
                    result = kaspi_module.get_kaspi_orders(state=state, size=100)
                    if result.get("orders"):
                        all_orders.extend(result["orders"])

                added = 0
                new_orders = []
                for o in all_orders:
                    # Нормализуем дату из Unix ms → dd.mm.yyyy
                    raw_date = o.get("date", "")
                    try:
                        d = _parse_order_date(raw_date)
                        order_date = d.strftime("%d.%m.%Y") if d else str(raw_date)
                    except Exception:
                        order_date = str(raw_date)

                    existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == str(o["id"])).first()
                    if existing:
                        # Обновляем статус и основные поля
                        existing.state = o.get("state", existing.state)
                        existing.total = int(o.get("total", existing.total or 0))
                        existing.customer = o.get("customer", existing.customer)
                        existing.delivery_method = o.get("deliveryMode", existing.delivery_method)
                        existing.payment_method = o.get("paymentMode", existing.payment_method)
                        if o.get("deliveryAddress"):
                            addr = o["deliveryAddress"]
                            existing.address = addr.get("formattedAddress", existing.address) if isinstance(addr, dict) else str(addr)
                    else:
                        # Загружаем состав заказа
                        entries = kaspi_module.get_order_entries(str(o["id"]))
                        o["entries"] = entries
                        # Берём имя товара и SKU из первого entry
                        product_name, sku, quantity = None, None, None
                        if entries:
                            product_name = entries[0].get("name")
                            sku = entries[0].get("sku") or entries[0].get("merchantProduct", {}).get("code", "") if isinstance(entries[0], dict) else None
                            quantity = sum(e.get("qty", e.get("quantity", 1)) for e in entries if isinstance(e, dict))
                        addr_obj = o.get("deliveryAddress")
                        address = addr_obj.get("formattedAddress", "") if isinstance(addr_obj, dict) else str(addr_obj or "")
                        db.add(KaspiOrder(
                            order_id=str(o["id"]),
                            state=o.get("state", ""),
                            total=int(o.get("total", 0)),
                            customer=o.get("customer", ""),
                            entries=json.dumps(entries, ensure_ascii=False),
                            order_date=order_date,
                            product_name=product_name,
                            sku=sku,
                            quantity=quantity,
                            delivery_method=o.get("deliveryMode", ""),
                            payment_method=o.get("paymentMode", ""),
                            address=address,
                            source="kaspi_api",
                        ))
                        added += 1
                        new_orders.append(o)
                db.commit()

                notify_states = {"NEW", "PICKUP", "KASPI_DELIVERY", "DELIVERY"}
                for o in new_orders:
                    if o.get("state") in notify_states:
                        _send_tg_notification(_format_order_notification(o))

                if added:
                    print(f"✅ Kaspi sync: +{added} новых заказов")
            except Exception as e:
                print(f"⚠️ Kaspi sync error: {e}")
            finally:
                db.close()
            time.sleep(300)  # каждые 5 минут

    t = threading.Thread(target=sync, daemon=True)
    t.start()


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




# ─── Auth ────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

@app.get("/login", response_class=HTMLResponse)
def login_page():
    with open("static/login.html", encoding="utf-8") as f:
        return f.read()

@app.post("/api/auth/login")
def auth_login(data: LoginRequest):
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        # Пароль не задан — всегда успех
        resp = JSONResponse({"ok": True})
    elif data.password == password:
        resp = JSONResponse({"ok": True})
    else:
        raise HTTPException(status_code=401, detail="Неверный пароль")
    resp.set_cookie(
        "lunary_session", _SESSION_TOKEN,
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30  # 30 дней
    )
    return resp

@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("lunary_session")
    return resp


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
    from datetime import datetime
    STATE_LABELS = {
        "NEW": "🟡 Новый заказ",
        "KASPI_DELIVERY": "🚚 Kaspi Доставка",
        "DELIVERY": "🚛 Ваша доставка",
        "PICKUP": "🏪 Самовывоз",
        "COMPLETED": "✅ Выполнен",
        "CANCELLED": "❌ Отменён",
        "SIGN_REQUIRED": "✍️ Нужна подпись",
    }
    DELIVERY_LABELS = {
        "DELIVERY_LOCAL": "по городу",
        "DELIVERY_PICKUP": "самовывоз",
        "DELIVERY_REGIONAL_TODOOR": "Kaspi Доставка",
        "DELIVERY_REGIONAL_PICKUP": "до склада",
    }
    PAYMENT_LABELS = {
        "PAY_WITH_CREDIT": "Кредит",
        "PREPAID": "Безналичная",
    }

    state = o.get("state", "")
    label = STATE_LABELS.get(state, state)
    customer = o.get("customer") or "Покупатель"
    total = int(o.get("total", 0))
    entries = o.get("entries", [])
    code = o.get("code") or o.get("id", "")
    delivery_mode = DELIVERY_LABELS.get(o.get("deliveryMode", ""), o.get("deliveryMode", ""))
    payment = PAYMENT_LABELS.get(o.get("paymentMode", ""), "")
    address = o.get("deliveryAddress") or {}
    addr_str = address.get("formattedAddress", "")
    planned = o.get("plannedDeliveryDate")
    planned_str = ""
    if planned:
        try:
            planned_str = datetime.fromtimestamp(int(planned) / 1000).strftime("%d.%m.%Y")
        except Exception:
            pass

    phone_raw = o.get("phone", "").strip()
    # Приводим к формату 7XXXXXXXXXX для WhatsApp
    phone_wa = ""
    if phone_raw:
        digits = "".join(c for c in phone_raw if c.isdigit())
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        if len(digits) >= 10:
            phone_wa = digits if digits.startswith("7") else "7" + digits[-10:]

    lines = [
        f"<b>{label}</b>",
        f"🛒 Заказ <b>{code}</b>",
        f"👤 {customer}",
    ]
    if phone_wa:
        lines.append(f"📞 <a href='https://wa.me/{phone_wa}'>WhatsApp {phone_raw}</a>")
    if delivery_mode:
        lines.append(f"📦 Доставка: {delivery_mode}")
    if addr_str:
        lines.append(f"📍 {addr_str}")
    if planned_str:
        lines.append(f"📅 Планируемая дата: {planned_str}")
    if payment:
        lines.append(f"💳 Оплата: {payment}")
    lines.append("")

    if entries:
        for e in entries:
            qty = e.get('qty', 1)
            price = int(e.get('basePrice', e.get('price', 0)))
            name = e.get('name', '—')
            lines.append(f"  • {name} — {qty} шт × {price:,} ₸".replace(",", " "))
    else:
        lines.append("  (состав заказа загружается отдельно)")

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
def kaspi_orders_local(
    state: Optional[str] = None,
    date_from: Optional[str] = None,  # YYYY-MM-DD
    date_to: Optional[str] = None,    # YYYY-MM-DD
    limit: int = 2000,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    from database import KaspiOrder
    from sqlalchemy import func
    import json

    # Маппинг русских статусов (XML) → внутренние
    STATE_MAP = {"Выдан": "ARCHIVE", "Отменен": "CANCELLED", "Возврат": "RETURN"}

    q = db.query(KaspiOrder)
    if state:
        if state in STATE_MAP.values():
            russian = [k for k, v in STATE_MAP.items() if v == state]
            q = q.filter(KaspiOrder.state.in_(russian + [state]))
        else:
            q = q.filter(KaspiOrder.state == state)

    # Fetch all matching orders (date filter happens in Python due to mixed date formats)
    all_orders = q.order_by(KaspiOrder.id.desc()).all()

    # Date filtering in Python
    df_dt = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if date_to else None
    if df_dt or dt_dt:
        filtered = []
        for o in all_orders:
            d = _parse_order_date(o.order_date)
            if d is None:
                continue
            if df_dt and d < df_dt:
                continue
            if dt_dt and d > dt_dt:
                continue
            filtered.append(o)
        all_orders = filtered

    total_count = len(all_orders)
    orders = all_orders[offset: offset + limit]

    # Счётчики по вкладкам
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
        synced = None
        if o.created_at:
            try:
                from datetime import timezone, timedelta
                synced = o.created_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5))).strftime("%d.%m %H:%M")
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


@app.get("/api/kaspi/orders/export")
def kaspi_orders_export(state: Optional[str] = None, db: Session = Depends(get_db)):
    """Экспорт заказов в CSV"""
    from database import KaspiOrder
    from fastapi.responses import StreamingResponse
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


def _filter_orders_by_date(rows, date_from: str | None, date_to: str | None):
    """Фильтр строк KaspiOrder по дате (Python-side, т.к. смешанные форматы)"""
    df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if date_to else None
    if not df and not dt:
        return rows
    result = []
    for r in rows:
        d = _parse_order_date(getattr(r, "order_date", None))
        if d is None:
            continue
        if df and d < df:
            continue
        if dt and d > dt:
            continue
        result.append(r)
    return result


@app.get("/api/analytics/overview")
def analytics_overview(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Общая статистика по заказам"""
    from database import KaspiOrder
    from sqlalchemy import func

    all_rows = db.query(KaspiOrder).all()
    rows = _filter_orders_by_date(all_rows, date_from, date_to)

    total_orders = len(rows)
    total_revenue = sum(r.total or 0 for r in rows)
    avg_order = int(total_revenue / total_orders) if total_orders else 0

    COMPLETED_STATES = {"Выдан", "ARCHIVE"}
    CANCELLED_STATES = {"Отменен", "CANCELLED"}
    completed = sum(1 for r in rows if r.state in COMPLETED_STATES)
    cancelled = sum(1 for r in rows if r.state in CANCELLED_STATES)

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order": avg_order,
        "completed": completed,
        "cancelled": cancelled,
        "conversion_rate": round(completed / total_orders * 100, 1) if total_orders else 0,
    }


@app.get("/api/analytics/abc")
def analytics_abc(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """ABC-анализ товаров по выручке"""
    from database import KaspiOrder
    from collections import defaultdict

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.product_name.isnot(None)).all()
    rows = _filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    if not rows:
        return {"products": [], "total_revenue": 0}

    agg: dict = defaultdict(lambda: {"sku": "", "category": "", "revenue": 0, "qty": 0, "orders": 0})
    for r in rows:
        k = r.product_name
        agg[k]["sku"] = r.sku or ""
        agg[k]["category"] = r.category or ""
        agg[k]["revenue"] += r.total or 0
        agg[k]["qty"] += r.quantity or 1
        agg[k]["orders"] += 1

    items = sorted(
        [{"name": k, **v} for k, v in agg.items()],
        key=lambda x: x["revenue"], reverse=True
    )

    total_rev = sum(i["revenue"] for i in items)
    cumulative = 0
    for item in items:
        cumulative += item["revenue"]
        pct = cumulative / total_rev * 100
        item["abc"] = "A" if pct <= 80 else ("B" if pct <= 95 else "C")
        item["revenue_pct"] = round(item["revenue"] / total_rev * 100, 1)

    return {"products": items, "total_revenue": total_rev}


@app.get("/api/analytics/revenue")
def analytics_revenue(
    period: str = "month",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Выручка по периодам (month / week / day)"""
    from database import KaspiOrder
    from collections import defaultdict

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.order_date.isnot(None)).all()
    rows = _filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    by_period: dict = defaultdict(lambda: {"revenue": 0, "orders": 0})
    for r in rows:
        d = _parse_order_date(r.order_date)
        if not d:
            continue
        if period == "day":
            key = d.strftime("%Y-%m-%d")
        elif period == "week":
            key = d.strftime("%Y-W%W")
        else:
            key = d.strftime("%Y-%m")
        by_period[key]["revenue"] += r.total or 0
        by_period[key]["orders"] += 1

    points = sorted([
        {"period": k, "revenue": v["revenue"], "orders": v["orders"]}
        for k, v in by_period.items()
    ], key=lambda x: x["period"])

    return {"points": points, "period": period}


@app.get("/api/analytics/forecast")
def analytics_forecast(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Прогноз спроса по топ-20 товарам (линейная регрессия по неделям)"""
    import numpy as np
    from database import KaspiOrder
    from collections import defaultdict

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.product_name.isnot(None)).all()
    rows = _filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    # Сгруппировать по товару → по неделе
    by_product: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = _parse_order_date(r.order_date)
        if not d:
            continue
        week = d.strftime("%Y-W%W")
        by_product[r.product_name][week] += r.quantity or 1

    # Топ-20 по суммарному количеству
    top_names = sorted(by_product.keys(), key=lambda n: sum(by_product[n].values()), reverse=True)[:20]

    results = []
    for name in top_names:
        week_data = by_product[name]
        weeks = sorted(week_data.keys())
        if len(weeks) < 3:
            continue
        y = np.array([week_data[w] for w in weeks], dtype=float)
        x = np.arange(len(y))
        # Линейная регрессия
        coeffs = np.polyfit(x, y, 1)
        trend = float(coeffs[0])  # положительный = рост
        # Прогноз на след. 4 недели
        next_x = len(y) + np.arange(4)
        forecast = [max(0, round(float(np.polyval(coeffs, xi)))) for xi in next_x]
        # Скользящее среднее за 4 недели
        ma4 = float(np.mean(y[-4:])) if len(y) >= 4 else float(np.mean(y))
        results.append({
            "name": name,
            "weeks": weeks[-8:],
            "history": [int(week_data[w]) for w in weeks[-8:]],
            "forecast_4w": forecast,
            "trend": round(trend, 2),
            "trend_dir": "up" if trend > 0.1 else ("down" if trend < -0.1 else "flat"),
            "ma4": round(ma4, 1),
        })

    results.sort(key=lambda x: sum(x["history"]), reverse=True)
    return {"products": results}




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


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page():
    with open("static/analytics.html") as f:
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
