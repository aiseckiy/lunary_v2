"""Shared utility helpers — используются в api.py, routers/*, bot.py."""
import os
from datetime import datetime, timedelta, timezone

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")


def save_upload(content: bytes, original_name: str, file_type: str, records: int, db):
    """Сохраняет файл на диск и пишет запись в uploaded_files."""
    from database import UploadedFile
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = original_name.replace(" ", "_")
    saved_name = f"{ts}_{file_type}_{safe_name}"
    path = os.path.join(UPLOADS_DIR, saved_name)
    try:
        with open(path, "wb") as f:
            f.write(content)
    except Exception:
        saved_name = None
    db.add(UploadedFile(
        original_name=original_name,
        saved_name=saved_name,
        file_type=file_type,
        size_bytes=len(content),
        records=records,
    ))
    db.commit()


_save_upload = save_upload


def parse_images(product) -> list:
    """Возвращает список изображений товара (из поля images JSON или image_url)."""
    import json
    if product.images:
        try:
            imgs = json.loads(product.images)
            if isinstance(imgs, list) and imgs:
                return imgs
        except Exception:
            pass
    if product.image_url:
        return [product.image_url]
    return []


_parse_images = parse_images


# ══════════════════════════════════════════════════════
# Auth / session helpers
# ══════════════════════════════════════════════════════
def make_session_token() -> str:
    """Стабильный admin-токен на основе ADMIN_PASSWORD. Пустой если пароль не задан."""
    pwd = os.getenv("ADMIN_PASSWORD", "")
    if not pwd:
        return ""
    import hashlib
    return hashlib.sha256(f"lunary-session-{pwd}".encode()).hexdigest()


SESSION_TOKEN = make_session_token()


def get_user_from_session(request):
    """Возвращает user dict по session cookie, или None."""
    session = request.cookies.get("lunary_session", "")
    if not session:
        return None
    if session == SESSION_TOKEN and SESSION_TOKEN:
        return {"role": "admin", "name": "Admin", "email": ""}
    import hashlib
    from database import SessionLocal, User as UserModel
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == int(session.split("_")[0])).first() if "_" in session else None
        if user:
            expected = hashlib.sha256(
                f"user-{user.id}-{user.email}-{os.getenv('ADMIN_PASSWORD','lunary-secret')}".encode()
            ).hexdigest()
            if session == f"{user.id}_{expected}":
                return {
                    "role": user.role,
                    "name": user.name,
                    "email": user.email,
                    "id": user.id,
                    "phone": user.phone or "",
                }
    except Exception:
        pass
    finally:
        db.close()
    return None


def is_staff(user) -> bool:
    return bool(user and user.get("role") in ("admin", "manager"))


def is_admin(user) -> bool:
    return bool(user and user.get("role") == "admin")


# Backwards-compat aliases
_make_session_token = make_session_token
_get_user_from_session = get_user_from_session
_is_staff = is_staff
_is_admin = is_admin


# ══════════════════════════════════════════════════════
# Kaspi / integrations helpers
# ══════════════════════════════════════════════════════
def decode_kaspi_order_id(raw_id: str) -> str:
    """Kaspi API возвращает order_id в base64. Декодируем в числовой ID."""
    import base64
    s = str(raw_id).strip()
    if s.isdigit():
        return s
    try:
        decoded = base64.b64decode(s + "==").decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return s


def get_integration(setting_key: str, env_var: str) -> str:
    """Читает настройку интеграции: сначала из БД (SiteSetting), потом из ENV."""
    try:
        from database import SiteSetting, SessionLocal as _SL
        db = _SL()
        row = db.query(SiteSetting).filter(SiteSetting.key == setting_key).first()
        db.close()
        if row and row.value and row.value.strip():
            return row.value.strip()
    except Exception:
        pass
    return os.getenv(env_var, "")


_decode_kaspi_order_id = decode_kaspi_order_id
_get_integration = get_integration


# ══════════════════════════════════════════════════════
# Kaspi stock / notifications helpers
# ══════════════════════════════════════════════════════
ARCHIVE_STATES = {"ARCHIVE", "Выдан"}
DEDUCT_STATES = {"KASPI_DELIVERY", "DELIVERY", "PICKUP", "ARCHIVE", "Выдан"}
CANCEL_STATES = {"CANCELLED", "Отменен", "RETURN", "Возврат"}


def send_tg_notification(text: str):
    """Отправить сообщение в Telegram (не блокирует caller)."""
    import threading
    import urllib.parse
    import urllib.request
    bot_token = get_integration("tg_bot_token", "BOT_TOKEN")
    chat_id = get_integration("tg_chat_id", "ADMIN_CHAT_ID")
    if not bot_token or not chat_id:
        return

    def _send():
        try:
            params = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage?{params}"
            urllib.request.urlopen(url, timeout=3)
        except Exception as e:
            print(f"⚠️ TG уведомление ошибка: {e}")

    threading.Thread(target=_send, daemon=True).start()


def format_order_notification(o: dict) -> str:
    """Форматирует новый Kaspi-заказ для Telegram-уведомления."""
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
    raw_id = o.get("id", "")
    code = decode_kaspi_order_id(str(raw_id)) if raw_id else ""
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

    lines = [
        f"<b>{label}</b>",
        f"🛒 Заказ <b>#{code}</b>",
        f"👤 {customer}",
    ]
    if delivery_mode:
        lines.append(f"📦 {delivery_mode}")
    if addr_str:
        lines.append(f"📍 {addr_str}")
    if planned_str:
        lines.append(f"📅 Дата доставки: {planned_str}")
    if payment:
        lines.append(f"💳 {payment}")
    lines.append("")

    if not entries:
        try:
            import kaspi as kaspi_module
            entries = kaspi_module.get_order_entries(str(code)) or []
        except Exception:
            entries = []

    for e in entries:
        qty = e.get('qty', 1)
        price = int(e.get('basePrice', e.get('price', 0)))
        name = e.get('name') or '—'
        sku = e.get('sku') or e.get('merchantSku') or ''
        sku_str = f" <code>{sku}</code>" if sku else ""
        lines.append(f"  • {name}{sku_str} — {qty} шт × {price:,} ₸".replace(",", " "))

    lines.append("")
    lines.append(f"<b>Итого: {total:,} ₸</b>".replace(",", " "))
    return "\n".join(lines)


def find_product_by_sku(merchant_sku: str, db):
    """Ищет товар по kaspi_sku с поддержкой нескольких SKU через запятую."""
    from database import Product
    if not merchant_sku:
        return None
    all_products = db.query(Product).filter(Product.kaspi_sku.isnot(None)).all()
    for p in all_products:
        skus = [s.strip() for s in (p.kaspi_sku or "").split(",") if s.strip()]
        if merchant_sku in skus:
            return p
        for s in skus:
            if s == merchant_sku or merchant_sku.startswith(s + "_") or s.startswith(merchant_sku + "_"):
                return p
    return None


def deduct_stock_for_order(order_row, db):
    """Списывает остатки по заказу. Вызывается один раз при переходе в DEDUCT_STATES."""
    import json
    import crud
    from database import Product

    deducted = []

    if order_row.sku and order_row.quantity:
        product = find_product_by_sku(order_row.sku, db)
        if product:
            crud.add_movement(product.id, order_row.quantity, "sale", db,
                              source="kaspi", note=f"Kaspi заказ {order_row.order_id}")
            deducted.append((product.name, order_row.quantity))
            return deducted

    if order_row.entries:
        try:
            entries = json.loads(order_row.entries)
        except Exception:
            entries = []
        for entry in entries:
            name = entry.get("name", "")
            merchant_sku = entry.get("merchantSku", "")
            qty = int(entry.get("qty", 1))
            if qty <= 0:
                continue
            product = None
            if merchant_sku:
                product = find_product_by_sku(merchant_sku, db)
            if not product and name:
                product = db.query(Product).filter(
                    Product.kaspi_sku.isnot(None),
                    Product.name.ilike(f"%{name[:30]}%")
                ).first()
            if not product and name:
                product = db.query(Product).filter(Product.name == name).first()
            if product:
                crud.add_movement(product.id, qty, "sale", db,
                                  source="kaspi", note=f"Kaspi заказ {order_row.order_id}")
                deducted.append((product.name, qty))

    return deducted


def return_stock_for_order(order_row, db):
    """Возвращает остатки при отмене заказа (если уже были списаны)."""
    import json
    import crud
    entries = []
    if order_row.entries:
        try:
            entries = json.loads(order_row.entries)
        except Exception:
            pass
    if not entries and order_row.sku and order_row.quantity:
        entries = [{"merchantSku": order_row.sku, "qty": order_row.quantity}]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        merchant_sku = entry.get("merchantSku", "")
        qty = int(entry.get("qty", 1))
        if qty <= 0:
            continue
        product = None
        if merchant_sku:
            product = find_product_by_sku(merchant_sku, db)
        if product:
            crud.add_movement(product.id, qty, "return", db,
                              source="kaspi", note=f"Возврат: отмена заказа {order_row.order_id}")


_send_tg_notification = send_tg_notification
_format_order_notification = format_order_notification
_find_product_by_sku = find_product_by_sku
_deduct_stock_for_order = deduct_stock_for_order
_return_stock_for_order = return_stock_for_order


def parse_order_date(date_str):
    """Парсит дату заказа из dd.mm.yyyy или Unix ms timestamp (UTC+5 Казахстан).
    Возвращает datetime или None."""
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        if '.' in s:
            return datetime.strptime(s, "%d.%m.%Y")
        ts = int(float(s))
        if ts > 1_000_000_000_000:
            ts //= 1000
        tz_kz = timezone(timedelta(hours=5))
        return datetime.fromtimestamp(ts, tz=tz_kz).replace(tzinfo=None)
    except Exception:
        return None


def filter_orders_by_date(rows, date_from, date_to):
    """Фильтр KaspiOrder-строк по дате.

    Активные заказы (NEW/DELIVERY/PICKUP/KASPI_DELIVERY/APPROVED/SIGN_REQUIRED)
    показываем ВСЕГДА — они сейчас в работе.
    Архивные (ARCHIVE/Выдан) фильтруем по status_date.
    Остальные — по order_date.
    """
    ACTIVE_STATES = {"NEW", "APPROVED", "DELIVERY", "KASPI_DELIVERY", "PICKUP", "SIGN_REQUIRED"}
    ARCHIVE_STATES = {"ARCHIVE", "Выдан"}

    df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if date_to else None
    if not df and not dt:
        return rows
    result = []
    for r in rows:
        state = getattr(r, "state", "")
        if state in ACTIVE_STATES:
            result.append(r)
            continue
        if state in ARCHIVE_STATES and getattr(r, "status_date", None):
            date_str = r.status_date
        else:
            date_str = getattr(r, "order_date", None)
        d = parse_order_date(date_str)
        if d is None:
            continue
        if df and d < df:
            continue
        if dt and d > dt:
            continue
        result.append(r)
    return result


# Backwards-compat aliases (с underscore префиксом, как раньше в api.py)
_parse_order_date = parse_order_date
_filter_orders_by_date = filter_orders_by_date
