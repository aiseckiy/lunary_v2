"""Публичный магазин: /shop, /api/store/products/*"""
import json
import random
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from helpers import parse_images

router = APIRouter(tags=["store"])


@router.get("/shop", response_class=HTMLResponse)
def shop_page():
    with open("static/store.html", encoding="utf-8") as f:
        return f.read()


@router.get("/api/store/products")
def store_products(db: Session = Depends(get_db)):
    """Публичный список товаров для магазина — только show_in_shop=True"""
    from database import Product as _P, Movement as _M
    stocks = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.show_in_shop == True)  # noqa: E712
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
            "images": parse_images(s[0]),
            "supplier_article": s[0].supplier_article or "",
        }
        for s in stocks
    ]


@router.get("/api/store/products/{product_id}/similar")
def store_product_similar(product_id: int, db: Session = Depends(get_db)):
    """Похожие товары — тот же бренд или категория, случайные 8 штук"""
    from database import Product as _P, Movement as _M

    src = db.query(_P).filter(_P.id == product_id, _P.show_in_shop == True).first()  # noqa: E712
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
        try:
            imgs = json.loads(p.images or "[]")
            img = imgs[0] if imgs else p.image_url
        except Exception:
            img = p.image_url
        result.append({
            "id": p.id, "name": p.name, "brand": p.brand or "",
            "price": p.price, "stock": int(stock),
            "image_url": img or "", "category": p.category or "",
        })
    return result


@router.get("/api/store/products/{product_id}")
def store_product_detail(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    row = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.id == product_id, _P.show_in_shop == True)  # noqa: E712
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
        "images": parse_images(p),
        "description": p.description or "",
        "specs": p.specs or "[]",
    }
