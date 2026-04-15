"""System: HTML admin pages + changelog + about. Без бизнес-логики.

/api/admin/processes остался в api.py (зависит от глобального _PROCESS_STATUS + APP_START).
"""
import json
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from helpers import get_user_from_session

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


# ─── Старые пути → редирект на /admin/* ─────────────────
@router.get("/analytics", response_class=HTMLResponse)
def analytics_redirect():
    return RedirectResponse("/admin/analytics", status_code=301)


@router.get("/history", response_class=HTMLResponse)
def history_redirect():
    return RedirectResponse("/admin/history", status_code=301)


@router.get("/scanner", response_class=HTMLResponse)
def scanner_redirect():
    return RedirectResponse("/admin/scanner", status_code=301)


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
