"""Публичный магазин: /shop, /api/store/products/*

Использует resolve_shop_view — отдаёт причёсанные данные (бренды/категории
через aliases, имена через PriceListItem для залинкованных товаров).
Raw Kaspi-данные остаются в Product нетронутыми.
"""
import json
import random
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from helpers import build_brand_map, build_category_map, resolve_shop_view, parse_images

router = APIRouter(tags=["store"])


@router.get("/shop", response_class=HTMLResponse)
def shop_page():
    with open("static/store.html", encoding="utf-8") as f:
        return f.read()


@router.get("/api/store/products")
def store_products(db: Session = Depends(get_db)):
    """Публичный список товаров для магазина — только show_in_shop=True"""
    from database import Product as _P, Movement as _M, PriceListItem
    stocks = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.show_in_shop == True)  # noqa: E712
        .group_by(_P.id)
        .all()
    )

    # Batch-оптимизация: строим индексы один раз
    brand_map = build_brand_map(db)
    category_map = build_category_map(db)
    linked_ids = {p.linked_ref_id for p, _ in stocks if p.linked_ref_id}
    ref_map = {}
    if linked_ids:
        for ri in db.query(PriceListItem).filter(PriceListItem.id.in_(linked_ids)).all():
            ref_map[ri.id] = ri

    result = []
    for p, stock in stocks:
        ref = ref_map.get(p.linked_ref_id) if p.linked_ref_id else None
        view = resolve_shop_view(p, db, brand_map, category_map, ref_item=ref)
        view["stock"] = int(stock)
        view["image_url"] = view["images"][0] if view["images"] else ""
        result.append(view)
    return result


@router.get("/api/store/products/{product_id}/similar")
def store_product_similar(product_id: int, db: Session = Depends(get_db)):
    """Похожие товары — тот же бренд или категория, случайные 8 штук.
    Сравнение ведётся по raw-брендам/категориям, а отдаются shop-view."""
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
    brand_map = build_brand_map(db)
    category_map = build_category_map(db)
    result = []
    for p, stock in pool[:8]:
        view = resolve_shop_view(p, db, brand_map, category_map)
        view["stock"] = int(stock)
        view["image_url"] = view["images"][0] if view["images"] else ""
        result.append(view)
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
    view = resolve_shop_view(p, db)
    view["stock"] = int(stock)
    view["min_stock"] = p.min_stock or 0
    view["image_url"] = view["images"][0] if view["images"] else ""
    view["sku"] = p.kaspi_sku or ""  # backward compat alias
    # specs в старом API возвращались как JSON-строка — сохраняем контракт
    view["specs"] = json.dumps(view["specs"], ensure_ascii=False)
    return view
