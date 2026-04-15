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
