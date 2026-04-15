"""Shop orders: публичное создание + my-orders + admin управление."""
import json
import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from helpers import get_user_from_session, is_admin, get_integration

router = APIRouter(tags=["shop_orders"])


class ShopOrderCreate(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None
    comment: Optional[str] = None
    items: list  # [{product_id, qty}]


def _notify_new_shop_order(order, items):
    """Уведомление в Telegram о новом заказе из магазина."""
    import requests as req_lib
    bot_token = get_integration("tg_bot_token", "TELEGRAM_BOT_TOKEN")
    chat_id = get_integration("tg_chat_id", "TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    lines = "\n".join([f"• {i['name']} × {i['qty']} = {i['price']*i['qty']:,} ₸" for i in items])
    text = (
        f"🛒 *Новый заказ #{order.id}*\n\n"
        f"👤 {order.name}\n"
        f"📞 {order.phone}\n"
        f"📍 {order.address or '—'}\n"
        f"💬 {order.comment or '—'}\n\n"
        f"{lines}\n\n"
        f"💰 *Итого: {order.total:,} ₸*"
    )

    def _send():
        try:
            req_lib.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=3
            )
        except Exception as e:
            print(f"⚠️ TG shop order уведомление ошибка: {e}")

    threading.Thread(target=_send, daemon=True).start()


@router.post("/api/shop/orders")
def create_shop_order(data: ShopOrderCreate, request: Request, db: Session = Depends(get_db)):
    from database import ShopOrder, Product as _P

    if not data.items:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    order_items = []
    total = 0
    for item in data.items:
        pid = item.get("product_id")
        qty = int(item.get("qty", 1))
        p = db.query(_P).filter(_P.id == pid).first()
        if not p:
            continue
        price = p.price or 0
        order_items.append({"product_id": p.id, "name": p.name, "qty": qty, "price": price, "sku": p.kaspi_sku or ""})
        total += price * qty

    if not order_items:
        raise HTTPException(status_code=400, detail="Товары не найдены")

    user = get_user_from_session(request)
    user_id = user.get("id") if user else None

    order = ShopOrder(
        user_id=user_id,
        name=data.name,
        phone=data.phone,
        address=data.address,
        comment=data.comment,
        items=json.dumps(order_items, ensure_ascii=False),
        total=total,
        status="new"
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    try:
        _notify_new_shop_order(order, order_items)
    except Exception:
        pass

    return {"ok": True, "order_id": order.id, "total": total}


@router.get("/api/shop/my-orders")
def my_orders(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_session(request)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Не авторизован")
    from database import ShopOrder
    orders = db.query(ShopOrder).filter(ShopOrder.user_id == user["id"]).order_by(ShopOrder.created_at.desc()).all()
    return [
        {"id": o.id, "status": o.status, "total": o.total,
         "items": o.items, "created_at": str(o.created_at)}
        for o in orders
    ]


@router.get("/api/admin/shop-orders/new-count")
def shop_orders_new_count(db: Session = Depends(get_db)):
    from database import ShopOrder
    count = db.query(ShopOrder).filter(ShopOrder.status == "new").count()
    return {"count": count}


@router.get("/api/admin/shop-orders")
def list_shop_orders(request: Request, status: Optional[str] = None, db: Session = Depends(get_db)):
    from database import ShopOrder
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    q = db.query(ShopOrder).order_by(ShopOrder.created_at.desc())
    if status:
        q = q.filter(ShopOrder.status == status)
    orders = q.limit(200).all()
    return [{
        "id": o.id, "name": o.name, "phone": o.phone,
        "address": o.address, "comment": o.comment,
        "items": json.loads(o.items or "[]"),
        "total": o.total, "status": o.status,
        "created_at": str(o.created_at)
    } for o in orders]


@router.patch("/api/admin/shop-orders/{order_id}")
def update_shop_order(order_id: int, data: dict, request: Request, db: Session = Depends(get_db)):
    from database import ShopOrder
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    o = db.query(ShopOrder).filter(ShopOrder.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404)
    if "status" in data:
        o.status = data["status"]
    db.commit()
    return {"ok": True, "status": o.status}


@router.get("/admin/shop-orders", response_class=HTMLResponse)
def shop_orders_page():
    with open("static/shop_orders.html", encoding="utf-8") as f:
        return f.read()


@router.get("/shop/my-orders", response_class=HTMLResponse)
def my_orders_page():
    with open("static/my_orders.html", encoding="utf-8") as f:
        return f.read()
