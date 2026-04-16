"""System: HTML admin pages + changelog + about. Без бизнес-логики.

/api/admin/processes остался в api.py (зависит от глобального _PROCESS_STATUS + APP_START).
"""
import json
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from helpers import get_user_from_session, is_admin

router = APIRouter(tags=["system"])


# ─── Публичные HTML-страницы ─────────────────────────────
@router.get("/about", response_class=HTMLResponse)
def about_page():
    with open("static/about.html", encoding="utf-8") as f:
        return f.read()


# ─── Admin HTML-страницы (простые file readers) ─────────
@router.get("/admin", response_class=HTMLResponse)
def dashboard():
    with open("static/index.html") as f:
        return f.read()


@router.get("/admin/scanner", response_class=HTMLResponse)
def scanner():
    with open("static/scanner.html") as f:
        return f.read()


@router.get("/admin/history", response_class=HTMLResponse)
def history_page():
    with open("static/history.html") as f:
        return f.read()


@router.get("/admin/analytics", response_class=HTMLResponse)
def analytics_page():
    with open("static/analytics.html") as f:
        return f.read()


@router.get("/admin/settings", response_class=HTMLResponse)
def settings_page():
    with open("static/settings.html") as f:
        return f.read()


@router.get("/admin/data", response_class=HTMLResponse)
def data_hub_page(request: Request):
    """Data hub — единая точка входа для импорта/экспорта/слияния/проверки."""
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    with open("static/data.html", encoding="utf-8") as f:
        return f.read()


@router.get("/api/admin/data-stats")
def data_stats(request: Request, db: Session = Depends(get_db)):
    """Статистика для hub-страницы /admin/data: сколько товаров, карточки,
    неревьюенные, последний импорт, справочник накладных."""
    from database import Product, PriceListItem, UploadedFile

    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=403)

    total_products = db.query(func.count(Product.id)).filter(Product.category != "Накладные").scalar() or 0
    kaspi_products = db.query(func.count(Product.id)).filter(Product.category == "Kaspi").scalar() or 0
    unverified = db.query(func.count(Product.id)).filter(
        Product.category != "Накладные",
        (Product.verified.is_(None)) | (Product.verified == 0)
    ).scalar() or 0
    pricelist_total = db.query(func.count(PriceListItem.id)).scalar() or 0
    pricelist_suppliers = db.query(func.count(func.distinct(PriceListItem.supplier))).scalar() or 0

    without_price = db.query(func.count(Product.id)).filter(
        Product.category == "Kaspi",
        (Product.price.is_(None)) | (Product.price == 0)
    ).scalar() or 0
    without_cost = db.query(func.count(Product.id)).filter(
        Product.category == "Kaspi",
        (Product.cost_price.is_(None)) | (Product.cost_price == 0)
    ).scalar() or 0

    last_import = db.query(UploadedFile).order_by(UploadedFile.uploaded_at.desc()).first()
    last_import_info = None
    if last_import:
        last_import_info = {
            "name": last_import.original_name or "",
            "type": last_import.file_type or "",
            "when": last_import.uploaded_at.strftime("%d.%m.%Y %H:%M") if last_import.uploaded_at else "",
            "records": last_import.records or 0,
        }

    return {
        "products": {
            "total": total_products,
            "kaspi": kaspi_products,
            "unverified": unverified,
            "without_price": without_price,
            "without_cost": without_cost,
        },
        "pricelist": {
            "total": pricelist_total,
            "suppliers": pricelist_suppliers,
        },
        "last_import": last_import_info,
    }


# ─── Bizmap / Sitemap / Changelog (требуют login) ────────
@router.get("/admin/bizmap", response_class=HTMLResponse)
def bizmap_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    with open("static/bizmap.html", encoding="utf-8") as f:
        return f.read()


@router.get("/admin/sitemap", response_class=HTMLResponse)
def sitemap_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    with open("static/sitemap.html", encoding="utf-8") as f:
        return f.read()


@router.get("/admin/changelog", response_class=HTMLResponse)
def changelog_page(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    with open("static/changelog.html", encoding="utf-8") as f:
        return f.read()


@router.get("/api/admin/changelog")
def get_changelog(request: Request):
    """Возвращает список коммитов из changelog.json"""
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=403)
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "changelog.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
