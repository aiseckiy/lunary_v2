from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os
import json
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime

from database import get_db, init_db

# ── Папка для загрузок ────────────────────────────────────────────────────────
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ── Глобальный трекер процессов ──────────────────────────────────────────────
_PROCESS_STATUS = {
    "kaspi_sync": {
        "status": "starting",   # starting | running | idle | error
        "last_run": None,
        "next_run": None,
        "last_result": None,
        "last_error": None,
        "cycle_count": 0,
    },
    "server_start": datetime.utcnow().isoformat(),
}
APP_START = time.monotonic()

def _save_upload(content: bytes, original_name: str, file_type: str, records: int, db):
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
        saved_name = None  # если диск недоступен — не критично
    db.add(UploadedFile(
        original_name=original_name,
        saved_name=saved_name,
        file_type=file_type,
        size_bytes=len(content),
        records=records,
    ))
    db.commit()
import crud
import kaspi as kaspi_module




def _decode_kaspi_order_id(raw_id: str) -> str:
    """Kaspi API возвращает order_id в base64. Декодируем в числовой ID."""
    import base64
    s = str(raw_id).strip()
    # Если уже числовой — оставляем как есть
    if s.isdigit():
        return s
    try:
        decoded = base64.b64decode(s + "==").decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return s


def _parse_order_date(date_str) -> datetime | None:
    """Парсит дату заказа из dd.mm.yyyy или Unix ms timestamp (UTC+5 Казахстан)"""
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        if '.' in s:
            return datetime.strptime(s, "%d.%m.%Y")
        ts = int(float(s))
        if ts > 1_000_000_000_000:
            ts //= 1000
        # Kaspi хранит время в UTC, добавляем +5 часов для Казахстана
        from datetime import timezone, timedelta
        tz_kz = timezone(timedelta(hours=5))
        return datetime.fromtimestamp(ts, tz=tz_kz).replace(tzinfo=None)
    except Exception:
        return None

import secrets
from fastapi import Request, Cookie
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Сессионный токен — стабильный между перезапусками (на основе пароля)
def _make_session_token():
    pwd = os.getenv("ADMIN_PASSWORD", "")
    if not pwd:
        return ""
    import hashlib
    return hashlib.sha256(f"lunary-session-{pwd}".encode()).hexdigest()

_SESSION_TOKEN = _make_session_token()

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
_PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/logout",
                 "/auth/google", "/auth/google/callback",
                 "/shop", "/", "/about", "/api/settings"}
_PUBLIC_PREFIXES = ("/static/", "/api/store/", "/api/shop/", "/shop/product/")
_ADMIN_PATHS = ("/admin", "/kaspi", "/analytics", "/history", "/scanner",
                "/api/kaspi", "/api/analytics", "/api/products", "/api/movements",
                "/api/history", "/api/admin", "/api/purchases", "/api/alerts",
                "/merge", "/review", "/import", "/pricelist", "/uploads", "/api/merge", "/api/import-price-list",
                "/api/kaspi/import", "/api/reset-products", "/api/fill-articles", "/api/pricelist", "/api/uploads")


def _get_user_from_session(request: Request):
    """Возвращает user из БД по session cookie, или None"""
    session = request.cookies.get("lunary_session", "")
    if not session:
        return None
    # Старый admin-пароль
    if session == _SESSION_TOKEN and _SESSION_TOKEN:
        return {"role": "admin", "name": "Admin", "email": ""}
    # Google OAuth session
    import hashlib
    from database import SessionLocal, User as UserModel
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == int(session.split("_")[0])).first() if "_" in session else None
        if user:
            expected = hashlib.sha256(f"user-{user.id}-{user.email}-{os.getenv('ADMIN_PASSWORD','lunary-secret')}".encode()).hexdigest()
            if session == f"{user.id}_{expected}":
                return {"role": user.role, "name": user.name, "email": user.email, "id": user.id, "phone": user.phone or ""}
    except Exception:
        pass
    finally:
        db.close()
    return None


def _is_staff(user) -> bool:
    """admin или manager — доступ к складу/заказам"""
    return user and user.get("role") in ("admin", "manager")

def _is_admin(user) -> bool:
    """только admin — настройки, удаление, сброс"""
    return user and user.get("role") == "admin"

# Пути доступные manager (не только admin)
_MANAGER_ALLOWED_PREFIXES = (
    "/admin", "/kaspi", "/analytics", "/history", "/scanner",
    "/api/kaspi", "/api/analytics", "/api/products", "/api/movements",
    "/api/history", "/api/purchases", "/api/alerts",
    "/merge", "/review", "/import", "/pricelist", "/uploads",
    "/api/merge", "/api/import-price-list", "/api/pricelist", "/api/uploads",
)
# Пути только для admin (опасные операции)
_ADMIN_ONLY_PREFIXES = (
    "/api/admin/settings", "/api/admin/users", "/api/reset-products",
    "/api/fill-articles", "/api/admin/fill", "/admin/settings", "/admin/theme",
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Публичные пути — без авторизации
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        user = _get_user_from_session(request)

        # Только-admin пути (настройки, сброс, пользователи)
        is_admin_only = any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES)
        if is_admin_only:
            if not _is_admin(user):
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Forbidden"}, status_code=403)
                return RedirectResponse("/shop/login?next=" + path)

        # Удаление — только admin
        if method == "DELETE" and any(path.startswith(p) for p in ("/api/products", "/api/kaspi", "/api/movements")):
            if not _is_admin(user):
                return JSONResponse({"detail": "Только администратор может удалять"}, status_code=403)

        # Админские пути — admin или manager
        is_admin_path = any(path.startswith(p) for p in _ADMIN_PATHS)
        if is_admin_path:
            if not _is_staff(user):
                if path.startswith("/api/"):
                    return JSONResponse({"error": "Forbidden"}, status_code=403)
                return RedirectResponse(f"/login?next={path}", status_code=302)

        # Пути требующие авторизации (оформление заказа и т.д.)
        _AUTH_REQUIRED = ("/api/orders",)
        if any(path.startswith(p) for p in _AUTH_REQUIRED):
            if not user:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)

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
    kaspi_sku: Optional[str] = None
    kaspi_article: Optional[str] = None
    cost_price: Optional[int] = None
    supplier: Optional[str] = None
    supplier_article: Optional[str] = None
    description: Optional[str] = None
    specs: Optional[str] = None
    show_in_shop: Optional[bool] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    meta_keywords: Optional[str] = None


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


# ─── Integration settings helper ────────────────────────────
def _get_integration(setting_key: str, env_var: str) -> str:
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


# ─── Startup ─────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    _auto_import_if_empty()
    _start_kaspi_sync_loop()


def _start_kaspi_sync_loop():
    """Фоновая синхронизация заказов Kaspi каждые 5 минут"""

    from database import KaspiOrder, SessionLocal as SL

    STATES = ["NEW", "APPROVED", "PICKUP", "DELIVERY", "KASPI_DELIVERY", "ARCHIVE", "CANCELLED", "SIGN_REQUIRED"]

    _backfill_fetched_this_cycle = [0]  # только для дозагрузки старых заказов
    MAX_BACKFILL_PER_CYCLE = 30         # не более 30 старых заказов за цикл

    def _fetch_entries(oid, is_new=False):
        """Загружает состав заказа. Новые заказы — всегда, старые — до 30 за цикл."""
        if not is_new and _backfill_fetched_this_cycle[0] >= MAX_BACKFILL_PER_CYCLE:
            return []
        try:
            result = kaspi_module.get_order_entries(oid) or []
            print(f"[sync entries] oid={oid} is_new={is_new} got={len(result)}", flush=True)
            if not is_new:
                _backfill_fetched_this_cycle[0] += 1
                time.sleep(0.2)
            return result
        except Exception as e:
            print(f"[sync entries] oid={oid} ОШИБКА: {e}", flush=True)
            return []

    def _update_entries_fields(row, entries):
        """Обновляет entries + product_name/sku/quantity"""
        row.entries = json.dumps(entries, ensure_ascii=False)
        if entries and not row.product_name:
            row.product_name = entries[0].get("name")
            row.sku = entries[0].get("merchantSku", "")
            row.quantity = sum(e.get("qty", 1) for e in entries if isinstance(e, dict))

    def sync():
        while True:
            cycle_start = time.monotonic()
            _PROCESS_STATUS["kaspi_sync"]["status"] = "running"
            _PROCESS_STATUS["kaspi_sync"]["last_run"] = datetime.utcnow().isoformat()
            _PROCESS_STATUS["kaspi_sync"]["cycle_count"] += 1
            db = SL()
            try:
                from database import SyncLog
                from datetime import timezone, timedelta
                tz_kz = timezone(timedelta(hours=5))
                sync_start = datetime.utcnow()

                all_orders = []
                for state in STATES:
                    result = kaspi_module.get_kaspi_orders(state=state, size=100)
                    if result.get("orders"):
                        all_orders.extend(result["orders"])

                # Дедупликация: один заказ может прийти из нескольких state-запросов
                seen_ids = {}
                for o in all_orders:
                    oid = _decode_kaspi_order_id(o["id"])
                    if oid not in seen_ids:
                        seen_ids[oid] = o
                all_orders = list(seen_ids.values())

                added = 0
                updated_count = 0
                returns_count = 0
                deducted_count = 0
                new_orders = []
                _backfill_fetched_this_cycle[0] = 0  # сброс счётчика

                for o in all_orders:
                    raw_date = o.get("date", "")
                    try:
                        d = _parse_order_date(raw_date)
                        order_date = d.strftime("%d.%m.%Y") if d else str(raw_date)
                    except Exception:
                        order_date = str(raw_date)

                    oid = _decode_kaspi_order_id(o["id"])
                    addr_obj = o.get("deliveryAddress")
                    address = addr_obj.get("formattedAddress", "") if isinstance(addr_obj, dict) else str(addr_obj or "")

                    existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == oid).first()
                    if not existing and oid != str(o["id"]):
                        existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == str(o["id"])).first()
                        if existing:
                            existing.order_id = oid

                    if existing:
                        old_state = existing.state
                        new_state = o.get("state", existing.state)

                        # Остатки: списываем / возвращаем при смене статуса
                        if new_state in DEDUCT_STATES and old_state not in DEDUCT_STATES and not existing.stock_deducted:
                            _deduct_stock_for_order(existing, db)
                            existing.stock_deducted = 1
                            deducted_count += 1
                        elif new_state in CANCEL_STATES and old_state not in CANCEL_STATES and existing.stock_deducted:
                            _return_stock_for_order(existing, db)
                            existing.stock_deducted = 0
                            returns_count += 1

                        if new_state != old_state:
                            updated_count += 1

                        # Всегда обновляем все поля из свежего ответа Kaspi
                        existing.state = new_state
                        existing.last_synced_at = datetime.utcnow()
                        existing.total = int(o.get("total", existing.total or 0))
                        existing.customer = o.get("customer", existing.customer)
                        existing.delivery_method = o.get("deliveryMode", existing.delivery_method)
                        existing.payment_method = o.get("paymentMode", existing.payment_method)
                        if address:
                            existing.address = address
                        if new_state == "ARCHIVE" and old_state != "ARCHIVE" and not existing.status_date:
                            existing.status_date = datetime.now(tz=tz_kz).strftime("%d.%m.%Y")

                        # Грузим состав если пустой — только для активных заказов
                        # Kaspi не отдаёт entries для архивных/завершённых заказов
                        ACTIVE_FOR_ENTRIES = {"NEW", "APPROVED", "DELIVERY", "KASPI_DELIVERY", "PICKUP", "SIGN_REQUIRED"}
                        if (not existing.entries or existing.entries in ("[]", "")) and new_state in ACTIVE_FOR_ENTRIES:
                            entries = _fetch_entries(oid, is_new=False)
                            if entries:
                                _update_entries_fields(existing, entries)
                                o["entries"] = entries
                    else:
                        # Новый заказ — грузим состав всегда без лимита
                        entries = _fetch_entries(oid, is_new=True)
                        product_name, sku, quantity = None, None, None
                        if entries:
                            product_name = entries[0].get("name")
                            sku = entries[0].get("merchantSku") or (entries[0].get("sku", "") if isinstance(entries[0], dict) else None)
                            quantity = sum(e.get("qty", e.get("quantity", 1)) for e in entries if isinstance(e, dict))

                        new_ko = KaspiOrder(
                            order_id=oid,
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
                            last_synced_at=datetime.utcnow(),
                        )
                        db.add(new_ko)
                        added += 1
                        o["id"] = oid
                        o["entries"] = entries
                        new_orders.append(o)

                # Записываем лог синхронизации
                db.add(SyncLog(
                    synced_at=sync_start.replace(tzinfo=None),
                    total_found=len(all_orders),
                    added=added,
                    updated=updated_count,
                    returns=returns_count,
                    deducted=deducted_count,
                ))
                db.commit()
                # Удаляем записи старше 1 дня
                cutoff = datetime.utcnow() - timedelta(days=1)
                db.query(SyncLog).filter(SyncLog.synced_at < cutoff).delete(synchronize_session=False)
                db.commit()

                notify_states = {"NEW", "PICKUP", "KASPI_DELIVERY", "DELIVERY"}
                for o in new_orders:
                    if o.get("state") in notify_states:
                        _send_tg_notification(_format_order_notification(o))

                _PROCESS_STATUS["kaspi_sync"]["last_result"] = f"+{added} новых, обновлено {updated_count}"
                _PROCESS_STATUS["kaspi_sync"]["last_error"] = None
                if added or returns_count:
                    print(f"✅ Kaspi sync: +{added} новых, обновлено {updated_count}, возвратов {returns_count}")
            except Exception as e:
                print(f"⚠️ Kaspi sync error: {e}")
                _PROCESS_STATUS["kaspi_sync"]["status"] = "error"
                _PROCESS_STATUS["kaspi_sync"]["last_error"] = str(e)[:300]
                try:
                    from database import SyncLog
                    db.add(SyncLog(synced_at=datetime.utcnow(), error=str(e)[:500]))
                    db.commit()
                except Exception:
                    pass
            finally:
                db.close()
            # Ждём ровно 5 минут от начала цикла (не от конца)
            elapsed = time.monotonic() - cycle_start
            sleep_sec = max(0, 300 - elapsed)
            _PROCESS_STATUS["kaspi_sync"]["status"] = "idle"
            _PROCESS_STATUS["kaspi_sync"]["next_run"] = (datetime.utcnow().timestamp() + sleep_sec)
            time.sleep(sleep_sec)

    t = threading.Thread(target=sync, daemon=True)
    t.start()


def _auto_import_if_empty():
    """Если база пустая — автоматически импортируем товары из export_products.json"""
    import os
    from database import Product as _P, SessionLocal
    db = SessionLocal()
    try:
        count = db.query(_P).count()
        if count > 0:
            return
        path = os.path.join(os.path.dirname(__file__), '_archive', 'export_products.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        added = 0
        for p in data:
            sku = p['sku'].upper()
            if False:  # sku field removed
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


def _seed_kaspi_from_xml():
    """
    Первый старт на Railway: если kaspi_orders пустая — загрузить
    исторические заказы из XML. После этого синк с API дополняет новыми.
    """
    import os
    from database import KaspiOrder, SessionLocal
    db = SessionLocal()
    try:
        count = db.query(KaspiOrder).count()
        if count > 0:
            print(f"ℹ️ kaspi_orders уже содержит {count} заказов — XML пропущен")
            return
        xml_path = os.path.join(os.path.dirname(__file__), 'lunary_all_orders (1).xml')
        if not os.path.exists(xml_path):
            print("⚠️ XML файл не найден — исторические заказы не загружены")
            return
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '_archive'))
        import import_xml
        orders = import_xml.parse_orders(xml_path)
        inserted, updated = import_xml.upsert_orders(orders)
        print(f"✅ Исторические заказы загружены: {inserted} добавлено, {updated} обновлено")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки исторических заказов: {e}")
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
        # Пароль не задан — вход ЗАПРЕЩЁН, требуется настройка
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD не задан — настройте переменную окружения на сервере")
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


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class EmailLoginRequest(BaseModel):
    email: str
    password: str


def _make_user_session(user) -> str:
    import hashlib
    secret = os.getenv("ADMIN_PASSWORD", "lunary-secret")
    return f"{user.id}_{hashlib.sha256(f'user-{user.id}-{user.email}-{secret}'.encode()).hexdigest()}"


@app.post("/api/auth/register")
def auth_register(data: RegisterRequest, db: Session = Depends(get_db)):
    import hashlib
    from database import User as UserModel
    if db.query(UserModel).filter(UserModel.email == data.email.lower()).first():
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    pw_hash = hashlib.sha256(data.password.encode()).hexdigest()
    _ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "aiseckiy@gmail.com").split(",")}
    role = "admin" if data.email.lower() in _ADMIN_EMAILS else "customer"
    user = UserModel(email=data.email.lower(), name=data.name, password_hash=pw_hash, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = JSONResponse({"ok": True, "name": user.name, "role": user.role})
    resp.set_cookie("lunary_session", _make_user_session(user), httponly=True, samesite="lax", max_age=60*60*24*30)
    return resp


@app.post("/api/auth/email-login")
def auth_email_login(data: EmailLoginRequest, db: Session = Depends(get_db)):
    import hashlib
    from database import User as UserModel
    user = db.query(UserModel).filter(UserModel.email == data.email.lower()).first()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    pw_hash = hashlib.sha256(data.password.encode()).hexdigest()
    if user.password_hash != pw_hash:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    _ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "aiseckiy@gmail.com").split(",")}
    if user.email in _ADMIN_EMAILS and user.role != "admin":
        user.role = "admin"
        db.commit()
    resp = JSONResponse({"ok": True, "name": user.name, "role": user.role})
    resp.set_cookie("lunary_session", _make_user_session(user), httponly=True, samesite="lax", max_age=60*60*24*30)
    return resp


# ─── Google OAuth ─────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://www.lunary.kz/auth/google/callback")

@app.get("/auth/google")
def google_auth():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth не настроен")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(code: str, request: Request, db: Session = Depends(get_db)):
    import hashlib
    from database import User as UserModel

    # Обмен code на токен
    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data)
    try:
        with urllib.request.urlopen(req) as r:
            token = json.loads(r.read())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка токена: {e}")

    # Получить профиль пользователя
    access_token = token.get("access_token")
    req2 = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    with urllib.request.urlopen(req2) as r:
        profile = json.loads(r.read())

    email = profile.get("email", "")
    google_id = profile.get("id", "")
    name = profile.get("name", "")
    avatar = profile.get("picture", "")

    _ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "aiseckiy@gmail.com").split(",")}
    auto_role = "admin" if email.lower() in _ADMIN_EMAILS else "customer"

    # Найти или создать пользователя
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if not user:
        user = UserModel(email=email, google_id=google_id, name=name, avatar=avatar, role=auto_role)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.google_id = google_id
        user.name = name
        user.avatar = avatar
        if email.lower() in _ADMIN_EMAILS and user.role != "admin":
            user.role = "admin"
        db.commit()

    next_url = "/admin" if user.role in ("admin", "manager") else "/shop"
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie("lunary_session", _make_user_session(user), httponly=True, samesite="lax", max_age=60*60*24*30)
    return resp


@app.get("/api/auth/check")
def auth_check():
    has_password = bool(os.getenv("ADMIN_PASSWORD", ""))
    return {"required": has_password}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return user


@app.patch("/api/auth/profile")
def update_profile(data: dict, request: Request, db: Session = Depends(get_db)):
    import hashlib
    from database import User as UserModel
    user = _get_user_from_session(request)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401)
    u = db.query(UserModel).filter(UserModel.id == user["id"]).first()
    if not u:
        raise HTTPException(status_code=404)
    if "name" in data and data["name"]:
        u.name = data["name"].strip()
    if "phone" in data:
        u.phone = data["phone"].strip()
    if "password" in data and data["password"]:
        if len(data["password"]) < 6:
            raise HTTPException(status_code=400, detail="Минимум 6 символов")
        u.password_hash = hashlib.sha256(data["password"].encode()).hexdigest()
    db.commit()
    return {"ok": True}


# ─── Users management ────────────────────────────────────────
@app.get("/api/admin/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    from database import User as UserModel
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    users = db.query(UserModel).order_by(UserModel.created_at.desc()).all()
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role,
             "avatar": u.avatar, "created_at": str(u.created_at)} for u in users]


@app.patch("/api/admin/users/{user_id}")
def update_user_role(user_id: int, data: dict, request: Request, db: Session = Depends(get_db)):
    from database import User as UserModel
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    u = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404)
    if "role" in data:
        u.role = data["role"]
    db.commit()
    return {"ok": True, "role": u.role}


# ─── Admin ───────────────────────────────────────────────────
@app.post("/api/admin/run-migrations")
def run_migrations():
    """Принудительно применить все pending миграции БД"""
    init_db()
    return {"ok": True, "message": "Миграции применены"}


@app.post("/api/admin/kaspi/backfill-entries")
def backfill_kaspi_entries(request: Request, db: Session = Depends(get_db)):
    """Загружает состав заказов у которых entries пустые"""
    from database import KaspiOrder
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)

    # Все заказы без состава (entries null, пустые или "[]")
    orders = db.query(KaspiOrder).filter(
        (KaspiOrder.entries == None) |
        (KaspiOrder.entries == "[]") |
        (KaspiOrder.entries == "")
    ).all()

    filled = 0
    failed = 0
    for o in orders:
        try:
            entries = kaspi_module.get_order_entries(o.order_id)
            if entries:
                o.entries = json.dumps(entries, ensure_ascii=False)
                # Также обновляем product_name / sku / quantity если были пустые
                if not o.product_name and entries:
                    o.product_name = entries[0].get("name")
                    o.sku = entries[0].get("merchantSku", "")
                    o.quantity = sum(e.get("qty", 1) for e in entries if isinstance(e, dict))
                filled += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    db.commit()
    return {"ok": True, "filled": filled, "failed": failed, "total": len(orders)}


@app.get("/api/admin/kaspi/debug-entries/{order_id}")
def debug_kaspi_entries(order_id: str, request: Request):
    """Временный: посмотреть сырой ответ Kaspi для entries"""
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    raw = kaspi_module._proxy("get_order_entries", {"orderId": order_id})
    return {"raw": raw}


@app.post("/api/admin/dedupe-kaspi-orders")
def dedupe_kaspi_orders(request: Request, db: Session = Depends(get_db)):
    """Удаляет base64 дубли заказов Kaspi, оставляя числовые ID"""
    import base64
    from database import KaspiOrder
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)

    all_orders = db.query(KaspiOrder).all()
    deleted = 0
    migrated = 0

    for o in all_orders:
        oid = o.order_id
        # Если ID не числовой — это base64
        if not oid.isdigit():
            try:
                decoded = base64.b64decode(oid + "==").decode("utf-8").strip()
                if decoded.isdigit():
                    # Есть ли уже числовая версия?
                    numeric = db.query(KaspiOrder).filter(KaspiOrder.order_id == decoded).first()
                    if numeric:
                        # Числовая версия есть — удаляем base64 дубль
                        db.delete(o)
                        deleted += 1
                    else:
                        # Числовой нет — переименовываем
                        o.order_id = decoded
                        migrated += 1
            except Exception:
                pass

    db.commit()
    return {"ok": True, "deleted": deleted, "migrated": migrated}


@app.get("/api/admin/kaspi/sync-log")
def get_sync_log(request: Request, db: Session = Depends(get_db)):
    """Последние 50 запусков синхронизации Kaspi"""
    from database import SyncLog
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    rows = db.query(SyncLog).order_by(SyncLog.id.desc()).limit(50).all()
    from datetime import timezone, timedelta
    tz_kz = timezone(timedelta(hours=5))
    result = []
    for r in rows:
        # конвертируем utc → UTC+5
        dt_kz = r.synced_at.replace(tzinfo=timezone.utc).astimezone(tz_kz) if r.synced_at else None
        result.append({
            "id": r.id,
            "synced_at": dt_kz.strftime("%d.%m.%Y %H:%M:%S") if dt_kz else None,
            "total_found": r.total_found,
            "added": r.added,
            "updated": r.updated,
            "returns": r.returns,
            "deducted": r.deducted,
            "error": r.error,
        })
    return result


# ─── Товары ──────────────────────────────────────────────────
@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    stocks = crud.get_all_stocks(db)
    return [
        {
            "id": s["product"].id,
            "name": s["product"].name,
            "barcode": s["product"].barcode,
            "category": s["product"].category,
            "unit": s["product"].unit,
            "min_stock": s["product"].min_stock,
            "stock": s["stock"],
            "low": s["stock"] <= s["product"].min_stock,
            "brand": s["product"].brand or "",
            "price": s["product"].price,
            "kaspi_sku": s["product"].kaspi_sku or "",
            "cost_price": s["product"].cost_price,
            "supplier": s["product"].supplier or "",
            "supplier_article": s["product"].supplier_article or "",
            "description": s["product"].description or "",
            "specs": s["product"].specs or "[]"
        }
        for s in stocks
    ]


@app.post("/api/products/import/xlsx")
async def products_import_xlsx(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Импорт товаров из Excel. Колонки: Название, Бренд, Категория, Цена, Закуп, SKU (Kaspi), Артикул, Ед. изм., Мин. остаток, Поставщик, Описание"""
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    import openpyxl, io
    from database import Product as _P

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось открыть файл: {e}")

    ws = wb.active
    headers_row = [str(c.value or "").strip().lower() for c in ws[1]]

    # Маппинг колонок по возможным названиям
    COL_MAP = {
        "name":        ["название", "наименование", "товар", "name"],
        "brand":       ["бренд", "brand", "марка", "производитель"],
        "category":    ["категория", "category"],
        "price":       ["цена", "price", "цена (₸)", "розница"],
        "cost_price":  ["закуп", "себестоимость", "закупочная", "cost", "закуп (₸)"],
        "kaspi_sku":   ["sku", "kaspi sku", "sku (kaspi)", "артикул kaspi", "kaspi_sku"],
        "unit":        ["ед. изм.", "единица", "unit", "ед.изм", "ед изм"],
        "min_stock":   ["мин. остаток", "минимум", "min_stock", "мин остаток"],
        "supplier":    ["поставщик", "supplier"],
        "description": ["описание", "description"],
    }

    def find_col(field):
        for alias in COL_MAP[field]:
            for i, h in enumerate(headers_row):
                if alias in h or h in alias:
                    return i
        return None

    cols = {f: find_col(f) for f in COL_MAP}

    def val(row, field):
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        v = row[idx].value
        return v

    created = 0
    updated = 0
    skipped = 0
    errors = []

    for ri, row in enumerate(ws.iter_rows(min_row=2), 2):
        name = val(row, "name")
        if not name or str(name).strip() == "":
            continue
        name = str(name).strip()

        try:
            price_raw = val(row, "price")
            price = float(str(price_raw).replace(" ", "").replace(",", ".")) if price_raw else None
            cost_raw = val(row, "cost_price")
            cost = float(str(cost_raw).replace(" ", "").replace(",", ".")) if cost_raw else None
            min_stock_raw = val(row, "min_stock")
            min_stock = int(float(str(min_stock_raw))) if min_stock_raw else 0
        except Exception:
            price = cost = None
            min_stock = 0

        kaspi_sku = str(val(row, "kaspi_sku") or "").strip() or None
        brand = str(val(row, "brand") or "").strip() or None
        category = str(val(row, "category") or "").strip() or None
        unit = str(val(row, "unit") or "").strip() or "шт"
        supplier = str(val(row, "supplier") or "").strip() or None
        description = str(val(row, "description") or "").strip() or None

        # Ищем существующий товар по kaspi_sku или по имени
        existing = None
        if kaspi_sku:
            existing = db.query(_P).filter(_P.kaspi_sku == kaspi_sku).first()
        if not existing:
            existing = db.query(_P).filter(_P.name == name).first()

        if existing:
            if brand: existing.brand = brand
            if category: existing.category = category
            if price: existing.price = price
            if cost: existing.cost_price = cost
            if kaspi_sku: existing.kaspi_sku = kaspi_sku
            if unit: existing.unit = unit
            if min_stock: existing.min_stock = min_stock
            if supplier: existing.supplier = supplier
            if description: existing.description = description
            updated += 1
        else:
            p = _P(
                name=name, brand=brand, category=category or "Другое",
                price=price, cost_price=cost, kaspi_sku=kaspi_sku,
                unit=unit, min_stock=min_stock, supplier=supplier,
                description=description,
            )
            db.add(p)
            created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


@app.get("/admin/import-xlsx", response_class=HTMLResponse)
def import_xlsx_page(request: Request):
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse("/login")
    with open("static/import_xlsx.html", encoding="utf-8") as f:
        return f.read()


@app.post("/api/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    p = crud.create_product(
        name=data.name, db=db,
        barcode=data.barcode, category=data.category,
        unit=data.unit, min_stock=data.min_stock, brand=data.brand, price=data.price
    )
    return {"id": p.id, "name": p.name}


@app.get("/api/products/suppliers")
def get_suppliers(db: Session = Depends(get_db)):
    from database import Product as _P
    rows = db.query(_P.supplier).filter(
        _P.supplier != None, _P.supplier != "",
        _P.category != "Накладные"
    ).distinct().all()
    return {"suppliers": sorted([r[0] for r in rows if r[0]])}


@app.get("/api/products/stats")
def products_stats(db: Session = Depends(get_db)):
    from database import Product as _P
    from sqlalchemy import func
    rows = db.query(_P.category, func.count(_P.id)).filter(
        _P.category != "Накладные"
    ).group_by(_P.category).all()
    by_category = {cat: cnt for cat, cnt in rows}
    total = sum(by_category.values())
    return {"total": total, "by_category": by_category}


@app.get("/api/products/search")
def search_products(q: str, db: Session = Depends(get_db)):
    products = crud.find_product(q, db)
    if not products:
        return []
    ids = {p.id for p in products}
    all_stocks = crud.get_all_stocks(db)
    stock_map = {s["product"].id: s["stock"] for s in all_stocks if s["product"].id in ids}
    return [
        {
            "id": p.id,
            "name": p.name,
            "kaspi_sku": p.kaspi_sku or "",
            "barcode": p.barcode,
            "stock": stock_map.get(p.id, 0),
            "unit": p.unit,
            "cost_price": p.cost_price,
            "supplier": p.supplier or "",
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
        "sku": p.kaspi_sku or "",
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
    from database import Product as _P
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    updates.pop("sku", None)
    if "barcode" in updates and updates["barcode"]:
        conflict = db.query(_P).filter(_P.barcode == updates["barcode"], _P.id != product_id).first()
        if conflict:
            raise HTTPException(status_code=409, detail=f"Штрихкод уже привязан к товару «{conflict.name}»")
    p = crud.update_product(product_id, db, **updates)
    return {"id": p.id, "name": p.name}


@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    stock = crud.get_stock(product_id, db)
    return {"product": {
        "id": p.id, "name": p.name, "kaspi_sku": p.kaspi_sku or "", "kaspi_article": p.kaspi_article or "",
        "category": p.category or "", "unit": p.unit or "шт",
        "price": p.price, "min_stock": p.min_stock,
        "stock": stock, "low": stock <= (p.min_stock or 0),
        "brand": p.brand or "", "supplier": p.supplier or "",
        "cost_price": p.cost_price, "barcode": p.barcode or "",
        "supplier_article": p.supplier_article or "",
        "images": p.images or "[]",
        "description": p.description or "",
        "specs": p.specs or "[]",
        "show_in_shop": bool(p.show_in_shop),
        "meta_title": p.meta_title or "",
        "meta_description": p.meta_description or "",
        "meta_keywords": p.meta_keywords or "",
    }}


class ProductPatch(BaseModel):
    barcode: Optional[str] = None

@app.patch("/api/products/{product_id}")
def patch_product(product_id: int, data: ProductPatch, db: Session = Depends(get_db)):
    from database import Product
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if data.barcode is not None:
        if data.barcode:
            conflict = db.query(Product).filter(Product.barcode == data.barcode, Product.id != product_id).first()
            if conflict:
                raise HTTPException(status_code=409, detail=f"Штрихкод уже привязан к товару «{conflict.name}» (арт. {conflict.supplier_article or conflict.kaspi_sku or '?'})")
        p.barcode = data.barcode
    db.commit()
    return {"ok": True}


class SetStockBody(BaseModel):
    actual: int
    note: Optional[str] = None

@app.post("/api/products/{product_id}/set-stock")
def set_stock_value(product_id: int, data: SetStockBody, request: Request, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if data.actual < 0:
        raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным")
    current = crud.get_stock(product_id, db)
    delta = data.actual - current
    if delta == 0:
        return {"product": p.name, "new_stock": current, "delta": 0}
    move_type = "income" if delta > 0 else "writeoff"
    note = data.note or f"Коррекция остатка: было {current}, стало {data.actual}"
    u = _get_user_from_session(request)
    crud.add_movement(product_id, abs(delta), move_type, db, "web", note,
                      user_id=u.get("id") if u else None,
                      user_name=u.get("name") or u.get("email") if u else None)
    new_stock = crud.get_stock(product_id, db)
    return {"product": p.name, "new_stock": new_stock, "delta": delta}


@app.get("/api/products/{product_id}/stock")
def get_stock(product_id: int, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    stock = crud.get_stock(product_id, db)
    return {"product_id": product_id, "name": p.name, "stock": stock, "unit": p.unit}


@app.post("/api/products/{product_id}/movement")
def add_movement(product_id: int, data: StockAdjust, request: Request, db: Session = Depends(get_db)):
    try:
        p = crud.get_product_by_id(product_id, db)
        if not p:
            raise HTTPException(status_code=404, detail="Товар не найден")

        if data.type not in ("income", "sale", "writeoff", "return", "adjustment"):
            raise HTTPException(status_code=400, detail="Неверный тип движения")
        if data.quantity <= 0:
            raise HTTPException(status_code=400, detail="Количество должно быть больше нуля")

        u = _get_user_from_session(request)
        m = crud.add_movement(product_id, data.quantity, data.type, db, data.source, data.note,
                              user_id=u.get("id") if u else None,
                              user_name=u.get("name") or u.get("email") if u else None)
        new_stock = crud.get_stock(product_id, db)
        return {
            "movement_id": m.id,
            "product": p.name,
            "type": data.type,
            "quantity": data.quantity,
            "new_stock": new_stock
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print("MOVEMENT ERROR:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


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
                "product_sku": p.kaspi_sku or "",
                "type": m.type,
                "type_label": type_labels.get(m.type, m.type),
                "quantity": m.quantity,
                "source": m.source or "manual",
                "note": m.note or "",
                "date": m.created_at.strftime("%d.%m.%Y"),
                "time": m.created_at.strftime("%H:%M"),
                "datetime_iso": m.created_at.isoformat(),
                "user_name": m.user_name or "",
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


# ─── Публичный магазин ───────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root_page():
    return RedirectResponse("/shop", status_code=302)


def _parse_images(p) -> list:
    """Возвращает список изображений товара."""
    
    if p.images:
        try:
            imgs = json.loads(p.images)
            if isinstance(imgs, list) and imgs:
                return imgs
        except Exception:
            pass
    if p.image_url:
        return [p.image_url]
    return []


@app.get("/shop", response_class=HTMLResponse)
def shop_page():
    with open("static/store.html", encoding="utf-8") as f:
        return f.read()


# ─── Публичный API магазина ───────────────────────────────────
@app.get("/api/store/products")
def store_products(db: Session = Depends(get_db)):
    """Публичный список товаров для магазина — только show_in_shop=True"""
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    stocks = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.show_in_shop == True)
        .group_by(_P.id)
        .all()
    )
    return [
        {
            "id": s[0].id,
            "name": s[0].name,
            "kaspi_sku": s[0].kaspi_sku,
            "category": s[0].category or "Другое",
            "brand": s[0].brand or "",
            "price": s[0].price,
            "unit": s[0].unit or "шт",
            "stock": int(s[1]),
            "image_url": s[0].image_url or "",
            "images": _parse_images(s[0]),
            "supplier_article": s[0].supplier_article or "",
        }
        for s in stocks
    ]


@app.get("/api/store/products/{product_id}/similar")
def store_product_similar(product_id: int, db: Session = Depends(get_db)):
    """Похожие товары — тот же бренд или категория, случайные 8 штук"""
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    import random

    src = db.query(_P).filter(_P.id == product_id, _P.show_in_shop == True).first()
    if not src:
        return []

    q = db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock")) \
        .outerjoin(_M, _M.product_id == _P.id) \
        .filter(_P.id != product_id, _P.show_in_shop == True, _P.price.isnot(None)) \
        .group_by(_P.id)

    if src.brand:
        same_brand = q.filter(_P.brand == src.brand).all()
    else:
        same_brand = []

    same_cat = q.filter(_P.category == src.category, _P.brand != (src.brand or "")).all() if src.category else []

    pool = same_brand[:4] + same_cat[:4]
    if len(pool) < 8:
        extra = q.filter(_P.id.notin_([p.id for p, _ in pool])).limit(8 - len(pool)).all()
        pool += extra

    random.shuffle(pool)
    result = []
    for p, stock in pool[:8]:
        img = None
        try:
            imgs = json.loads(p.images or "[]")
            img = imgs[0] if imgs else p.image_url
        except:
            img = p.image_url
        result.append({
            "id": p.id, "name": p.name, "brand": p.brand or "",
            "price": p.price, "stock": int(stock),
            "image_url": img or "", "category": p.category or "",
        })
    return result


@app.get("/api/store/products/{product_id}")
def store_product_detail(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    row = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.id == product_id, _P.show_in_shop == True)
        .group_by(_P.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p, stock = row
    return {
        "id": p.id, "name": p.name, "sku": p.kaspi_sku or "",
        "supplier_article": p.supplier_article or "",
        "category": p.category or "Другое",
        "brand": p.brand or "",
        "price": p.price, "unit": p.unit or "шт",
        "stock": int(stock), "min_stock": p.min_stock or 0,
        "image_url": p.image_url or "",
        "images": _parse_images(p),
        "description": p.description or "",
        "specs": p.specs or "[]",
    }


class ProductImagesBody(BaseModel):
    images: list  # список URL/base64

@app.post("/api/products/{product_id}/image")
def save_product_images(product_id: int, data: ProductImagesBody, request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только администратор")
    
    from database import Product as _P
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p.images = json.dumps(data.images, ensure_ascii=False)
    p.image_url = data.images[0] if data.images else None
    db.commit()
    return {"ok": True}


@app.get("/api/products/{product_id}/search-images")
def search_product_images(product_id: int, request: Request, db: Session = Depends(get_db)):
    """Ищет картинки через SerpApi (Google Images)"""
    import requests as req_lib
    from database import Product as _P

    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404)

    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="SERPAPI_KEY не задан")

    query = f"{p.brand or ''} {p.name}".strip()
    images = []

    try:
        r = req_lib.get("https://serpapi.com/search", params={
            "engine": "google_images",
            "q": query,
            "api_key": api_key,
            "num": 10,
            "hl": "ru",
            "gl": "kz",
        }, timeout=15)
        data = r.json()
        for item in data.get("images_results", []):
            link = item.get("original")
            if link:
                images.append(link)
    except Exception as e:
        print(f"[image search] ошибка: {e}")

    return {"images": images, "query": query}


@app.get("/api/admin/test-google-images")
def test_google_images(request: Request):
    """Тест SerpApi image search"""
    import requests as req_lib
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)
    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        return {"error": "SERPAPI_KEY не задан в Railway"}
    try:
        r = req_lib.get("https://serpapi.com/search", params={
            "engine": "google_images", "q": "герметик tytan", "api_key": api_key, "num": 2, "hl": "ru", "gl": "kz",
        }, timeout=15)
        data = r.json()
        if "error" in data:
            return {"error": data["error"]}
        items = data.get("images_results", [])
        return {"ok": True, "found": len(items), "first_image": items[0].get("original") if items else None}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/products/{product_id}/ai-describe")
def ai_describe_product(product_id: int, db: Session = Depends(get_db)):
    """Генерирует описание и характеристики товара через OpenAI"""
    from database import Product as _P, SiteSetting
    import os, json

    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        settings = {s.key: s.value for s in db.query(SiteSetting).all()}
        api_key = settings.get("openai_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY не настроен")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        existing_specs = ""
        try:
            specs = json.loads(p.specs or "[]")
            if specs:
                existing_specs = "\nУже известные характеристики:\n" + "\n".join(f"- {s['key']}: {s['value']}" for s in specs)
        except Exception:
            pass

        price_hint = f"- Цена: {p.price} тенге" if p.price else ""
        prompt = f"""Ты SEO-специалист и копирайтер для интернет-магазина строительных материалов в Казахстане (lunary.kz).

Товар:
- Название: {p.name}
- Бренд: {p.brand or 'не указан'}
- Категория: {p.category or 'не указана'}
- Единица: {p.unit or 'шт'}
{price_hint}
{existing_specs}

Задача — заполни 5 полей:

1. **description** — продающее описание (2-4 предложения). Что это, для чего применяется, главные преимущества. Это НОВЫЙ товар — НЕ пиши "состояние", "б/у", "новое". Только технические свойства и применение.

2. **specs** — технические характеристики (5-10 строк). Только реальные параметры: объём, состав, цвет, температура применения, нагрузка и т.п. ЗАПРЕЩЕНО: "Состояние", "Тип объявления", "Наличие", "Страна".

3. **meta_title** — SEO-заголовок страницы (50-60 символов). Формат: "[Название товара] купить в Алматы | LUNARY". Включи главный поисковый запрос по которому люди ищут этот товар.

4. **meta_description** — SEO-описание (150-160 символов). Включи: название, главное применение, призыв к действию ("купить", "заказать"), упомяни Казахстан или Алматы для локального SEO.

5. **meta_keywords** — 8-12 ключевых слов через запятую. Включи: название товара, бренд, синонимы, применение, "купить", "цена", "Алматы", "Казахстан". Разные варианты написания и сочетания запросов.

Верни ТОЛЬКО JSON без markdown-обёртки:
{{
  "description": "текст описания",
  "specs": [{{"key": "Характеристика", "value": "значение"}}],
  "meta_title": "SEO заголовок",
  "meta_description": "SEO описание",
  "meta_keywords": "ключ1, ключ2, ключ3"
}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        # Сохраняем в БД
        p.description = result.get("description", p.description)
        specs_new = result.get("specs", [])
        if specs_new:
            # мёрджим: существующие + новые (без дублей по key)
            try:
                existing = json.loads(p.specs or "[]")
            except Exception:
                existing = []
            existing_keys = {s["key"].lower() for s in existing}
            for s in specs_new:
                if s["key"].lower() not in existing_keys:
                    existing.append(s)
            p.specs = json.dumps(existing, ensure_ascii=False)
        if result.get("meta_title"):
            p.meta_title = result["meta_title"][:70]
        if result.get("meta_description"):
            p.meta_description = result["meta_description"][:200]
        if result.get("meta_keywords"):
            p.meta_keywords = result["meta_keywords"]
        db.commit()

        return {
            "ok": True,
            "description": p.description,
            "specs": json.loads(p.specs or "[]"),
            "meta_title": p.meta_title,
            "meta_description": p.meta_description,
            "meta_keywords": p.meta_keywords,
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI вернул некорректный JSON — попробуйте ещё раз")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка AI: {str(e)[:200]}")


@app.post("/api/admin/fill-descriptions")
async def fill_descriptions_bulk(request: Request, db: Session = Depends(get_db)):
    """Массовая генерация описаний и характеристик для товаров без описания через OpenAI"""
    import os, json, time
    from database import Product as _P, SiteSetting

    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        settings = {s.key: s.value for s in db.query(SiteSetting).all()}
        api_key = settings.get("openai_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY не настроен")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    # Только товары без описания (или из переданного списка)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    id_list = body.get("ids") if isinstance(body, dict) else None

    base_q = db.query(_P)
    if id_list:
        base_q = base_q.filter(_P.id.in_(id_list))
    else:
        base_q = base_q.filter((_P.description == None) | (_P.description == ""))
    products = base_q.all()

    done = 0
    errors = []
    for p in products:
        try:
            existing_specs = ""
            try:
                specs = json.loads(p.specs or "[]")
                if specs:
                    existing_specs = "\nУже известные характеристики:\n" + "\n".join(f"- {s['key']}: {s['value']}" for s in specs)
            except Exception:
                pass

            price_hint = f"- Цена: {p.price} тенге" if p.price else ""
            prompt = f"""Ты SEO-специалист и копирайтер для интернет-магазина строительных материалов в Казахстане (lunary.kz).

Товар:
- Название: {p.name}
- Бренд: {p.brand or 'не указан'}
- Категория: {p.category or 'не указана'}
- Единица: {p.unit or 'шт'}
{price_hint}
{existing_specs}

Заполни 5 полей:
1. **description** — продающее описание (2-4 предложения). НЕ пиши "состояние", "б/у", "новое". Только свойства и применение.
2. **specs** — 5-10 технических характеристик. ЗАПРЕЩЕНО: "Состояние", "Наличие", "Страна".
3. **meta_title** — SEO-заголовок (50-60 символов): "[Название] купить в Алматы | LUNARY"
4. **meta_description** — SEO-описание (150-160 символов) с призывом купить/заказать, упомяни Казахстан.
5. **meta_keywords** — 8-12 ключевых слов через запятую: название, бренд, синонимы, "купить", "цена", "Алматы".

Верни ТОЛЬКО JSON без markdown-обёртки:
{{"description": "текст", "specs": [{{"key": "К", "value": "В"}}], "meta_title": "...", "meta_description": "...", "meta_keywords": "..."}}"""

            response = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            result = json.loads(text)

            p.description = result.get("description", "")
            specs_new = result.get("specs", [])
            if specs_new:
                try: existing = json.loads(p.specs or "[]")
                except: existing = []
                existing_keys = {s["key"].lower() for s in existing}
                for s in specs_new:
                    if s["key"].lower() not in existing_keys:
                        existing.append(s)
                p.specs = json.dumps(existing, ensure_ascii=False)
            if result.get("meta_title"):
                p.meta_title = result["meta_title"][:70]
            if result.get("meta_description"):
                p.meta_description = result["meta_description"][:200]
            if result.get("meta_keywords"):
                p.meta_keywords = result["meta_keywords"]

            db.commit()
            done += 1
            time.sleep(0.3)  # небольшая пауза чтобы не превысить rate limit
        except Exception as e:
            errors.append(f"{p.name}: {str(e)[:100]}")
            print(f"[fill-descriptions] ошибка {p.name}: {e}", flush=True)

    return {"ok": True, "done": done, "total": len(products), "errors": errors}


@app.post("/api/admin/fill-images")
def fill_images_bulk(request: Request, db: Session = Depends(get_db)):
    """Автоматически ищет и заполняет картинки для товаров без фото через SerpApi"""
    import requests as req_lib
    from database import Product as _P

    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="SERPAPI_KEY не задан")

    # Только товары без изображений
    products = db.query(_P).filter(
        (_P.images == None) | (_P.images == "") | (_P.images == "[]")
    ).filter(
        (_P.image_url == None) | (_P.image_url == "")
    ).all()

    filled = 0
    skipped = 0
    errors = 0

    for p in products:
        try:
            query = f"{p.brand or ''} {p.name}".strip()
            r = req_lib.get("https://serpapi.com/search", params={
                "engine": "google_images", "q": query,
                "api_key": api_key, "num": 5, "hl": "ru", "gl": "kz",
            }, timeout=15)
            data = r.json()

            if "error" in data:
                print(f"[fill-images] SerpApi error: {data['error']}")
                errors += 1
                break

            items = data.get("images_results", [])
            if not items:
                skipped += 1
                continue

            links = [i["original"] for i in items if i.get("original")]
            if not links:
                skipped += 1
                continue

            p.images = json.dumps(links[:5], ensure_ascii=False)
            p.image_url = links[0]
            db.commit()
            filled += 1
            time.sleep(0.3)  # небольшая пауза чтобы не перегружать API

        except Exception as e:
            print(f"[fill-images] ошибка для {p.name}: {e}", flush=True)
            errors += 1
            break  # останавливаемся чтобы не тратить запросы

    return {
        "filled": filled,
        "skipped": skipped,
        "errors": errors,
        "remaining_without_images": db.query(_P).filter(
            (_P.images == None) | (_P.images == "") | (_P.images == "[]")
        ).count()
    }


@app.get("/shop/product/{product_id}", response_class=HTMLResponse)
def shop_product_page(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    import html as _html

    BASE_URL = "https://www.lunary.kz"
    row = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.id == product_id, _P.category == "Kaspi")
        .group_by(_P.id)
        .first()
    )

    with open("static/product.html", encoding="utf-8") as f:
        tmpl = f.read()

    if not row:
        return tmpl

    p, stock = row
    name = _html.escape(p.name or "Товар")
    brand = _html.escape(p.brand or "")
    canon = f"{BASE_URL}/shop/product/{product_id}"
    avail = "https://schema.org/InStock" if stock > 0 else "https://schema.org/OutOfStock"
    price_str = str(int(p.price)) if p.price else ""

    # SEO поля — используем заполненные AI или генерируем автоматически
    title = _html.escape(p.meta_title.strip()) if p.meta_title and p.meta_title.strip() \
        else f"{name} купить в Алматы | LUNARY"

    desc_raw = (p.meta_description or "").strip()
    if not desc_raw:
        # Авто-генерация из описания товара
        base = (p.description or f"{brand} {name}").strip()
        price_part = f" Цена {int(p.price):,} ₸.".replace(",", " ") if p.price else ""
        desc_raw = base[:130] + price_part if price_part else base[:155]
    description = _html.escape(desc_raw[:165])

    keywords_raw = (p.meta_keywords or "").strip()
    if not keywords_raw:
        parts = [p.name or ""]
        if p.brand: parts.append(p.brand)
        if p.category: parts.append(p.category)
        parts += ["купить", "цена", "Алматы", "Казахстан", "LUNARY"]
        keywords_raw = ", ".join(parts)
    keywords = _html.escape(keywords_raw[:300])

    image = p.image_url or f"{BASE_URL}/static/og-default.jpg"

    schema = ""
    if p.price:
        schema = f"""<script type="application/ld+json">
{{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "{name}",
  "brand": {{"@type": "Brand", "name": "{brand}"}},
  "description": "{description}",
  "image": "{_html.escape(image)}",
  "url": "{canon}",
  "offers": {{
    "@type": "Offer",
    "priceCurrency": "KZT",
    "price": "{price_str}",
    "availability": "{avail}",
    "seller": {{"@type": "Organization", "name": "LUNARY"}}
  }}
}}
</script>"""

    seo_head = f"""<title>{title}</title>
<meta name="description" content="{description}">
<meta name="keywords" content="{keywords}">
<link rel="canonical" href="{canon}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="product">
<meta property="og:url" content="{canon}">
<meta property="og:image" content="{_html.escape(image)}">
<meta property="og:site_name" content="LUNARY">
<meta property="product:price:amount" content="{price_str}">
<meta property="product:price:currency" content="KZT">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{_html.escape(image)}">
{schema}"""

    # Заменяем старый <title> + вставляем SEO блок
    tmpl = tmpl.replace("<title>Товар — LUNARY</title>", seo_head, 1)
    return tmpl


@app.get("/robots.txt")
def robots_txt():
    from fastapi.responses import PlainTextResponse
    content = """User-agent: *
Allow: /shop
Allow: /shop/product/
Disallow: /admin
Disallow: /api/
Disallow: /import
Disallow: /pricelist
Disallow: /merge
Disallow: /review
Disallow: /uploads
Disallow: /login

Sitemap: https://www.lunary.kz/sitemap.xml
"""
    return PlainTextResponse(content)


@app.get("/sitemap.xml")
def sitemap_xml(db: Session = Depends(get_db)):
    from fastapi.responses import Response
    from database import Product as _P
    import json as _json
    from datetime import datetime

    BASE_URL = "https://www.lunary.kz"
    today = datetime.utcnow().strftime("%Y-%m-%d")

    products = db.query(_P).filter(_P.show_in_shop == True, _P.price.isnot(None)).all()

    urls = [f"""  <url>
    <loc>{BASE_URL}/shop</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
    <lastmod>{today}</lastmod>
  </url>"""]

    for p in products:
        # Собираем картинки товара
        images = []
        try:
            imgs = _json.loads(p.images or "[]")
            if isinstance(imgs, list):
                images = [i for i in imgs if isinstance(i, str) and i.startswith("http")]
        except Exception:
            pass
        if not images and p.image_url and p.image_url.startswith("http"):
            images = [p.image_url]

        image_tags = ""
        for img_url in images[:5]:  # максимум 5 картинок на товар
            name_escaped = (p.name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            image_tags += f"""
    <image:image>
      <image:loc>{img_url}</image:loc>
      <image:title>{name_escaped}</image:title>
    </image:image>"""

        urls.append(f"""  <url>
    <loc>{BASE_URL}/shop/product/{p.id}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <lastmod>{today}</lastmod>{image_tags}
  </url>""")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
    xml += '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
    xml += '\n'.join(urls)
    xml += '\n</urlset>'

    return Response(content=xml, media_type="application/xml")


@app.get("/api/shop/my-orders")
def my_orders(request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Не авторизован")
    from database import ShopOrder
    orders = db.query(ShopOrder).filter(ShopOrder.user_id == user["id"]).order_by(ShopOrder.created_at.desc()).all()
    return [
        {"id": o.id, "status": o.status, "total": o.total,
         "items": o.items, "created_at": str(o.created_at)}
        for o in orders
    ]


@app.get("/shop/my-orders", response_class=HTMLResponse)
def my_orders_page():
    with open("static/my_orders.html", encoding="utf-8") as f:
        return f.read()


@app.get("/about", response_class=HTMLResponse)
def about_page():
    with open("static/about.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/admin/shop-orders/new-count")
def shop_orders_new_count(request: Request, db: Session = Depends(get_db)):
    from database import ShopOrder
    count = db.query(ShopOrder).filter(ShopOrder.status == "new").count()
    return {"count": count}


# ─── Site Settings ───────────────────────────────────────────
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    """Публичные настройки для магазина (без integrations)"""
    from database import SiteSetting
    rows = db.query(SiteSetting).filter(SiteSetting.group != "integrations").all()
    return {r.key: r.value for r in rows}


@app.get("/api/admin/settings")
def get_admin_settings(request: Request, db: Session = Depends(get_db)):
    from database import SiteSetting
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    rows = db.query(SiteSetting).order_by(SiteSetting.group, SiteSetting.key).all()
    return [{"key": r.key, "value": r.value or "", "label": r.label, "group": r.group} for r in rows]


@app.post("/api/admin/settings")
def save_admin_settings(data: dict, request: Request, db: Session = Depends(get_db)):
    from database import SiteSetting
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    for key, value in data.items():
        row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(SiteSetting(key=key, value=str(value)))
    db.commit()
    return {"ok": True}


# ─── Панель управления (только для авторизованных) ───────────
@app.get("/admin", response_class=HTMLResponse)
def dashboard():
    with open("static/index.html") as f:
        return f.read()


@app.get("/admin/scanner", response_class=HTMLResponse)
def scanner():
    with open("static/scanner.html") as f:
        return f.read()


@app.get("/admin/history", response_class=HTMLResponse)
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
    token = _get_integration("kaspi_api_key", "KASPI_TOKEN")
    shop_id = _get_integration("kaspi_shop_id", "KASPI_SHOP_ID")
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
    """Отправить сообщение в Telegram (не блокирует sync loop)."""
    bot_token = _get_integration("tg_bot_token", "BOT_TOKEN")
    chat_id = _get_integration("tg_chat_id", "ADMIN_CHAT_ID")
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


def _format_order_notification(o: dict) -> str:
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
    code = _decode_kaspi_order_id(str(raw_id)) if raw_id else ""
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

    # Если entries нет — пробуем загрузить прямо сейчас
    if not entries:
        try:
            entries = kaspi_module.get_order_entries(str(code)) or []
        except Exception:
            entries = []

    # Показываем состав
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


ARCHIVE_STATES = {"ARCHIVE", "Выдан"}
# Состояния когда товар физически ушёл — списываем остаток
DEDUCT_STATES = {"KASPI_DELIVERY", "DELIVERY", "PICKUP", "ARCHIVE", "Выдан"}
# Состояния отмены — возвращаем остаток если уже списали
CANCEL_STATES = {"CANCELLED", "Отменен", "RETURN", "Возврат"}

def _return_stock_for_order(order_row, db: Session):
    """Возвращает остатки при отмене заказа (если уже были списаны)."""
    from database import Product
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
            product = _find_product_by_sku(merchant_sku, db)
        if product:
            crud.add_movement(product.id, qty, "return", db,
                              source="kaspi", note=f"Возврат: отмена заказа {order_row.order_id}")


def _find_product_by_sku(merchant_sku: str, db: Session):
    """Ищет товар по kaspi_sku с поддержкой нескольких SKU через запятую."""
    from database import Product
    if not merchant_sku:
        return None
    # Точное совпадение или один из нескольких SKU в поле через запятую
    all_products = db.query(Product).filter(Product.kaspi_sku != None).all()
    for p in all_products:
        skus = [s.strip() for s in (p.kaspi_sku or "").split(",") if s.strip()]
        if merchant_sku in skus:
            return p
        # fallback: старый формат "101438761_943240382" матчится по prefix "101438761"
        for s in skus:
            if s == merchant_sku or merchant_sku.startswith(s + "_") or s.startswith(merchant_sku + "_"):
                return p
    return None


def _deduct_stock_for_order(order_row, db: Session):
    """Списывает остатки по заказу. Вызывается один раз при переходе в DEDUCT_STATES."""
    from database import Product

    deducted = []

    # Способ 1: у заказа есть kaspi_sku + quantity (XML-импорт или расширенные поля)
    if order_row.sku and order_row.quantity:
        product = _find_product_by_sku(order_row.sku, db)
        if product:
            crud.add_movement(product.id, order_row.quantity, "sale", db,
                              source="kaspi", note=f"Kaspi заказ {order_row.order_id}")
            deducted.append((product.name, order_row.quantity))
            return deducted

    # Способ 2: entries JSON — матчим по kaspi_sku через name
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
            # Способ 2a: по merchantSku (код товара продавца из Kaspi API)
            # kaspi_sku в БД может быть "101438761_943240382" или просто "101438761"
            if merchant_sku:
                product = _find_product_by_sku(merchant_sku, db)
            # Способ 2b: по названию (ilike первые 30 символов)
            if not product and name:
                product = db.query(Product).filter(
                    Product.kaspi_sku.isnot(None),
                    Product.name.ilike(f"%{name[:30]}%")
                ).first()
            if not product and name:
                # fallback: точное совпадение по name
                product = db.query(Product).filter(Product.name == name).first()
            if product:
                crud.add_movement(product.id, qty, "sale", db,
                                  source="kaspi", note=f"Kaspi заказ {order_row.order_id}")
                deducted.append((product.name, qty))

    return deducted


@app.post("/api/kaspi/orders/sync")
def kaspi_orders_sync(payload: KaspiOrdersPayload, db: Session = Depends(get_db)):
    """Принимает заказы от локального sync скрипта и сохраняет в БД"""
    orders = payload.orders
    from database import KaspiOrder
    added = 0
    updated = 0
    deducted_total = 0
    new_orders = []
    # Дедупликация входящих заказов
    seen = {}
    for o in orders:
        oid = _decode_kaspi_order_id(o["id"])
        seen[oid] = o
    orders = list(seen.values())

    for o in orders:
        oid = _decode_kaspi_order_id(o["id"])
        existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == oid).first()
        if not existing and oid != str(o["id"]):
            existing = db.query(KaspiOrder).filter(KaspiOrder.order_id == str(o["id"])).first()
            if existing:
                existing.order_id = oid
        new_state = o.get("state", "")
        if existing:
            old_state = existing.state
            existing.state = new_state
            updated += 1
            # Списываем при переходе в доставку/архив
            if new_state in DEDUCT_STATES and old_state not in DEDUCT_STATES and not existing.stock_deducted:
                _deduct_stock_for_order(existing, db)
                existing.stock_deducted = 1
                deducted_total += 1
            # Возвращаем при отмене
            elif new_state in CANCEL_STATES and old_state not in CANCEL_STATES and existing.stock_deducted:
                _return_stock_for_order(existing, db)
                existing.stock_deducted = 0
        else:
            ko = KaspiOrder(
                order_id=oid,
                state=new_state,
                total=int(o.get("total", 0)),
                customer=o.get("customer", ""),
                entries=json.dumps(o.get("entries", []), ensure_ascii=False),
                order_date=str(o.get("date", "")),
                stock_deducted=0,
            )
            db.add(ko)
            db.flush()
            added += 1
            new_orders.append(o)
            # Если новый заказ сразу в доставке или архиве — списываем
            if new_state in DEDUCT_STATES:
                _deduct_stock_for_order(ko, db)
                ko.stock_deducted = 1
                deducted_total += 1
    db.commit()

    # Уведомления о новых заказах (только ACCEPTED/PICKUP/KASPI_DELIVERY)
    notify_states = {"ACCEPTED", "PICKUP", "KASPI_DELIVERY"}
    for o in new_orders:
        if o.get("state") in notify_states:
            _send_tg_notification(_format_order_notification(o))

    return {"added": added, "updated": updated, "stock_deducted": deducted_total}


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

    # Date filtering in Python (единая логика с аналитикой)
    if date_from or date_to:
        all_orders = _filter_orders_by_date(all_orders, date_from, date_to)

    total_count = len(all_orders)
    orders = all_orders[offset: offset + limit]

    # Счётчики по вкладкам
    raw_counts = db.query(KaspiOrder.state, func.count(KaspiOrder.id)).group_by(KaspiOrder.state).all()
    state_counts: dict = {}
    for s, c in raw_counts:
        key = STATE_MAP.get(s, s)
        state_counts[key] = state_counts.get(key, 0) + c

    def fmt(o):
        from database import Product
        normalized = STATE_MAP.get(o.state, o.state)
        if o.entries and o.entries != "[]":
            entries = json.loads(o.entries)
        elif o.product_name:
            entries = [{"name": o.product_name, "sku": o.sku or "", "qty": o.quantity or 1, "basePrice": o.total}]
        else:
            entries = []
        # Добавить product_id к каждой позиции для ссылки на карточку
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


@app.get("/api/kaspi/orders/{order_id}/entries")
def get_kaspi_order_entries(order_id: str, db: Session = Depends(get_db)):
    """Получить и сохранить состав конкретного заказа"""
    from database import KaspiOrder
    order = db.query(KaspiOrder).filter(KaspiOrder.order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404)
    # Если уже есть — вернуть
    if order.entries and order.entries not in ("[]", ""):
        return {"entries": json.loads(order.entries)}
    # Загрузить из Kaspi API
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


@app.get("/api/kaspi/export-preview")
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


@app.get("/admin/export-preview")
def export_preview_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/export_preview.html")


def _build_kaspi_xml(db) -> str:
    """Внутренняя функция — строит Kaspi Shopping XML из БД"""
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
    ET.SubElement(root, "company").text   = merchant_id
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

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


@app.get("/api/kaspi/feed.xml", include_in_schema=False)
def kaspi_feed_public(token: str = "", db: Session = Depends(get_db)):
    """Публичный Kaspi Price Feed — Kaspi забирает автоматически по этому URL.
    Защищён токеном: ?token=<kaspi_feed_token из настроек>.
    Если токен не задан в настройках — открыт для всех (для первоначальной настройки).
    """
    from fastapi.responses import Response
    from database import SiteSetting
    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    feed_token = settings.get("kaspi_feed_token", "")
    if feed_token and token != feed_token:
        raise HTTPException(status_code=403, detail="Invalid token")

    xml_str = _build_kaspi_xml(db)
    return Response(content=xml_str, media_type="application/xml",
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/kaspi/export-xml")
def kaspi_export_xml(db: Session = Depends(get_db)):
    """Скачать Kaspi XML (attachment) — для ручной загрузки в Merchant Portal"""
    from fastapi.responses import Response
    xml_str = _build_kaspi_xml(db)
    return Response(content=xml_str, media_type="application/xml",
                    headers={"Content-Disposition": "attachment; filename=ACTIVE.xml"})



# ── Состояние фонового импорта истории ───────────────────────
_history_import_state: dict = {
    "running": False, "done": False, "created": 0, "skipped": 0,
    "errors": 0, "chunk": 0, "total_chunks": 0, "log": []
}

def _run_history_import_bg():
    """Фоновый импорт заказов Kaspi за последний год с полным составом"""
    import time as _time
    from database import KaspiOrder, SessionLocal as _SL
    from sqlalchemy import func

    global _history_import_state
    s = _history_import_state

    STATES = ["COMPLETED", "ARCHIVE", "CANCELLED", "RETURN", "ACCEPTED", "NEW"]
    CHUNK_DAYS = 14
    TOTAL_DAYS = 365

    now_ms = int(_time.time() * 1000)
    day_ms = 24 * 60 * 60 * 1000
    chunks = list(range(0, TOTAL_DAYS, CHUNK_DAYS))
    s["total_chunks"] = len(chunks) * len(STATES)
    s["chunk"] = 0

    db = _SL()
    try:
        for chunk_start in chunks:
            date_le = now_ms - chunk_start * day_ms
            date_ge = now_ms - (chunk_start + CHUNK_DAYS) * day_ms

            for state in STATES:
                if not s["running"]:
                    return
                s["chunk"] += 1
                page = 0
                chunk_created = 0

                while True:
                    try:
                        data = kaspi_module._proxy("get_orders", {
                            "state": state, "page": page, "size": 100,
                            "creationDateGe": str(date_ge),
                            "creationDateLe": str(date_le),
                        })
                    except Exception as e:
                        s["errors"] += 1
                        break

                    if not data:
                        break

                    items = data.get("data", [])
                    if not items:
                        break

                    for item in items:
                        attr = item.get("attributes", {})
                        oid = item.get("id", "")
                        if not oid:
                            continue

                        exists = db.query(KaspiOrder).filter(KaspiOrder.order_id == oid).first()
                        if exists:
                            s["skipped"] += 1
                            continue

                        # Получаем состав заказа (product_name, quantity)
                        pname, qty, sku_val = "", 1, ""
                        try:
                            entries_data = kaspi_module._proxy("get_order_entries", {"orderId": oid})
                            if entries_data and entries_data.get("data"):
                                entry = entries_data["data"][0]
                                ea = entry.get("attributes", {})
                                qty = int(ea.get("quantity", 1))
                                # Название из included
                                included = {i["id"]: i for i in entries_data.get("included", [])}
                                rels = entry.get("relationships", {})
                                prod_rel = rels.get("product", {}).get("data") or rels.get("offer", {}).get("data")
                                if prod_rel:
                                    prod = included.get(prod_rel.get("id"), {})
                                    pname = prod.get("attributes", {}).get("name", "")
                                    sku_val = ea.get("merchantSku", "")
                                if not pname:
                                    pname = ea.get("name", "") or ea.get("merchantSku", "")
                        except Exception:
                            pass

                        # Дата заказа
                        creation_ms = attr.get("creationDate", 0)
                        try:
                            import datetime as _dt
                            order_date = _dt.datetime.fromtimestamp(int(creation_ms) / 1000).strftime("%d.%m.%Y")
                        except Exception:
                            order_date = ""

                        total = int(attr.get("totalPrice", 0) or 0)

                        o = KaspiOrder(
                            order_id=oid,
                            state=attr.get("state", state),
                            total=total,
                            order_date=order_date,
                            product_name=pname or None,
                            sku=sku_val or None,
                            quantity=qty,
                            stock_deducted=0,
                        )
                        db.add(o)
                        s["created"] += 1
                        chunk_created += 1

                    db.commit()

                    if len(items) < 100:
                        break
                    page += 1
                    _time.sleep(0.2)

                if chunk_created > 0:
                    import datetime as _dt2
                    d_from = _dt2.datetime.fromtimestamp(date_ge / 1000).strftime("%d.%m")
                    d_to   = _dt2.datetime.fromtimestamp(date_le / 1000).strftime("%d.%m")
                    s["log"].append(f"{state} {d_from}–{d_to}: +{chunk_created}")
                    if len(s["log"]) > 30:
                        s["log"] = s["log"][-30:]

                _time.sleep(0.3)

    finally:
        db.close()
        s["running"] = False
        s["done"] = True


@app.post("/api/kaspi/import-history")
def kaspi_import_history(request: Request):
    """Запустить фоновый исторический импорт заказов Kaspi за год"""
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    global _history_import_state
    if _history_import_state["running"]:
        return {"ok": False, "detail": "Импорт уже запущен"}

    _history_import_state = {
        "running": True, "done": False, "created": 0, "skipped": 0,
        "errors": 0, "chunk": 0, "total_chunks": 0, "log": []
    }

    import threading
    t = threading.Thread(target=_run_history_import_bg, daemon=True)
    t.start()
    return {"ok": True, "detail": "Импорт запущен в фоне"}


@app.get("/api/kaspi/import-history/status")
def kaspi_import_history_status(request: Request):
    """Статус фонового импорта истории"""
    user = _get_user_from_session(request)
    if not user or not _is_staff(user):
        raise HTTPException(status_code=403)
    return dict(_history_import_state)


@app.post("/api/kaspi/import-history/stop")
def kaspi_import_history_stop(request: Request):
    """Остановить импорт"""
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)
    _history_import_state["running"] = False
    return {"ok": True}


@app.get("/api/admin/forecast")
def stock_forecast(db: Session = Depends(get_db)):
    """Прогноз: когда закончится товар на основе движений за последние 30 дней"""
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=30)

    # Продажи за 30 дней по товарам
    sales = db.query(_M.product_id, func.sum(-_M.quantity).label("sold")) \
        .filter(_M.move_type.in_(["sale", "kaspi_sale"]), _M.created_at >= cutoff) \
        .group_by(_M.product_id).all()

    sales_map = {s.product_id: float(s.sold) for s in sales}

    # Текущие остатки
    stocks = db.query(_M.product_id, func.sum(_M.quantity).label("stock")) \
        .group_by(_M.product_id).all()
    stock_map = {s.product_id: max(float(s.stock), 0) for s in stocks}

    products = db.query(_P).filter(_P.category == "Kaspi").all()

    result = []
    for p in products:
        stock = stock_map.get(p.id, 0)
        sold_30d = sales_map.get(p.id, 0)
        daily_rate = sold_30d / 30 if sold_30d > 0 else 0

        if daily_rate > 0:
            days_left = stock / daily_rate
            status = "critical" if days_left < 7 else ("warning" if days_left < 14 else "ok")
        else:
            days_left = None
            status = "no_sales"

        result.append({
            "id": p.id,
            "name": p.name,
            "brand": p.brand or "",
            "stock": stock,
            "sold_30d": sold_30d,
            "daily_rate": round(daily_rate, 2),
            "days_left": round(days_left, 1) if days_left is not None else None,
            "status": status,
        })

    # Сортировка: сначала критичные
    order = {"critical": 0, "warning": 1, "ok": 2, "no_sales": 3}
    result.sort(key=lambda x: (order[x["status"]], x["days_left"] if x["days_left"] is not None else 9999))
    return result


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


@app.get("/api/products/export/xlsx")
def products_export_xlsx(db: Session = Depends(get_db), ids: str = None):
    """Экспорт остатков в Excel (формат загрузки Kaspi)"""
    from fastapi.responses import StreamingResponse
    from sqlalchemy import func
    import openpyxl, io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from database import Product, Movement

    q = (
        db.query(Product, func.coalesce(func.sum(Movement.quantity), 0).label("stock"))
        .outerjoin(Movement, Movement.product_id == Product.id)
        .group_by(Product.id)
        .order_by(Product.name)
    )
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            q = q.filter(Product.id.in_(id_list))
    rows = q.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Остатки"

    # Стили
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="111827")
    center = Alignment(horizontal="center", vertical="center")
    thin = Border(
        left=Side(style="thin", color="E5E7EB"),
        right=Side(style="thin", color="E5E7EB"),
        top=Side(style="thin", color="E5E7EB"),
        bottom=Side(style="thin", color="E5E7EB"),
    )

    headers = ["SKU (Kaspi)", "Артикул (внутр.)", "Название товара", "Бренд",
               "Категория", "Цена (₸)", "Закуп (₸)", "Маржа %", "Остаток", "Мин. остаток", "Ед. изм.", "Поставщик"]
    col_widths = [28, 18, 50, 15, 20, 12, 12, 10, 12, 14, 10, 30]

    for ci, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(1, ci, h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin
        ws.column_dimensions[cell.column_letter].width = w

    ws.row_dimensions[1].height = 22

    low_fill = PatternFill("solid", fgColor="FEE2E2")
    ok_fill  = PatternFill("solid", fgColor="DCFCE7")

    for ri, (p, stock) in enumerate(rows, 2):
        stock = int(stock)
        values = [
            p.kaspi_sku or "",
            p.kaspi_sku or "",
            p.name or "",
            p.brand or "",
            p.category or "",
            p.price or "",
            p.cost_price or "",
            round((p.price - p.cost_price) / p.price * 100) if p.price and p.cost_price and p.price > 0 else "",
            stock,
            p.min_stock or 0,
            p.unit or "шт",
            p.supplier or "",
        ]
        for ci, val in enumerate(values, 1):
            cell = ws.cell(ri, ci, val)
            cell.alignment = Alignment(vertical="center")
            cell.border = thin

        # Подсветить строку если остаток низкий
        if stock <= (p.min_stock or 5):
            for ci in range(1, len(headers)+1):
                ws.cell(ri, ci).fill = low_fill
        ws.row_dimensions[ri].height = 18

    # Заморозить шапку
    ws.freeze_panes = "A2"

    # Второй лист — только для загрузки в Kaspi (формат active.xlsx)
    ws2 = wb.create_sheet("Для Kaspi")
    kaspi_headers = ["SKU", "model", "brand", "price", "PP1", "PP2", "PP3", "PP4", "PP5", "preorder"]
    for ci, h in enumerate(kaspi_headers, 1):
        cell = ws2.cell(1, ci, h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F3F4F6")

    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 50
    ws2.column_dimensions["C"].width = 15

    for ri, (p, stock) in enumerate(rows, 2):
        if not p.kaspi_sku:
            continue
        ws2.cell(ri, 1, p.kaspi_sku)
        ws2.cell(ri, 2, p.name)
        ws2.cell(ri, 3, p.brand or "")
        ws2.cell(ri, 4, p.price or "")
        ws2.cell(ri, 5, max(0, int(stock)))  # PP1 = основной склад
        for c in range(6, 11):
            ws2.cell(ri, c, "no")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=stock_export.xlsx"}
    )


def _filter_orders_by_date(rows, date_from: str | None, date_to: str | None):
    """Фильтр строк KaspiOrder по дате.

    Логика:
    - Активные заказы (в процессе: NEW/DELIVERY/PICKUP/KASPI_DELIVERY/APPROVED/SIGN_REQUIRED)
      показываем ВСЕГДА, независимо от фильтра — они сейчас в работе.
    - Выданные (ARCHIVE/Выдан) — фильтруем по дате выдачи (status_date).
    - Отменённые/Возвраты — фильтруем по дате создания (order_date).
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
        # Активные заказы всегда показываем
        if state in ACTIVE_STATES:
            result.append(r)
            continue
        # Для выданных — дата выдачи, для остальных — дата создания заказа
        if state in ARCHIVE_STATES and getattr(r, "status_date", None):
            date_str = r.status_date
        else:
            date_str = getattr(r, "order_date", None)
        d = _parse_order_date(date_str)
        if d is None:
            continue
        if df and d < df:
            continue
        if dt and d > dt:
            continue
        result.append(r)
    return result


@app.get("/api/admin/orders-debug")
def orders_debug(request: Request, db: Session = Depends(get_db)):
    """Статистика заказов по статусам — для диагностики"""
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    from database import KaspiOrder
    from sqlalchemy import func
    rows = db.query(KaspiOrder.state, func.count(KaspiOrder.id), func.sum(KaspiOrder.total))\
        .group_by(KaspiOrder.state).all()
    result = []
    for state, cnt, total in sorted(rows, key=lambda x: x[1], reverse=True):
        result.append({"state": state, "count": cnt, "total": int(total or 0)})
    grand_total = sum(r["total"] for r in result)
    completed_total = sum(r["total"] for r in result if r["state"] in ("ARCHIVE", "Выдан"))
    return {"by_state": result, "grand_total": grand_total, "completed_total": completed_total}


@app.get("/api/analytics/overview")
def analytics_overview(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Общая статистика по заказам"""
    from database import KaspiOrder
    from sqlalchemy import func

    COMPLETED_STATES = {"Выдан", "ARCHIVE"}
    CANCELLED_STATES = {"Отменен", "CANCELLED", "Возврат"}

    # Для аналитики берём только завершённые и отменённые (без активных)
    all_rows = db.query(KaspiOrder).filter(
        KaspiOrder.state.in_(list(COMPLETED_STATES) + list(CANCELLED_STATES))
    ).all()
    rows = _filter_orders_by_date(all_rows, date_from, date_to)

    total_orders = len(rows)
    completed = sum(1 for r in rows if r.state in COMPLETED_STATES)
    cancelled = sum(1 for r in rows if r.state in CANCELLED_STATES)

    # Выручка только по завершённым заказам
    total_revenue = sum(r.total or 0 for r in rows if r.state in COMPLETED_STATES)
    avg_order = int(total_revenue / completed) if completed else 0

    # P&L: комиссия и налог из настроек
    from database import SiteSetting, Product as _P
    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    kaspi_commission_pct = float(settings.get("kaspi_commission_pct", "8"))
    tax_pct              = float(settings.get("tax_pct", "4"))

    commission  = int(total_revenue * kaspi_commission_pct / 100)
    tax         = int(total_revenue * tax_pct / 100)

    # Себестоимость: сумма (qty * cost_price) по завершённым заказам
    cost_total = 0
    for r in rows:
        if r.state not in COMPLETED_STATES:
            continue
        if r.product_id and r.quantity:
            p_obj = db.query(_P).filter(_P.id == r.product_id).first()
            if p_obj and p_obj.cost_price:
                cost_total += int(r.quantity) * int(p_obj.cost_price)

    gross_profit = total_revenue - commission - tax - cost_total

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order": avg_order,
        "completed": completed,
        "cancelled": cancelled,
        "conversion_rate": round(completed / total_orders * 100, 1) if total_orders else 0,
        "commission": commission,
        "commission_pct": kaspi_commission_pct,
        "tax": tax,
        "tax_pct": tax_pct,
        "cost_total": cost_total,
        "gross_profit": gross_profit,
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

    # Если выбран один день — показываем по часам
    single_day = date_from and date_to and date_from == date_to

    by_period: dict = defaultdict(lambda: {"revenue": 0, "orders": 0})
    for r in rows:
        d = _parse_order_date(r.order_date)
        if not d:
            continue
        if single_day:
            key = d.strftime("%Y-%m-%d %H:00")
        elif period == "day":
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
    """Прогноз спроса: тренд + сезонность + волатильность + дни до нуля"""
    import numpy as np
    from database import KaspiOrder, Product as _P, Movement as _M
    from collections import defaultdict
    from sqlalchemy import func
    import datetime

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.product_name.isnot(None)).all()
    rows = _filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    # Все данные за всё время (для сезонности нужны прошлый год)
    all_rows_full = [r for r in all_rows if r.state in COMPLETED]

    # Текущие остатки
    stocks_q = (
        db.query(_P.name, func.coalesce(func.sum(_M.quantity), 0))
        .outerjoin(_M, _M.product_id == _P.id)
        .group_by(_P.name)
        .all()
    )
    current_stock = {name: int(s) for name, s in stocks_q}

    # Сгруппировать по товару → по неделе
    by_product: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = _parse_order_date(r.order_date)
        if not d:
            continue
        week = d.strftime("%Y-W%W")
        by_product[r.product_name][week] += r.quantity or 1

    # Полная история по неделям (для сезонности)
    full_by_product: dict = defaultdict(lambda: defaultdict(int))
    for r in all_rows_full:
        d = _parse_order_date(r.order_date)
        if not d:
            continue
        week = d.strftime("%Y-W%W")
        full_by_product[r.product_name][week] += r.quantity or 1

    # Топ-20 по суммарному количеству
    top_names = sorted(by_product.keys(), key=lambda n: sum(by_product[n].values()), reverse=True)[:20]

    now = datetime.date.today()
    cur_week_num = int(now.strftime("%W"))

    results = []
    for name in top_names:
        week_data = by_product[name]
        weeks = sorted(week_data.keys())
        if len(weeks) < 3:
            continue
        y = np.array([week_data[w] for w in weeks], dtype=float)
        x = np.arange(len(y))

        # ── Линейный тренд ──────────────────────────────────────
        coeffs = np.polyfit(x, y, 1)
        trend = float(coeffs[0])
        next_x = len(y) + np.arange(4)
        forecast_linear = [max(0, float(np.polyval(coeffs, xi))) for xi in next_x]

        # ── Скользящее среднее 4 нед ─────────────────────────────
        ma4 = float(np.mean(y[-4:])) if len(y) >= 4 else float(np.mean(y))

        # ── Сезонность — сравниваем с теми же неделями год назад ──
        full_data = full_by_product[name]
        seasonal_factor = 1.0
        seasonal_weeks_found = 0
        for offset in range(4):
            w_num = (cur_week_num + offset) % 52
            key_this = f"{now.year}-W{w_num:02d}"
            key_prev = f"{now.year - 1}-W{w_num:02d}"
            val_prev = full_data.get(key_prev, 0)
            val_this = full_data.get(key_this, 0)
            if val_prev > 0:
                seasonal_weeks_found += 1
                seasonal_factor += val_this / val_prev
        if seasonal_weeks_found > 0:
            seasonal_factor = seasonal_factor / seasonal_weeks_found
            seasonal_factor = max(0.3, min(3.0, seasonal_factor))  # ограничиваем

        # ── Итоговый прогноз = тренд × сезонность ───────────────
        forecast = [max(0, round(v * seasonal_factor)) for v in forecast_linear]

        # ── Волатильность (CV = std/mean) ────────────────────────
        mean_y = float(np.mean(y)) if len(y) > 0 else 1
        std_y = float(np.std(y)) if len(y) > 1 else 0
        cv = round(std_y / mean_y, 2) if mean_y > 0 else 0  # 0=стабильно, >1=хаос

        # ── Дни до нуля ─────────────────────────────────────────
        stock = current_stock.get(name, 0)
        weekly_rate = ma4 * seasonal_factor if ma4 > 0 else 0
        days_left = round((stock / weekly_rate) * 7) if weekly_rate > 0 else None

        # ── Рекомендация ─────────────────────────────────────────
        if days_left is not None and days_left < 14:
            recommendation = "urgent"   # срочно заказать
        elif days_left is not None and days_left < 30:
            recommendation = "order"    # заказать
        elif trend < -0.2 and cv < 0.5:
            recommendation = "watch"    # спад — следить
        else:
            recommendation = "ok"

        results.append({
            "name": name,
            "weeks": weeks[-8:],
            "history": [int(week_data[w]) for w in weeks[-8:]],
            "forecast_4w": forecast,
            "trend": round(trend, 2),
            "trend_dir": "up" if trend > 0.1 else ("down" if trend < -0.1 else "flat"),
            "ma4": round(ma4, 1),
            "seasonal_factor": round(seasonal_factor, 2),
            "volatility": cv,
            "volatility_label": "стабильный" if cv < 0.4 else ("умеренный" if cv < 0.8 else "непредсказуемый"),
            "stock": stock,
            "days_left": days_left,
            "recommendation": recommendation,
        })

    results.sort(key=lambda x: (
        0 if x["recommendation"] == "urgent" else
        1 if x["recommendation"] == "order" else
        2 if x["recommendation"] == "watch" else 3,
        -sum(x["history"])
    ))
    return {"products": results}




@app.post("/api/kaspi/sync-products")
def sync_kaspi_products_endpoint():

    """Синхронизировать товары из Kaspi в склад"""
    token = _get_integration("kaspi_api_key", "KASPI_TOKEN")
    shop_id = _get_integration("kaspi_shop_id", "KASPI_SHOP_ID")
    if not token or not shop_id:
        raise HTTPException(status_code=400, detail="KASPI_TOKEN и KASPI_SHOP_ID не заданы")
    result = kaspi_module.sync_kaspi_products()
    return {"message": result}


@app.get("/admin/kaspi", response_class=HTMLResponse)
def kaspi_page():
    with open("static/kaspi.html") as f:
        return f.read()


@app.get("/admin/analytics", response_class=HTMLResponse)
def analytics_page():
    with open("static/analytics.html") as f:
        return f.read()


@app.get("/admin/settings", response_class=HTMLResponse)
def settings_page():
    with open("static/settings.html") as f:
        return f.read()


# Редиректы со старых путей
@app.get("/kaspi", response_class=HTMLResponse)
def kaspi_redirect():
    return RedirectResponse("/admin/kaspi", status_code=301)

@app.get("/analytics", response_class=HTMLResponse)
def analytics_redirect():
    return RedirectResponse("/admin/analytics", status_code=301)

@app.get("/history", response_class=HTMLResponse)
def history_redirect():
    return RedirectResponse("/admin/history", status_code=301)

@app.get("/scanner", response_class=HTMLResponse)
def scanner_redirect():
    return RedirectResponse("/admin/scanner", status_code=301)


# ─── Shop Orders ─────────────────────────────────────────────
class ShopOrderCreate(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None
    comment: Optional[str] = None
    items: list  # [{product_id, qty}]


@app.post("/api/shop/orders")
def create_shop_order(data: ShopOrderCreate, request: Request, db: Session = Depends(get_db)):
    from database import ShopOrder, Product as _P

    if not data.items:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    # Собрать позиции с ценами
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

    user = _get_user_from_session(request)
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

    # Уведомление в Telegram
    try:
        _notify_new_shop_order(order, order_items)
    except Exception:
        pass

    return {"ok": True, "order_id": order.id, "total": total}


def _notify_new_shop_order(order, items):
    import requests as req_lib
    bot_token = _get_integration("tg_bot_token", "TELEGRAM_BOT_TOKEN")
    chat_id = _get_integration("tg_chat_id", "TELEGRAM_CHAT_ID")
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


@app.get("/api/admin/shop-orders")
def list_shop_orders(request: Request, status: Optional[str] = None, db: Session = Depends(get_db)):
    from database import ShopOrder
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
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


@app.patch("/api/admin/shop-orders/{order_id}")
def update_shop_order(order_id: int, data: dict, request: Request, db: Session = Depends(get_db)):
    from database import ShopOrder
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    o = db.query(ShopOrder).filter(ShopOrder.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404)
    if "status" in data:
        o.status = data["status"]
    db.commit()
    return {"ok": True, "status": o.status}


@app.get("/admin/shop-orders", response_class=HTMLResponse)
def shop_orders_page():
    with open("static/shop_orders.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/purchases/list")
def purchases_list(db: Session = Depends(get_db)):
    """Список товаров с остатком ниже минимума, сгруппированных по поставщику."""
    all_stocks = crud.get_all_stocks(db)
    result = []
    for s in all_stocks:
        p = s["product"]
        stock = s["stock"]
        min_s = p.min_stock or 0
        if stock <= min_s:
            result.append({
                "id": p.id,
                "name": p.name,
                "sku": p.kaspi_sku or "",
                "supplier": p.supplier or "Не указан",
                "brand": p.brand or "",
                "stock": stock,
                "min_stock": min_s,
                "unit": p.unit or "шт",
                "need": max(1, min_s - stock + max(min_s, 5)),
            })
    # Сортируем: сначала по поставщику, потом по бренду
    result.sort(key=lambda x: (x["supplier"], x["brand"], x["name"]))
    return {"items": result, "total": len(result)}


@app.post("/api/purchases/send-tg")
def purchases_send_tg(db: Session = Depends(get_db)):
    """Отправить список закупок в Telegram."""
    data = purchases_list(db)
    items = data["items"]
    if not items:
        return {"ok": True, "message": "Нет товаров для закупки"}
    # Группируем по поставщику
    from collections import defaultdict
    by_supplier = defaultdict(list)
    for item in items:
        by_supplier[item["supplier"]].append(item)
    lines = ["📦 <b>Список закупок</b>", f"Итого позиций: {len(items)}", ""]
    for supplier, sup_items in by_supplier.items():
        lines.append(f"🏭 <b>{supplier}</b>")
        for it in sup_items:
            lines.append(f"  • {it['name']} ({it['sku']}) — осталось {it['stock']} {it['unit']}, заказать ~{it['need']}")
        lines.append("")
    _send_tg_notification("\n".join(lines))
    return {"ok": True, "message": f"Отправлено {len(items)} позиций"}


@app.post("/api/import-products")
def import_products(db: Session = Depends(get_db)):
    """Одноразовый импорт товаров из export_products.json"""
    import os
    path = os.path.join(os.path.dirname(__file__), '_archive', 'export_products.json')
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="export_products.json не найден")
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    from database import Product as _P
    added = skipped = 0
    for p in data:
        sku = p['sku'].upper()
        if False:  # sku field removed
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


@app.post("/api/kaspi/import-xml-products")
async def kaspi_import_xml_products(
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Импорт товаров из Kaspi каталога XML — через файл или ACTIVE.xml на сервере"""
    import xml.etree.ElementTree as ET
    from database import Product as _P
    import re

    if file:
        content = await file.read()
        original_name = file.filename or "ACTIVE.xml"
        root = ET.fromstring(content)
    else:
        path = os.path.join(os.path.dirname(__file__), 'ACTIVE.xml')
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="ACTIVE.xml не найден. Загрузите файл.")
        with open(path, "rb") as f:
            content = f.read()
        original_name = "ACTIVE.xml"
        root = ET.fromstring(content)
    # убираем namespace из тегов
    ns = "kaspiShopping"

    def tag(el):
        return re.sub(r'\{[^}]+\}', '', el.tag)

    def find_text(el, name):
        for child in el:
            if tag(child) == name:
                return (child.text or "").strip()
        return ""

    # Удаляем только товары категории "Kaspi" и их движения (накладные не трогаем)
    from database import Movement
    old_products = db.query(_P).filter(_P.category == "Kaspi").all()
    old_ids = [p.id for p in old_products]
    if old_ids:
        db.query(Movement).filter(Movement.product_id.in_(old_ids)).delete(synchronize_session=False)
        db.query(_P).filter(_P.id.in_(old_ids)).delete(synchronize_session=False)
        db.commit()

    added = 0

    offers_el = None
    for child in root:
        if tag(child) == "offers":
            offers_el = child
            break

    if offers_el is None:
        raise HTTPException(status_code=400, detail="Тег <offers> не найден в XML")

    for offer in offers_el:
        if tag(offer) != "offer":
            continue
        kaspi_sku = offer.attrib.get("sku", "").strip()
        if not kaspi_sku:
            continue

        model = find_text(offer, "model")
        brand = find_text(offer, "brand")

        price = None
        for child in offer:
            if tag(child) == "cityprices":
                for cp in child:
                    if tag(cp) == "cityprice":
                        try:
                            price = int(cp.text.strip())
                        except Exception:
                            pass
                        break
                break

        stock_count = 0
        for child in offer:
            if tag(child) == "availabilities":
                for av in child:
                    if tag(av) == "availability":
                        try:
                            stock_count = int(float(av.attrib.get("stockCount", 0)))
                        except Exception:
                            pass
                        break
                break

        new_p = _P(
            name=model or kaspi_sku,
            sku=None,
            kaspi_sku=kaspi_sku,
            brand=brand or None,
            price=price,
            category="Kaspi",
            unit="шт",
            min_stock=1,
        )
        db.add(new_p)
        db.flush()

        if stock_count > 0:
            crud.set_initial_stock(new_p.id, stock_count, db)

        added += 1

    db.commit()
    _save_upload(content, original_name, "kaspi_active", added, db)
    return {"deleted": len(old_ids), "added": added}


@app.post("/api/kaspi/import-archive")
async def kaspi_import_archive(
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Импорт архивных товаров — добавляет только новые, существующие не трогает"""
    import xml.etree.ElementTree as ET
    from database import Product as _P
    import re

    if file:
        content = await file.read()
        original_name = file.filename or "ARCHIVE.xml"
        root = ET.fromstring(content)
    else:
        path = os.path.join(os.path.dirname(__file__), 'ARCHIVE.xml')
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="ARCHIVE.xml не найден. Загрузите файл.")
        with open(path, "rb") as f:
            content = f.read()
        original_name = "ARCHIVE.xml"
        root = ET.fromstring(content)

    def tag(el):
        return re.sub(r'\{[^}]+\}', '', el.tag)

    def find_text(el, name):
        for child in el:
            if tag(child) == name:
                return (child.text or "").strip()
        return ""

    # Индекс существующих kaspi_sku
    existing_skus = {p.kaspi_sku for p in db.query(_P).filter(_P.kaspi_sku != None).all()}

    offers_el = None
    for child in root:
        if tag(child) == "offers":
            offers_el = child
            break

    if offers_el is None:
        raise HTTPException(status_code=400, detail="Тег <offers> не найден в XML")

    added = 0
    skipped = 0

    for offer in offers_el:
        if tag(offer) != "offer":
            continue
        kaspi_sku = offer.attrib.get("sku", "").strip()
        if not kaspi_sku:
            continue

        # Пропускаем если уже есть
        if kaspi_sku in existing_skus:
            skipped += 1
            continue

        model = find_text(offer, "model")
        brand = find_text(offer, "brand")

        price = None
        for child in offer:
            if tag(child) == "cityprices":
                for cp in child:
                    if tag(cp) == "cityprice":
                        try:
                            price = int(cp.text.strip())
                        except Exception:
                            pass
                        break
                break

        new_p = _P(
            name=model or kaspi_sku,
            sku=sku,
            kaspi_sku=kaspi_sku,
            brand=brand or None,
            price=price,
            category="Kaspi",
            unit="шт",
            min_stock=1,
        )
        db.add(new_p)
        existing_skus.add(kaspi_sku)
        added += 1

    db.commit()
    _save_upload(content, original_name, "kaspi_archive", added, db)
    return {"added": added, "skipped": skipped}


# ── Слияние накладных и Kaspi товаров ────────────────────────────────────────

@app.get("/api/merge-preview")
def merge_preview(db: Session = Depends(get_db)):
    import re
    from database import Product as _P, PriceListItem
    kaspi_products = db.query(_P).filter(_P.category == "Kaspi").all()
    ref_items = db.query(PriceListItem).all()

    STOPWORDS = {"и","в","на","с","для","из","по","шт","мл","л","кг","г","см","мм","м","гр","х","x","the","of","for","pcs"}

    def words(text):
        return {w for w in re.split(r'[\s\-_/,.()\[\]]+', (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}

    def match_score(a_words, b_words):
        if not a_words or not b_words:
            return 0
        common = a_words & b_words
        return len(common) / max(len(a_words), len(b_words))

    # Предвычислим слова для Kaspi
    kaspi_words = {kp.id: words(kp.name) for kp in kaspi_products}

    # Извлекает числовое количество из названия (напр. "25 кг", "5л", "1000мл" → число в граммах/мл для сравнения)
    def extract_qty(text):
        """Возвращает (число, единица) или None"""
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*(кг|г|гр|kg|g|л|л\.|мл|ml|l|м|mm|см|cm|шт|pcs|pc)', (text or "").lower())
        if not m:
            return None
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        # Нормализуем к базовой единице
        if unit in ("кг", "kg"):
            return (val * 1000, "g")
        if unit in ("г", "гр", "g"):
            return (val, "g")
        if unit in ("л", "л.", "l"):
            return (val * 1000, "ml")
        if unit in ("мл", "ml"):
            return (val, "ml")
        return (val, unit)

    def qty_penalty(name_a, name_b):
        """1.0 если количество совпадает или не указано, 0.05 если явно разное"""
        qa = extract_qty(name_a)
        qb = extract_qty(name_b)
        if qa is None or qb is None:
            return 1.0
        if qa[1] != qb[1]:
            return 1.0  # разные единицы — не сравниваем
        return 1.0 if abs(qa[0] - qb[0]) < 0.01 else 0.05

    # Индексы для Kaspi товаров
    kaspi_by_sku = {kp.kaspi_sku.upper(): kp for kp in kaspi_products if kp.kaspi_sku}
    kaspi_by_barcode = {kp.barcode.upper(): kp for kp in kaspi_products if kp.barcode}
    kaspi_by_article = {kp.kaspi_article.upper(): kp for kp in kaspi_products if kp.kaspi_article}
    ref_by_id = {ri.id: ri for ri in ref_items}

    # Сначала берём сохранённые вручную пары (linked_ref_id)
    candidates = []  # (score, match_type, kp, ri)
    for kp in kaspi_products:
        if kp.linked_ref_id and kp.linked_ref_id in ref_by_id:
            ri = ref_by_id[kp.linked_ref_id]
            candidates.append((2.0, "linked", kp, ri))  # score 2.0 = всегда побеждает

    for ri in ref_items:
        ri_article = (ri.article or "").upper()

        # Точное совпадение по артикулу/sku/barcode
        matched_kp = None
        if ri_article:
            matched_kp = (kaspi_by_article.get(ri_article)
                          or kaspi_by_sku.get(ri_article)
                          or kaspi_by_barcode.get(ri_article))
        # Артикул встречается в названии Kaspi
        if not matched_kp and ri_article and len(ri_article) >= 5:
            for kp in kaspi_products:
                if ri_article in kp.name.upper():
                    matched_kp = kp
                    break

        if matched_kp:
            penalty = qty_penalty(ri.name, matched_kp.name)
            candidates.append((1.0 * penalty, "sku", matched_kp, ri))

    # Нечёткий для ВСЕХ справочник-элементов (жадный сортировщик сам выберет лучшее)
    for ri in ref_items:
        ri_words = words(ri.name)
        for kp in kaspi_products:
            score = match_score(ri_words, kaspi_words[kp.id])
            if score >= 0.4:
                penalty = qty_penalty(ri.name, kp.name)
                candidates.append((round(score * penalty, 3), "fuzzy", kp, ri))

    # Сортируем по убыванию оценки
    candidates.sort(key=lambda c: -c[0])

    # Жадное 1-к-1 присвоение
    used_kaspi = set()
    used_ref = set()
    pairs = []
    for score, match_type, kp, ri in candidates:
        if kp.id in used_kaspi or ri.id in used_ref:
            continue
        used_kaspi.add(kp.id)
        used_ref.add(ri.id)
        pairs.append({
            "kaspi_id": kp.id,
            "kaspi_name": kp.name,
            "kaspi_sku": kp.kaspi_sku,
            "kaspi_price": kp.price,
            "other_id": ri.id,
            "other_name": ri.name,
            "other_sku": ri.article or "",
            "other_supplier": ri.supplier or "",
            "other_cost_price": ri.cost_price,
            "match_type": match_type,
            "score": score,
        })

    # Без пары — справочник items которые не попали ни в одну пару
    matched_ref_ids = used_ref
    unmatched_ref = [
        {"id": ri.id, "name": ri.name, "sku": ri.article or "",
         "cost_price": ri.cost_price, "supplier": ri.supplier or ""}
        for ri in ref_items if ri.id not in matched_ref_ids
    ]

    # Сортируем: сначала linked, потом sku, потом fuzzy по убыванию score
    pairs.sort(key=lambda p: (-int(p["match_type"] == "linked"), -int(p["match_type"] == "sku"), -p["score"]))

    kaspi_list = [{"id": kp.id, "name": kp.name, "kaspi_sku": kp.kaspi_sku,
                   "price": kp.price, "brand": kp.brand} for kp in kaspi_products]
    other_list = [{"id": ri.id, "name": ri.name, "sku": ri.article or "",
                   "cost_price": ri.cost_price, "supplier": ri.supplier or ""} for ri in ref_items]

    # Синхронизированные — Kaspi товары у которых уже есть cost_price или supplier
    synced = [{"id": kp.id, "name": kp.name, "kaspi_sku": kp.kaspi_sku,
               "price": kp.price, "cost_price": kp.cost_price, "supplier": kp.supplier or "",
               "supplier_article": kp.supplier_article or ""}
              for kp in kaspi_products if kp.cost_price or kp.supplier]

    return {
        "pairs": pairs,
        "unmatched_other": unmatched_ref,
        "kaspi_list": kaspi_list,
        "other_list": other_list,
        "total_kaspi": len(kaspi_products),
        "total_other": len(ref_items),
        "synced": synced,
        "total_synced": len(synced),
    }


@app.post("/api/merge-confirm")
def merge_confirm(body: dict, db: Session = Depends(get_db)):
    """body: {"pairs": [{"kaspi_id": X, "other_id": Y}], "fields": ["name","cost_price","supplier","supplier_article"]}"""
    from database import Product as _P, PriceListItem
    selected = body.get("pairs", [])
    fields = set(body.get("fields", ["cost_price", "supplier", "supplier_article"]))

    def clean_article(val):
        if not val:
            return None
        for prefix in ("KSP_", "PL-"):
            if val.upper().startswith(prefix):
                return None
        return val

    force = body.get("force", False)  # перезаписывать даже заполненные поля
    merged = 0
    for pair in selected:
        kaspi_p = db.query(_P).filter(_P.id == pair["kaspi_id"]).first()
        ref_item = db.query(PriceListItem).filter(PriceListItem.id == pair["other_id"]).first()
        if not kaspi_p or not ref_item:
            continue

        if "name" in fields and ref_item.name:
            if force or not kaspi_p.name:
                kaspi_p.name = ref_item.name
        if "cost_price" in fields and ref_item.cost_price:
            if force or not kaspi_p.cost_price:
                kaspi_p.cost_price = ref_item.cost_price
        if "supplier" in fields and ref_item.supplier:
            if force or not kaspi_p.supplier:
                kaspi_p.supplier = ref_item.supplier
        if "supplier_article" in fields:
            article_val = clean_article(ref_item.article)
            if article_val and (force or not kaspi_p.supplier_article):
                kaspi_p.supplier_article = article_val

        kaspi_p.linked_ref_id = ref_item.id  # сохраняем привязку
        merged += 1
    db.commit()
    return {"merged": merged}


@app.post("/api/fill-brands")
def fill_brands(db: Session = Depends(get_db)):
    """Авто-заполнение бренда по названию товара для всех у кого бренд пустой."""
    from database import Product as _P
    filled = 0
    for p in db.query(_P).filter(_P.brand == None).all():
        brand = crud.detect_brand(p.name)
        if brand:
            p.brand = brand
            filled += 1
    db.commit()
    return {"filled": filled}


@app.get("/merge")
def merge_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/merge.html")


@app.delete("/api/reset-nakladnye")
def reset_nakladnye(db: Session = Depends(get_db)):
    """Удаляет все товары категории Накладные и их движения"""
    from database import Product as _P, Movement
    products = db.query(_P).filter(_P.category == "Накладные").all()
    ids = [p.id for p in products]
    if ids:
        db.query(Movement).filter(Movement.product_id.in_(ids)).delete(synchronize_session=False)
        db.query(_P).filter(_P.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": len(ids)}


@app.get("/import")
def import_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/import.html")


# ── Справочник накладных (price_list_items) ───────────────────────────────────

@app.post("/api/pricelist/import")
async def pricelist_import(
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Импортирует XML в справочник накладных. Старые записи из того же файла заменяются."""
    import xml.etree.ElementTree as ET
    from database import PriceListItem

    if not file:
        raise HTTPException(status_code=400, detail="Файл не передан")

    content = await file.read()
    filename = file.filename or "unknown.xml"

    try:
        root = ET.fromstring(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга XML: {e}")

    товары = root.find("Товары")
    if товары is None:
        raise HTTPException(status_code=400, detail="Нет тега <Товары> в XML")

    # Загружаем существующие записи из этого файла для upsert
    existing = {
        (i.article or "", i.supplier or ""): i
        for i in db.query(PriceListItem).filter(PriceListItem.source_file == filename).all()
    }

    created = 0
    updated = 0
    seen_keys = set()

    for товар in товары.findall("Товар"):
        name_el    = товар.find("Наименование")
        price_el   = товар.find("ЦенаЗакупки")
        supplier_el= товар.find("Поставщик")
        article_el = товар.find("Артикул")
        unit_el    = товар.find("ЕдИзм")

        name     = (name_el.text or "").strip()    if name_el    is not None else ""
        supplier = (supplier_el.text or "").strip() if supplier_el is not None else ""
        article  = (article_el.text or "").strip() if article_el is not None else ""
        unit     = (unit_el.text or "шт").strip()  if unit_el    is not None else "шт"
        cost_raw = (price_el.text or "0").strip()  if price_el   is not None else "0"

        if not name:
            continue
        try:
            cost_price = int(float(cost_raw))
        except Exception:
            cost_price = None

        key = (article, supplier)
        if key in seen_keys:
            continue  # пропускаем дубли внутри файла
        seen_keys.add(key)

        if key in existing:
            # Обновляем существующую запись — снимаем метку "новый"
            item = existing[key]
            item.name = name
            item.cost_price = cost_price
            item.unit = unit
            item.is_new = False
            updated += 1
        else:
            db.add(PriceListItem(
                name=name,
                article=article or None,
                supplier=supplier or None,
                cost_price=cost_price,
                unit=unit,
                source_file=filename,
                is_new=True,
            ))
            created += 1

    # Удаляем записи из файла которых больше нет в XML
    removed = 0
    for key, item in existing.items():
        if key not in seen_keys:
            db.delete(item)
            removed += 1

    db.commit()
    _save_upload(content, filename, "pricelist_ref", created + updated, db)
    return {"created": created, "updated": updated, "removed": removed, "file": filename}


@app.get("/api/pricelist/search")
def pricelist_search(q: str = "", supplier: str = "", limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """Поиск по справочнику накладных."""
    from database import PriceListItem
    from sqlalchemy import or_
    query = db.query(PriceListItem)
    if q:
        from sqlalchemy import and_
        words = [w for w in q.strip().split() if w]
        for w in words:
            like = f"%{w}%"
            query = query.filter(or_(
                PriceListItem.name.ilike(like),
                PriceListItem.article.ilike(like),
            ))
    if supplier:
        query = query.filter(PriceListItem.supplier == supplier)
    total = query.count()
    items = query.order_by(PriceListItem.name).offset(offset).limit(limit).all()
    return {
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "article": i.article or "",
                "supplier": i.supplier or "",
                "cost_price": i.cost_price,
                "unit": i.unit,
                "source_file": i.source_file or "",
                "is_new": bool(i.is_new),
            }
            for i in items
        ],
        "total": total,
    }


@app.get("/api/pricelist/suppliers")
def pricelist_suppliers(db: Session = Depends(get_db)):
    from database import PriceListItem
    rows = db.query(PriceListItem.supplier).distinct().filter(PriceListItem.supplier != None).all()
    return {"suppliers": sorted([r[0] for r in rows if r[0]])}


@app.get("/api/pricelist/price-check")
def pricelist_price_check(db: Session = Depends(get_db)):
    """Сравнение закупочных цен справочника с текущими ценами Kaspi-карточек."""
    from database import PriceListItem, Product as _P
    ref_items = db.query(PriceListItem).filter(PriceListItem.cost_price != None).all()
    kaspi_products = db.query(_P).filter(_P.category == "Kaspi").all()

    # Индексы для быстрого поиска
    kaspi_by_sku      = {p.kaspi_sku.upper(): p for p in kaspi_products if p.kaspi_sku}
    kaspi_by_article  = {p.kaspi_article.upper(): p for p in kaspi_products if p.kaspi_article}
    kaspi_by_barcode  = {p.barcode.upper(): p for p in kaspi_products if p.barcode}
    kaspi_by_sup_art  = {p.supplier_article.upper(): p for p in kaspi_products if p.supplier_article}

    rows = []
    for ri in ref_items:
        art = (ri.article or "").upper()
        kp = None
        if art:
            kp = kaspi_by_article.get(art) or kaspi_by_sku.get(art) or kaspi_by_barcode.get(art) or kaspi_by_sup_art.get(art)

        ref_price = ri.cost_price
        cur_price = kp.cost_price if kp else None
        if cur_price and ref_price:
            diff = round(ref_price - cur_price, 2)
            pct  = round((ref_price - cur_price) / cur_price * 100, 1) if cur_price else None
        else:
            diff = None
            pct  = None

        rows.append({
            "ref_id":      ri.id,
            "ref_name":    ri.name,
            "article":     ri.article or "",
            "supplier":    ri.supplier or "",
            "ref_price":   ref_price,
            "kaspi_id":    kp.id if kp else None,
            "kaspi_name":  kp.name if kp else None,
            "cur_price":   cur_price,
            "diff":        diff,
            "pct":         pct,
            "matched":     kp is not None,
        })

    # Сортируем: сначала несовпавшие, потом по убыванию абс. разницы
    rows.sort(key=lambda r: (r["matched"], -abs(r["diff"] or 0)))
    return {"rows": rows, "total": len(rows),
            "matched": sum(1 for r in rows if r["matched"]),
            "changed": sum(1 for r in rows if r["diff"] and r["diff"] != 0)}


@app.get("/api/pricelist/stats")
def pricelist_stats(db: Session = Depends(get_db)):
    from database import PriceListItem, UploadedFile
    from sqlalchemy import func
    total = db.query(PriceListItem).count()
    raw_file = db.query(PriceListItem.source_file, func.count()).group_by(PriceListItem.source_file).all()
    by_supplier = dict(db.query(PriceListItem.supplier, func.count()).group_by(PriceListItem.supplier).all())

    # Получаем даты загрузки из UploadedFile
    uploads = db.query(UploadedFile).filter(UploadedFile.file_type == "pricelist_ref").order_by(UploadedFile.uploaded_at.desc()).all()
    upload_dates = {}
    for u in uploads:
        if u.original_name not in upload_dates:
            upload_dates[u.original_name] = u.uploaded_at.strftime("%d.%m.%Y %H:%M") if u.uploaded_at else ""

    files = []
    for fname, cnt in raw_file:
        if fname:
            files.append({"name": fname, "count": cnt, "uploaded_at": upload_dates.get(fname, "")})

    return {"total": total, "files": files, "by_supplier": {k or "": v for k, v in by_supplier.items()}}


@app.delete("/api/pricelist/clear")
def pricelist_clear(source_file: str = "", db: Session = Depends(get_db)):
    from database import PriceListItem
    q = db.query(PriceListItem)
    if source_file:
        q = q.filter(PriceListItem.source_file == source_file)
    deleted = q.delete()
    db.commit()
    return {"deleted": deleted}


@app.get("/pricelist")
def pricelist_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/pricelist.html")


# ── Загруженные файлы ─────────────────────────────────────────────────────────

@app.get("/api/uploads")
def list_uploads(db: Session = Depends(get_db)):
    from database import UploadedFile
    files = db.query(UploadedFile).order_by(UploadedFile.uploaded_at.desc()).all()
    type_labels = {
        "kaspi_active":  "Kaspi ACTIVE",
        "kaspi_archive": "Kaspi ARCHIVE",
        "price_list":    "Прайс (накладные)",
        "pricelist_ref": "Справочник",
    }
    return [
        {
            "id": f.id,
            "original_name": f.original_name,
            "saved_name": f.saved_name,
            "file_type": f.file_type,
            "type_label": type_labels.get(f.file_type, f.file_type),
            "size_bytes": f.size_bytes,
            "records": f.records,
            "uploaded_at": f.uploaded_at.strftime("%d.%m.%Y %H:%M") if f.uploaded_at else "",
            "exists": os.path.exists(os.path.join(UPLOADS_DIR, f.saved_name)) if f.saved_name else False,
        }
        for f in files
    ]

@app.get("/api/uploads/{upload_id}/download")
def download_upload(upload_id: int, db: Session = Depends(get_db)):
    from database import UploadedFile
    from fastapi.responses import FileResponse
    f = db.query(UploadedFile).filter(UploadedFile.id == upload_id).first()
    if not f or not f.saved_name:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = os.path.join(UPLOADS_DIR, f.saved_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл удалён с диска")
    return FileResponse(path, filename=f.original_name, media_type="application/octet-stream")

@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: int, db: Session = Depends(get_db)):
    from database import UploadedFile
    f = db.query(UploadedFile).filter(UploadedFile.id == upload_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Не найдено")
    if f.saved_name:
        path = os.path.join(UPLOADS_DIR, f.saved_name)
        if os.path.exists(path):
            os.remove(path)
    db.delete(f)
    db.commit()
    return {"ok": True}

@app.get("/uploads")
def uploads_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/uploads.html")


@app.post("/api/fill-articles-from-pricelist")
def fill_articles_from_pricelist(db: Session = Depends(get_db)):
    """Заполняет barcode/kaspi_article у всех товаров у которых они пустые,
    матчинг по нечёткому имени из products_info.xml"""
    import xml.etree.ElementTree as ET
    import re
    from database import Product as _P

    xml_path = "products_info.xml"
    try:
        tree = ET.parse(xml_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось открыть XML: {e}")

    root = tree.getroot()
    товары = root.find("Товары")
    if товары is None:
        raise HTTPException(status_code=400, detail="Нет тега <Товары> в XML")

    STOPWORDS = {"и","в","на","с","для","из","по","шт","мл","л","кг","г","см","мм","м","гр","х","x","the","of","for","pcs"}
    def words(text):
        return {w for w in re.split(r'[\s\-_/,.()\[\]"«»\']+', (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}

    # Загружаем прайс в память
    price_items = []
    for товар in товары.findall("Товар"):
        name_el = товар.find("Наименование")
        article_el = товар.find("Артикул")
        supplier_el = товар.find("Поставщик")
        price_el = товар.find("ЦенаЗакупки")
        name = (name_el.text or "").strip() if name_el is not None else ""
        article = (article_el.text or "").strip() if article_el is not None else ""
        supplier = (supplier_el.text or "").strip() if supplier_el is not None else ""
        cost_price_raw = (price_el.text or "0").strip() if price_el is not None else "0"
        if not name:
            continue
        try:
            cost_price = int(float(cost_price_raw))
        except Exception:
            cost_price = None
        price_items.append({"name": name, "article": article, "supplier": supplier,
                            "cost_price": cost_price, "words": words(name)})

    # Берём все товары у которых нет артикула
    all_products = db.query(_P).all()
    updated = 0

    for p in all_products:
        if p.barcode and p.supplier_article:
            continue  # артикулы уже есть

        p_words = words(p.name)
        if not p_words:
            continue

        best_score = 0.0
        best_item = None
        for item in price_items:
            if not item["words"]:
                continue
            common = p_words & item["words"]
            score = len(common) / max(len(p_words), len(item["words"]))
            if score > best_score:
                best_score = score
                best_item = item

        if best_item and best_score >= 0.5:
            changed = False
            if best_item["article"] and not p.barcode:
                p.barcode = best_item["article"]
                changed = True
            if best_item["article"] and not p.supplier_article:
                p.supplier_article = best_item["article"]
                changed = True
            if best_item["cost_price"] is not None and not p.cost_price:
                p.cost_price = best_item["cost_price"]
                changed = True
            if best_item["supplier"] and not p.supplier:
                p.supplier = best_item["supplier"]
                changed = True
            if changed:
                updated += 1

    db.commit()
    return {"updated": updated, "total_products": len(all_products)}


@app.post("/api/import-price-list")
async def import_price_list(
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Импортирует закупочные цены из XML прайс-листа в таблицу products."""
    import xml.etree.ElementTree as ET
    import re
    from database import Product as _P

    if file:
        content = await file.read()
        original_name = file.filename or "price_list.xml"
        root = ET.fromstring(content)
        товары = root.find("Товары")
    else:
        xml_path = "products_info.xml"
        original_name = "products_info.xml"
        try:
            with open(xml_path, "rb") as f:
                content = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Не удалось открыть XML: {e}")
        root = ET.fromstring(content)
        товары = root.find("Товары")

    if товары is None:
        raise HTTPException(status_code=400, detail="Нет тега <Товары> в XML")

    import uuid
    created = 0

    # Индекс существующих артикулов чтобы не дублировать
    existing_articles = {p.supplier_article for p in db.query(_P).filter(_P.supplier_article != None).all()}
    existing_kaspi_skus = {p.kaspi_sku for p in db.query(_P).filter(_P.kaspi_sku != None).all()}

    for товар in товары.findall("Товар"):
        name_el = товар.find("Наименование")
        price_el = товар.find("ЦенаЗакупки")
        supplier_el = товар.find("Поставщик")
        article_el = товар.find("Артикул")
        unit_el = товар.find("ЕдИзм")

        name = (name_el.text or "").strip() if name_el is not None else ""
        cost_price_raw = (price_el.text or "0").strip() if price_el is not None else "0"
        supplier = (supplier_el.text or "").strip() if supplier_el is not None else ""
        article = (article_el.text or "").strip() if article_el is not None else ""
        unit = (unit_el.text or "шт").strip() if unit_el is not None else "шт"

        if not name:
            continue

        try:
            cost_price = int(float(cost_price_raw))
        except Exception:
            cost_price = None

        # Не создаём дубль если артикул уже есть в базе
        if article and article in existing_articles:
            continue

        # Уникальный SKU
        sku = article.upper() if article else f"PL-{uuid.uuid4().hex[:8].upper()}"
        while sku in existing_skus:
            sku = f"PL-{uuid.uuid4().hex[:8].upper()}"

        new_p = _P(
            name=name,
            sku=sku,
            supplier_article=article if article else None,
            category="Накладные",
            unit=unit,
            cost_price=cost_price,
            supplier=supplier,
        )
        db.add(new_p)
        existing_kaspi_skus.add(sku)
        if article:
            existing_articles.add(article)
        created += 1

    db.commit()
    _save_upload(content, original_name, "price_list", created, db)
    return {"created": created}


@app.post("/api/products/{product_id}/verify")
def toggle_verify(product_id: int, body: dict, db: Session = Depends(get_db)):
    from database import Product as _P
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p.verified = 1 if body.get("verified") else 0
    db.commit()
    return {"id": p.id, "verified": p.verified}


@app.get("/api/review-products")
def products_review(
    verified: Optional[str] = None,  # "yes" | "no" | None = все
    search: Optional[str] = None,
    supplier: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    from database import Product as _P
    q = db.query(_P).filter(_P.category != "Накладные")
    if verified == "yes":
        q = q.filter(_P.verified == 1)
    elif verified == "no":
        q = q.filter((_P.verified == None) | (_P.verified == 0))
    if supplier:
        q = q.filter(_P.supplier == supplier)
    if search:
        q = q.filter(_P.name.ilike(f"%{search}%"))
    total = q.count()
    products = q.order_by(_P.supplier, _P.name).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": p.id,
            "name": p.name,
            "sku": p.kaspi_sku or "",
            "category": p.category,
            "supplier": p.supplier or "",
            "cost_price": p.cost_price,
            "price": p.price,
            "unit": p.unit or "шт",
            "verified": bool(p.verified),
            "kaspi_sku": p.kaspi_sku or "",
            "supplier_article": p.supplier_article or "",
            "stock": crud.get_stock(p.id, db),
        } for p in products]
    }


@app.post("/api/reset-products")
def reset_products(body: dict, db: Session = Depends(get_db)):
    """Удаляет все товары и движения. Требует подтверждения."""
    from database import Product as _P, Movement
    if body.get("confirm") != "DELETE ALL PRODUCTS":
        raise HTTPException(status_code=400, detail="Неверное подтверждение")
    movements = db.query(Movement).delete()
    products = db.query(_P).delete()
    db.commit()
    return {"deleted_products": products, "deleted_movements": movements}


@app.post("/api/clean-bad-articles")
def clean_bad_articles(db: Session = Depends(get_db)):
    """Очищает мусор:
    1. Обнуляет KSP_... в поле sku (технический мусор, kaspi_sku хранится отдельно)
    2. Удаляет KSP_ и PL- из supplier_article и barcode
    3. Если barcode == supplier_article — обнуляет barcode
    """
    from database import Product as _P
    cleaned = 0
    for p in db.query(_P).all():
        changed = False
        for field in ("supplier_article", "barcode"):
            val = getattr(p, field)
            if val and (val.upper().startswith("KSP_") or val.upper().startswith("PL-")):
                setattr(p, field, None)
                changed = True
        if p.barcode and p.supplier_article and p.barcode == p.supplier_article:
            p.barcode = None
            changed = True
        if changed:
            cleaned += 1
    db.commit()
    return {"cleaned": cleaned}


@app.get("/review")
def review_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/review.html")


# ── Дизайн-токены (тема) ─────────────────────────────────────────────────────

DEFAULT_TOKENS = {
    "--bg":         {"value": "#f4f4f6",  "label": "Фон страницы",        "group": "Фоны"},
    "--surface":    {"value": "#ffffff",  "label": "Поверхность (карточки)","group": "Фоны"},
    "--sidebar-bg": {"value": "#f0f0f5",  "label": "Фон сайдбара",        "group": "Фоны"},
    "--border":     {"value": "#e5e7eb",  "label": "Граница основная",     "group": "Границы"},
    "--border2":    {"value": "#f0f0f2",  "label": "Граница второстепенная","group": "Границы"},
    "--text":       {"value": "#111827",  "label": "Текст основной",       "group": "Текст"},
    "--text2":      {"value": "#6b7280",  "label": "Текст второстепенный", "group": "Текст"},
    "--text3":      {"value": "#9ca3af",  "label": "Текст подсказки",      "group": "Текст"},
    "--accent":     {"value": "#6366f1",  "label": "Акцент (кнопки, ссылки)","group": "Акценты"},
    "--green":      {"value": "#16a34a",  "label": "Зелёный (наличие, успех)","group": "Акценты"},
    "--red":        {"value": "#ef4444",  "label": "Красный (ошибка, отмена)","group": "Акценты"},
    "--orange":     {"value": "#f97316",  "label": "Оранжевый (предупреждение)","group": "Акценты"},
    "--yellow":     {"value": "#eab308",  "label": "Жёлтый (статус)",     "group": "Акценты"},
}

@app.get("/api/admin/theme")
def get_theme(request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    from database import SiteSetting
    row = db.query(SiteSetting).filter(SiteSetting.key == "theme_tokens").first()
    if row and row.value:
        try:
            saved = json.loads(row.value)
            # Мержим с дефолтами (на случай новых токенов)
            tokens = {k: {**v, "value": saved.get(k, v["value"])} for k, v in DEFAULT_TOKENS.items()}
            return tokens
        except Exception:
            pass
    return DEFAULT_TOKENS

@app.post("/api/admin/theme")
def save_theme(data: dict, request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    from database import SiteSetting
    # Сохраняем только значения {varName: colorValue}
    values = {k: v for k, v in data.items() if k.startswith("--")}
    row = db.query(SiteSetting).filter(SiteSetting.key == "theme_tokens").first()
    if row:
        row.value = json.dumps(values)
    else:
        db.add(SiteSetting(key="theme_tokens", value=json.dumps(values), label="Дизайн-токены", group="theme"))
    db.commit()
    return {"ok": True}

@app.get("/api/admin/theme/css")
def get_theme_css(db: Session = Depends(get_db)):
    """Возвращает :root { ... } с текущими токенами — подключается на всех страницах"""
    from database import SiteSetting
    row = db.query(SiteSetting).filter(SiteSetting.key == "theme_tokens").first()
    overrides = {}
    if row and row.value:
        try:
            overrides = json.loads(row.value)
        except Exception:
            pass
    lines = []
    for var, meta in DEFAULT_TOKENS.items():
        val = overrides.get(var, meta["value"])
        lines.append(f"  {var}: {val};")
    css = ":root {\n" + "\n".join(lines) + "\n}"
    from fastapi.responses import Response
    return Response(content=css, media_type="text/css")

@app.get("/admin/theme", response_class=HTMLResponse)
def theme_page(request: Request):
    user = _get_user_from_session(request)
    if not user or user["role"] != "admin":
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    with open("static/theme.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/admin/processes")
def get_processes(db: Session = Depends(get_db)):
    """Статус фоновых процессов и БД."""
    from database import Product, KaspiOrder, Movement, PriceListItem, UploadedFile
    from sqlalchemy import func as sqlfunc

    uptime_sec = int(time.monotonic() - APP_START)
    h, rem = divmod(uptime_sec, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h}ч {m}м {s}с" if h else f"{m}м {s}с"

    # Счётчики БД
    db_counts = {}
    try:
        db_counts = {
            "products":   db.query(sqlfunc.count(Product.id)).scalar(),
            "kaspi":      db.query(sqlfunc.count(Product.id)).filter(Product.category == "Kaspi").scalar(),
            "orders":     db.query(sqlfunc.count(KaspiOrder.id)).scalar(),
            "movements":  db.query(sqlfunc.count(Movement.id)).scalar(),
            "pricelist":  db.query(sqlfunc.count(PriceListItem.id)).scalar(),
            "uploads":    db.query(sqlfunc.count(UploadedFile.id)).scalar(),
        }
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"

    # Kaspi sync — секунд до следующего запуска
    ks = _PROCESS_STATUS["kaspi_sync"]
    next_run_in = None
    if ks.get("next_run"):
        next_run_in = max(0, int(ks["next_run"] - datetime.utcnow().timestamp()))

    return {
        "uptime": uptime_str,
        "server_start": _PROCESS_STATUS["server_start"],
        "db": {"status": db_status, **db_counts},
        "processes": [
            {
                "name": "Kaspi синхронизация",
                "key": "kaspi_sync",
                "status": ks["status"],
                "last_run": ks["last_run"],
                "next_run_in": next_run_in,
                "last_result": ks["last_result"],
                "last_error": ks["last_error"],
                "cycle_count": ks["cycle_count"],
                "interval": "5 мин",
            },
        ],
    }


@app.get("/admin/bizmap", response_class=HTMLResponse)
def bizmap_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    with open("static/bizmap.html", encoding="utf-8") as f:
        return f.read()


@app.get("/admin/sitemap", response_class=HTMLResponse)
def sitemap_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    with open("static/sitemap.html", encoding="utf-8") as f:
        return f.read()


@app.get("/admin/changelog", response_class=HTMLResponse)
def changelog_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login")
    with open("static/changelog.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/admin/changelog")
def get_changelog(request: Request):
    """Возвращает список коммитов из changelog.json"""
    user = _get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=403)
    try:
        path = os.path.join(os.path.dirname(__file__), "static", "changelog.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
