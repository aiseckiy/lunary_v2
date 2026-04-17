"""BrandAlias / CategoryAlias admin endpoints + страницы-справочники.

BrandAlias: raw_name (из Kaspi) → shop_name (для магазина) + hidden
CategoryAlias: raw_name → shop_name + icon + sort_order + hidden

Используются в resolve_shop_view для нормализации брендов и категорий
на витрине lunary.kz/shop. Kaspi feed/XML не затрагиваются.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from database import get_db
from helpers import get_user_from_session, is_staff

router = APIRouter(tags=["aliases"])


# ══════════════════════════════════════════════════════
# Brand aliases
# ══════════════════════════════════════════════════════
@router.get("/api/admin/brand-aliases")
def list_brand_aliases(request: Request, db: Session = Depends(get_db)):
    """Список всех raw-брендов с количеством товаров и shop-именем."""
    from database import BrandAlias, Product
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    # Счётчик товаров по каждому raw brand
    counts = dict(
        db.query(Product.brand, sqlfunc.count(Product.id))
        .filter(Product.brand.isnot(None), Product.brand != "")
        .group_by(Product.brand)
        .all()
    )

    aliases = db.query(BrandAlias).order_by(BrandAlias.raw_name).all()
    result = []
    for a in aliases:
        result.append({
            "id": a.id,
            "raw_name": a.raw_name,
            "shop_name": a.shop_name or "",
            "hidden": bool(a.hidden),
            "products_count": counts.get(a.raw_name, 0),
        })

    # Также покажем raw-бренды которых нет в aliases (если кто-то добавил
    # товар минуя импорт). Автоматически их создадим при первом открытии.
    orphans_created = 0
    existing_raws = {a.raw_name for a in aliases}
    for raw, cnt in counts.items():
        if raw and raw not in existing_raws:
            db.add(BrandAlias(raw_name=raw))
            orphans_created += 1
    if orphans_created:
        db.commit()
        # перечитаем
        aliases = db.query(BrandAlias).order_by(BrandAlias.raw_name).all()
        result = [
            {
                "id": a.id, "raw_name": a.raw_name, "shop_name": a.shop_name or "",
                "hidden": bool(a.hidden), "products_count": counts.get(a.raw_name, 0),
            }
            for a in aliases
        ]

    # Сортируем: сначала не-причёсанные (shop_name пуст) и с большим количеством товаров,
    # потом причёсанные.
    result.sort(key=lambda x: (bool(x["shop_name"]), -x["products_count"]))

    # Группировка для UI: какие shop_name уже существуют (для autocomplete)
    shop_names = sorted({r["shop_name"] for r in result if r["shop_name"]})

    # Товары совсем без бренда
    no_brand_count = db.query(sqlfunc.count(Product.id)).filter(
        (Product.brand.is_(None)) | (Product.brand == "")
    ).filter(Product.category != "Накладные").scalar() or 0

    return {"items": result, "shop_names": shop_names, "no_brand_count": no_brand_count}


@router.patch("/api/admin/brand-aliases/{alias_id}")
def update_brand_alias(alias_id: int, body: dict, request: Request, db: Session = Depends(get_db)):
    """Обновить shop_name / hidden у записи."""
    from database import BrandAlias
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    a = db.query(BrandAlias).filter(BrandAlias.id == alias_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alias не найден")

    if "shop_name" in body:
        val = (body["shop_name"] or "").strip()
        a.shop_name = val or None
    if "hidden" in body:
        a.hidden = bool(body["hidden"])

    db.commit()
    return {"ok": True, "id": a.id, "shop_name": a.shop_name or "", "hidden": bool(a.hidden)}


@router.get("/admin/brands")
def brands_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    return FileResponse("static/brands.html")


# ══════════════════════════════════════════════════════
# Category aliases
# ══════════════════════════════════════════════════════
@router.get("/api/admin/category-aliases")
def list_category_aliases(request: Request, db: Session = Depends(get_db)):
    """Список всех raw-категорий с количеством товаров."""
    from database import CategoryAlias, Product
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    counts = dict(
        db.query(Product.category, sqlfunc.count(Product.id))
        .filter(Product.category.isnot(None), Product.category != "")
        .group_by(Product.category)
        .all()
    )

    aliases = db.query(CategoryAlias).order_by(CategoryAlias.sort_order, CategoryAlias.raw_name).all()
    existing_raws = {a.raw_name for a in aliases}

    # Автосоздать для orphan категорий
    orphans = 0
    for raw, cnt in counts.items():
        if raw and raw not in existing_raws:
            db.add(CategoryAlias(raw_name=raw))
            orphans += 1
    if orphans:
        db.commit()
        aliases = db.query(CategoryAlias).order_by(CategoryAlias.sort_order, CategoryAlias.raw_name).all()

    result = [
        {
            "id": a.id,
            "raw_name": a.raw_name,
            "shop_name": a.shop_name or "",
            "icon": a.icon or "",
            "sort_order": a.sort_order or 0,
            "hidden": bool(a.hidden),
            "products_count": counts.get(a.raw_name, 0),
        }
        for a in aliases
    ]
    result.sort(key=lambda x: (bool(x["shop_name"]), -x["products_count"]))

    shop_names = sorted({r["shop_name"] for r in result if r["shop_name"]})
    return {"items": result, "shop_names": shop_names}


@router.patch("/api/admin/category-aliases/{alias_id}")
def update_category_alias(alias_id: int, body: dict, request: Request, db: Session = Depends(get_db)):
    from database import CategoryAlias
    user = get_user_from_session(request)
    if not is_staff(user):
        raise HTTPException(status_code=403)

    a = db.query(CategoryAlias).filter(CategoryAlias.id == alias_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alias не найден")

    if "shop_name" in body:
        val = (body["shop_name"] or "").strip()
        a.shop_name = val or None
    if "icon" in body:
        a.icon = (body["icon"] or "").strip() or None
    if "sort_order" in body:
        try:
            a.sort_order = int(body["sort_order"])
        except Exception:
            pass
    if "hidden" in body:
        a.hidden = bool(body["hidden"])

    db.commit()
    return {
        "ok": True,
        "id": a.id,
        "shop_name": a.shop_name or "",
        "icon": a.icon or "",
        "sort_order": a.sort_order or 0,
        "hidden": bool(a.hidden),
    }


@router.get("/admin/categories")
def categories_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    return FileResponse("static/categories.html")
