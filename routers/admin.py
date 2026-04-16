"""Admin utility endpoints: пользователи + Kaspi sync log."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from helpers import get_user_from_session, is_admin

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


@router.get("/api/admin/short-skus")
def list_short_skus(request: Request, db: Session = Depends(get_db)):
    """Товары с коротким SKU (без underscore) — кнопка 'На Kaspi' у них не работает."""
    from database import Product as _P
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    products = db.query(_P).filter(
        _P.kaspi_sku.isnot(None),
        _P.kaspi_sku != "",
        ~_P.kaspi_sku.contains("_"),
    ).order_by(_P.name).all()
    return {
        "count": len(products),
        "items": [
            {"id": p.id, "name": p.name, "kaspi_sku": p.kaspi_sku, "brand": p.brand or ""}
            for p in products
        ],
    }


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
