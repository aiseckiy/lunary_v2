"""Инвентаризация: аудит остатков на складе.

Workflow:
1. POST /api/audit/start → создать сессию
2. POST /api/audit/{id}/check → проверить товар (ввести фактический остаток)
3. POST /api/audit/{id}/finish → завершить (apply корректировки опционально)
4. GET /api/audit/history → список всех аудитов
5. GET /api/audit/{id} → детали аудита с проверенными/непроверенными товарами
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from helpers import get_user_from_session, is_staff
import crud

router = APIRouter(tags=["audit"])


@router.post("/api/audit/start")
def start_audit(request: Request, db: Session = Depends(get_db)):
    """Начать новую инвентаризацию."""
    from database import Audit
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    # Закрыть предыдущий незавершённый аудит если есть
    active = db.query(Audit).filter(Audit.status == "active").first()
    if active:
        active.status = "completed"
        active.finished_at = datetime.utcnow()

    audit = Audit(
        status="active",
        user_name=user.get("name") or user.get("email") or "",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return {"ok": True, "audit_id": audit.id}


@router.get("/api/audit/active")
def get_active_audit(request: Request, db: Session = Depends(get_db)):
    """Текущий активный аудит (или null)."""
    from database import Audit, AuditItem
    from sqlalchemy import func
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    audit = db.query(Audit).filter(Audit.status == "active").first()
    if not audit:
        return {"audit": None}

    checked_count = db.query(func.count(AuditItem.id)).filter(AuditItem.audit_id == audit.id).scalar() or 0
    total_products = len(crud.get_all_stocks(db))

    return {
        "audit": {
            "id": audit.id,
            "started_at": audit.started_at.strftime("%d.%m.%Y %H:%M") if audit.started_at else "",
            "user_name": audit.user_name or "",
            "checked": checked_count,
            "total": total_products,
            "remaining": total_products - checked_count,
        }
    }


@router.post("/api/audit/{audit_id}/check")
def check_item(audit_id: int, body: dict, request: Request, db: Session = Depends(get_db)):
    """Проверить один товар: записать фактический остаток."""
    from database import Audit, AuditItem
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    audit = db.query(Audit).filter(Audit.id == audit_id, Audit.status == "active").first()
    if not audit:
        raise HTTPException(status_code=404, detail="Аудит не найден или уже завершён")

    product_id = body.get("product_id")
    actual_qty = body.get("actual_qty")
    note = body.get("note", "")

    if product_id is None or actual_qty is None:
        raise HTTPException(status_code=400, detail="product_id и actual_qty обязательны")

    actual_qty = int(actual_qty)
    system_qty = crud.get_stock(product_id, db)
    delta = actual_qty - system_qty

    # Если уже проверяли — обновляем
    existing = db.query(AuditItem).filter(
        AuditItem.audit_id == audit_id,
        AuditItem.product_id == product_id
    ).first()

    if existing:
        existing.system_qty = system_qty
        existing.actual_qty = actual_qty
        existing.delta = delta
        existing.checked_at = datetime.utcnow()
        existing.note = note or existing.note
    else:
        db.add(AuditItem(
            audit_id=audit_id,
            product_id=product_id,
            system_qty=system_qty,
            actual_qty=actual_qty,
            delta=delta,
            note=note,
        ))

    db.commit()

    p = crud.get_product_by_id(product_id, db)
    return {
        "ok": True,
        "product_name": p.name if p else "",
        "system_qty": system_qty,
        "actual_qty": actual_qty,
        "delta": delta,
    }


@router.post("/api/audit/{audit_id}/finish")
def finish_audit(audit_id: int, body: dict, request: Request, db: Session = Depends(get_db)):
    """Завершить аудит. Если apply_corrections=true → создать Movement-записи для всех расхождений."""
    from database import Audit, AuditItem
    from sqlalchemy import func
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    audit = db.query(Audit).filter(Audit.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Аудит не найден")

    apply = body.get("apply_corrections", False)

    items = db.query(AuditItem).filter(AuditItem.audit_id == audit_id).all()
    corrections = 0

    if apply:
        for item in items:
            if item.delta != 0:
                move_type = "income" if item.delta > 0 else "writeoff"
                crud.add_movement(
                    item.product_id, abs(item.delta), move_type, db,
                    source="audit",
                    note=f"Инвентаризация #{audit_id}: было {item.system_qty}, факт {item.actual_qty}",
                    user_name=user.get("name") or user.get("email"),
                )
                corrections += 1

    audit.status = "completed"
    audit.finished_at = datetime.utcnow()
    audit.total_checked = len(items)
    audit.total_delta = sum(abs(i.delta) for i in items)
    audit.note = body.get("note", audit.note)
    db.commit()

    return {
        "ok": True,
        "checked": len(items),
        "corrections_applied": corrections,
        "total_delta": audit.total_delta,
    }


@router.get("/api/audit/{audit_id}")
def get_audit_detail(audit_id: int, request: Request, db: Session = Depends(get_db)):
    """Детали аудита: проверенные товары + непроверенные."""
    from database import Audit, AuditItem, Product
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    audit = db.query(Audit).filter(Audit.id == audit_id).first()
    if not audit:
        raise HTTPException(status_code=404)

    items = db.query(AuditItem, Product).join(
        Product, AuditItem.product_id == Product.id
    ).filter(AuditItem.audit_id == audit_id).order_by(AuditItem.checked_at.desc()).all()

    checked_ids = {item.product_id for item, _ in items}

    # Непроверенные — все товары склада кроме тех что в checked_ids
    all_stocks = crud.get_all_stocks(db)
    unchecked = [
        {"id": s["product"].id, "name": s["product"].name, "stock": s["stock"],
         "brand": s["product"].brand or "", "kaspi_sku": s["product"].kaspi_sku or ""}
        for s in all_stocks if s["product"].id not in checked_ids
    ]

    return {
        "audit": {
            "id": audit.id,
            "status": audit.status,
            "started_at": audit.started_at.strftime("%d.%m.%Y %H:%M") if audit.started_at else "",
            "finished_at": audit.finished_at.strftime("%d.%m.%Y %H:%M") if audit.finished_at else "",
            "user_name": audit.user_name or "",
            "total_checked": audit.total_checked or len(items),
            "total_delta": audit.total_delta or 0,
            "note": audit.note or "",
        },
        "checked": [
            {
                "product_id": item.product_id,
                "product_name": p.name,
                "brand": p.brand or "",
                "system_qty": item.system_qty,
                "actual_qty": item.actual_qty,
                "delta": item.delta,
                "checked_at": item.checked_at.strftime("%H:%M") if item.checked_at else "",
                "note": item.note or "",
            }
            for item, p in items
        ],
        "unchecked": unchecked,
        "unchecked_count": len(unchecked),
    }


@router.get("/api/audit/history")
def audit_history(request: Request, db: Session = Depends(get_db)):
    """Список всех аудитов."""
    from database import Audit
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    audits = db.query(Audit).order_by(Audit.id.desc()).limit(50).all()
    return [
        {
            "id": a.id,
            "status": a.status,
            "started_at": a.started_at.strftime("%d.%m.%Y %H:%M") if a.started_at else "",
            "finished_at": a.finished_at.strftime("%d.%m.%Y %H:%M") if a.finished_at else "",
            "user_name": a.user_name or "",
            "total_checked": a.total_checked or 0,
            "total_delta": a.total_delta or 0,
        }
        for a in audits
    ]


@router.get("/admin/audit")
def audit_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    return FileResponse("static/audit.html")
