"""Review: верификация товаров, список на ревью, опасные очистки."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
import crud

router = APIRouter(tags=["review"])


@router.post("/api/products/{product_id}/verify")
def toggle_verify(product_id: int, body: dict, db: Session = Depends(get_db)):
    from database import Product as _P
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p.verified = 1 if body.get("verified") else 0
    db.commit()
    return {"id": p.id, "verified": p.verified}


@router.get("/api/review-products")
def products_review(
    verified: Optional[str] = None,
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
        q = q.filter((_P.verified.is_(None)) | (_P.verified == 0))
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


@router.post("/api/reset-products")
def reset_products(body: dict, db: Session = Depends(get_db)):
    """Удаляет все товары и движения. Требует подтверждения."""
    from database import Product as _P, Movement
    if body.get("confirm") != "DELETE ALL PRODUCTS":
        raise HTTPException(status_code=400, detail="Неверное подтверждение")
    movements = db.query(Movement).delete()
    products = db.query(_P).delete()
    db.commit()
    return {"deleted_products": products, "deleted_movements": movements}


@router.post("/api/clean-bad-articles")
def clean_bad_articles(db: Session = Depends(get_db)):
    """Очищает мусор: KSP_/PL- префиксы из supplier_article и barcode."""
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


@router.get("/review")
def review_page():
    return FileResponse("static/review.html")
