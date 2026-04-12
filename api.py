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
                "/api/history", "/api/admin", "/api/purchases", "/api/alerts")


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
                return {"role": user.role, "name": user.name, "email": user.email, "id": user.id}
    except Exception:
        pass
    finally:
        db.close()
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Публичные пути — без авторизации
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        user = _get_user_from_session(request)

        # Админские пути — только admin
        is_admin_path = any(path.startswith(p) for p in _ADMIN_PATHS)
        if is_admin_path:
            if not user or user["role"] != "admin":
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
    import threading, time, json
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

                if added or returns_count:
                    print(f"✅ Kaspi sync: +{added} новых, обновлено {updated_count}, возвратов {returns_count}")
            except Exception as e:
                print(f"⚠️ Kaspi sync error: {e}")
                try:
                    from database import SyncLog
                    db.add(SyncLog(synced_at=datetime.utcnow(), error=str(e)[:500]))
                    db.commit()
                except Exception:
                    pass
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
        path = os.path.join(os.path.dirname(__file__), '_archive', 'export_products.json')
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
    import urllib.parse
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
    import urllib.parse, urllib.request, json, hashlib
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

    next_url = "/admin" if user.role == "admin" else "/shop"
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
    import json
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
            "sku": s["product"].sku,
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
            "supplier": s["product"].supplier or ""
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


@app.get("/api/products/suppliers")
def get_suppliers(db: Session = Depends(get_db)):
    from database import Product as _P
    rows = db.query(_P.supplier).filter(_P.supplier != None, _P.supplier != "").distinct().all()
    return sorted([r[0] for r in rows if r[0]])


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
    from database import Product as _P
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if "sku" in updates:
        updates["sku"] = updates["sku"].upper()
    if "barcode" in updates and updates["barcode"]:
        conflict = db.query(_P).filter(_P.barcode == updates["barcode"], _P.id != product_id).first()
        if conflict:
            raise HTTPException(status_code=409, detail=f"Штрихкод уже привязан к товару «{conflict.name}» (арт. {conflict.sku})")
    p = crud.update_product(product_id, db, **updates)
    return {"id": p.id, "name": p.name, "sku": p.sku}


@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    stock = crud.get_stock(product_id, db)
    return {"product": {
        "id": p.id, "name": p.name, "sku": p.sku or "", "kaspi_sku": p.kaspi_sku or "", "kaspi_article": p.kaspi_article or "",
        "category": p.category or "", "unit": p.unit or "шт",
        "price": p.price, "min_stock": p.min_stock,
        "stock": stock, "low": stock <= (p.min_stock or 0),
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
                raise HTTPException(status_code=409, detail=f"Штрихкод уже привязан к товару «{conflict.name}» (арт. {conflict.sku})")
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
                "product_sku": p.sku,
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
    import json as _j
    if p.images:
        try:
            imgs = _j.loads(p.images)
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
    """Публичный список товаров для магазина (только с ценой и в наличии)"""
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    stocks = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .group_by(_P.id)
        .all()
    )
    return [
        {
            "id": s[0].id,
            "name": s[0].name,
            "sku": s[0].sku,
            "category": s[0].category or "Другое",
            "brand": s[0].brand or "",
            "price": s[0].price,
            "unit": s[0].unit or "шт",
            "stock": int(s[1]),
            "image_url": s[0].image_url or "",
            "images": _parse_images(s[0]),
        }
        for s in stocks
    ]


@app.get("/api/store/products/{product_id}")
def store_product_detail(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    row = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.id == product_id)
        .group_by(_P.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p, stock = row
    return {
        "id": p.id, "name": p.name, "sku": p.sku,
        "category": p.category or "Другое",
        "brand": p.brand or "",
        "price": p.price, "unit": p.unit or "шт",
        "stock": int(stock), "min_stock": p.min_stock or 0,
        "image_url": p.image_url or "",
        "images": _parse_images(p),
    }


class ProductImagesBody(BaseModel):
    images: list  # список URL/base64

@app.post("/api/products/{product_id}/image")
def save_product_images(product_id: int, data: ProductImagesBody, request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только администратор")
    import json as _json
    from database import Product as _P
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p.images = _json.dumps(data.images, ensure_ascii=False)
    p.image_url = data.images[0] if data.images else None
    db.commit()
    return {"ok": True}


@app.get("/shop/product/{product_id}", response_class=HTMLResponse)
def shop_product_page(product_id: int):
    with open("static/product.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/shop/my-orders")
def my_orders(request: Request, db: Session = Depends(get_db)):
    user = _get_user_from_session(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    from database import ShopOrder
    orders = db.query(ShopOrder).filter(ShopOrder.user_id == user.id).order_by(ShopOrder.created_at.desc()).all()
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
    """Отправить сообщение в Telegram через Bot API напрямую (без polling)."""
    bot_token = _get_integration("tg_bot_token", "BOT_TOKEN")
    chat_id = _get_integration("tg_chat_id", "ADMIN_CHAT_ID")
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
    import json
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
    import json

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
    import json
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
    import json
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
def products_export_xlsx(db: Session = Depends(get_db)):
    """Экспорт остатков в Excel (формат загрузки Kaspi)"""
    from fastapi.responses import StreamingResponse
    from sqlalchemy import func
    import openpyxl, io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from database import Product, Movement

    rows = (
        db.query(Product, func.coalesce(func.sum(Movement.quantity), 0).label("stock"))
        .outerjoin(Movement, Movement.product_id == Product.id)
        .group_by(Product.id)
        .order_by(Product.name)
        .all()
    )

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
            p.sku or "",
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

    COMPLETED_STATES = {"Выдан", "ARCHIVE"}
    CANCELLED_STATES = {"Отменен", "CANCELLED"}
    completed = sum(1 for r in rows if r.state in COMPLETED_STATES)
    cancelled = sum(1 for r in rows if r.state in CANCELLED_STATES)

    # Выручка только по завершённым заказам (без отменённых и в процессе)
    total_revenue = sum(r.total or 0 for r in rows if r.state in COMPLETED_STATES)
    avg_order = int(total_revenue / completed) if completed else 0

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
    import json
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
        order_items.append({"product_id": p.id, "name": p.name, "qty": qty, "price": price, "sku": p.sku or ""})
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
    req_lib.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=5
    )


@app.get("/api/admin/shop-orders")
def list_shop_orders(request: Request, status: Optional[str] = None, db: Session = Depends(get_db)):
    import json
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
    products = crud.get_all_products(db)
    result = []
    for p in products:
        stock = crud.get_stock(p.id, db)
        min_s = p.min_stock or 0
        if stock <= min_s:
            result.append({
                "id": p.id,
                "name": p.name,
                "sku": p.sku or "",
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
    import json, os
    path = os.path.join(os.path.dirname(__file__), '_archive', 'export_products.json')
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
        with open("static/changelog.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
