"""Admin utility endpoints: пользователи + migrations + Kaspi debug/dedupe/sync-log."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db, init_db
from helpers import get_user_from_session, is_admin
import kaspi as kaspi_module

router = APIRouter(tags=["admin"])


@router.get("/api/admin/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    from database import User as UserModel
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    users = db.query(UserModel).order_by(UserModel.created_at.desc()).all()
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role,
             "avatar": u.avatar, "created_at": str(u.created_at)} for u in users]


@router.patch("/api/admin/users/{user_id}")
def update_user_role(user_id: int, data: dict, request: Request, db: Session = Depends(get_db)):
    from database import User as UserModel
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    u = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404)
    if "role" in data:
        u.role = data["role"]
    db.commit()
    return {"ok": True, "role": u.role}


@router.post("/api/admin/run-migrations")
def run_migrations():
    """Принудительно применить все pending миграции БД."""
    init_db()
    return {"ok": True, "message": "Миграции применены"}


@router.post("/api/admin/kaspi/backfill-entries")
def backfill_kaspi_entries(request: Request, db: Session = Depends(get_db)):
    """Загружает состав заказов у которых entries пустые."""
    from database import KaspiOrder
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    orders = db.query(KaspiOrder).filter(
        (KaspiOrder.entries.is_(None)) |
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


@router.get("/api/admin/kaspi/debug-entries/{order_id}")
def debug_kaspi_entries(order_id: str, request: Request):
    """Временный: посмотреть сырой ответ Kaspi для entries."""
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    raw = kaspi_module._proxy("get_order_entries", {"orderId": order_id})
    return {"raw": raw}


@router.post("/api/admin/dedupe-kaspi-orders")
def dedupe_kaspi_orders(request: Request, db: Session = Depends(get_db)):
    """Удаляет base64 дубли заказов Kaspi, оставляя числовые ID."""
    import base64
    from database import KaspiOrder
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    all_orders = db.query(KaspiOrder).all()
    deleted = 0
    migrated = 0

    for o in all_orders:
        oid = o.order_id
        if not oid.isdigit():
            try:
                decoded = base64.b64decode(oid + "==").decode("utf-8").strip()
                if decoded.isdigit():
                    numeric = db.query(KaspiOrder).filter(KaspiOrder.order_id == decoded).first()
                    if numeric:
                        db.delete(o)
                        deleted += 1
                    else:
                        o.order_id = decoded
                        migrated += 1
            except Exception:
                pass

    db.commit()
    return {"ok": True, "deleted": deleted, "migrated": migrated}


@router.get("/api/admin/kaspi/sync-log")
def get_sync_log(request: Request, db: Session = Depends(get_db)):
    """Последние 50 запусков синхронизации Kaspi."""
    from database import SyncLog
    from datetime import timezone, timedelta
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    rows = db.query(SyncLog).order_by(SyncLog.id.desc()).limit(50).all()
    tz_kz = timezone(timedelta(hours=5))
    result = []
    for r in rows:
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
