"""Settings + Theme: site settings CRUD + дизайн-токены + theme.html."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from helpers import get_user_from_session, is_admin

router = APIRouter(tags=["settings"])


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


@router.get("/api/settings")
def get_public_settings(db: Session = Depends(get_db)):
    """Публичные настройки для магазина (без integrations)"""
    from database import SiteSetting
    rows = db.query(SiteSetting).filter(SiteSetting.group != "integrations").all()
    return {r.key: r.value for r in rows}


@router.get("/api/admin/settings")
def get_admin_settings(request: Request, db: Session = Depends(get_db)):
    from database import SiteSetting
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    rows = db.query(SiteSetting).order_by(SiteSetting.group, SiteSetting.key).all()
    return [{"key": r.key, "value": r.value or "", "label": r.label, "group": r.group} for r in rows]


@router.post("/api/admin/settings")
def save_admin_settings(data: dict, request: Request, db: Session = Depends(get_db)):
    from database import SiteSetting
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    for key, value in data.items():
        row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(SiteSetting(key=key, value=str(value)))
    db.commit()
    return {"ok": True}


@router.get("/api/admin/theme")
def get_theme(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    from database import SiteSetting
    row = db.query(SiteSetting).filter(SiteSetting.key == "theme_tokens").first()
    if row and row.value:
        try:
            saved = json.loads(row.value)
            tokens = {k: {**v, "value": saved.get(k, v["value"])} for k, v in DEFAULT_TOKENS.items()}
            return tokens
        except Exception:
            pass
    return DEFAULT_TOKENS


@router.post("/api/admin/theme")
def save_theme(data: dict, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    from database import SiteSetting
    values = {k: v for k, v in data.items() if k.startswith("--")}
    row = db.query(SiteSetting).filter(SiteSetting.key == "theme_tokens").first()
    if row:
        row.value = json.dumps(values)
    else:
        db.add(SiteSetting(key="theme_tokens", value=json.dumps(values), label="Дизайн-токены", group="theme"))
    db.commit()
    return {"ok": True}


@router.get("/api/admin/theme/css")
def get_theme_css(db: Session = Depends(get_db)):
    """:root { ... } с текущими токенами — подключается на всех страницах через nav.js."""
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
    return Response(content=css, media_type="text/css")


@router.get("/admin/theme", response_class=HTMLResponse)
def theme_page(request: Request):
    user = get_user_from_session(request)
    if not is_admin(user):
        return RedirectResponse("/login")
    with open("static/theme.html", encoding="utf-8") as f:
        return f.read()
