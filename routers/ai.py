"""AI и изображения: OpenAI описания/характеристики/SEO, SerpApi Google Images.

Endpoints:
- POST /api/products/{id}/image          — сохранить массив images (URL/base64)
- GET  /api/products/{id}/search-images  — поиск через SerpApi
- GET  /api/admin/test-google-images     — тест SerpApi
- POST /api/products/{id}/ai-describe    — OpenAI: description + specs + SEO meta
- POST /api/admin/fill-descriptions      — bulk ai-describe
- POST /api/admin/fill-images            — bulk SerpApi
- POST /api/ai/suggest                   — категория/бренд/unit по названию
"""
import json
import os
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from helpers import get_user_from_session, is_admin

router = APIRouter(tags=["ai"])


class ProductImagesBody(BaseModel):
    images: list


class AISuggestRequest(BaseModel):
    name: str
    barcode: Optional[str] = None


def _get_openai_key(db: Session) -> str:
    """OpenAI ключ: сначала ENV, потом SiteSetting."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        from database import SiteSetting
        settings = {s.key: s.value for s in db.query(SiteSetting).all()}
        api_key = settings.get("openai_api_key", "")
    return api_key


def _parse_json_from_llm(text: str) -> dict:
    """Чистит markdown-обёртку и парсит JSON из ответа LLM."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _build_ai_prompt(p) -> str:
    """Промпт для ai-describe: возвращает description + specs + SEO meta полями."""
    existing_specs = ""
    try:
        specs = json.loads(p.specs or "[]")
        if specs:
            existing_specs = "\nУже известные характеристики:\n" + "\n".join(f"- {s['key']}: {s['value']}" for s in specs)
    except Exception:
        pass

    price_hint = f"- Цена: {p.price} тенге" if p.price else ""
    return f"""Ты SEO-специалист и копирайтер для интернет-магазина строительных материалов в Казахстане (lunary.kz).

Товар:
- Название: {p.name}
- Бренд: {p.brand or 'не указан'}
- Категория: {p.category or 'не указана'}
- Единица: {p.unit or 'шт'}
{price_hint}
{existing_specs}

Задача — заполни 5 полей:

1. **description** — продающее описание (2-4 предложения). Что это, для чего применяется, главные преимущества. Это НОВЫЙ товар — НЕ пиши "состояние", "б/у", "новое". Только технические свойства и применение.

2. **specs** — технические характеристики (5-10 строк). Только реальные параметры: объём, состав, цвет, температура применения, нагрузка и т.п. ЗАПРЕЩЕНО: "Состояние", "Тип объявления", "Наличие", "Страна".

3. **meta_title** — SEO-заголовок страницы (50-60 символов). Формат: "[Название товара] купить в Алматы | LUNARY". Включи главный поисковый запрос по которому люди ищут этот товар.

4. **meta_description** — SEO-описание (150-160 символов). Включи: название, главное применение, призыв к действию ("купить", "заказать"), упомяни Казахстан или Алматы для локального SEO.

5. **meta_keywords** — 8-12 ключевых слов через запятую. Включи: название товара, бренд, синонимы, применение, "купить", "цена", "Алматы", "Казахстан". Разные варианты написания и сочетания запросов.

Верни ТОЛЬКО JSON без markdown-обёртки:
{{
  "description": "текст описания",
  "specs": [{{"key": "Характеристика", "value": "значение"}}],
  "meta_title": "SEO заголовок",
  "meta_description": "SEO описание",
  "meta_keywords": "ключ1, ключ2, ключ3"
}}"""


def _apply_ai_result(p, result: dict):
    """Применяет результат AI к товару: description, specs (мёрджит), SEO meta."""
    p.description = result.get("description", p.description)
    specs_new = result.get("specs", [])
    if specs_new:
        try:
            existing = json.loads(p.specs or "[]")
        except Exception:
            existing = []
        existing_keys = {s["key"].lower() for s in existing}
        for s in specs_new:
            if s["key"].lower() not in existing_keys:
                existing.append(s)
        p.specs = json.dumps(existing, ensure_ascii=False)
    if result.get("meta_title"):
        p.meta_title = result["meta_title"][:70]
    if result.get("meta_description"):
        p.meta_description = result["meta_description"][:200]
    if result.get("meta_keywords"):
        p.meta_keywords = result["meta_keywords"]


# ══════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════
@router.post("/api/products/{product_id}/image")
def save_product_images(product_id: int, data: ProductImagesBody, request: Request, db: Session = Depends(get_db)):
    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Только администратор")

    from database import Product as _P
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    p.images = json.dumps(data.images, ensure_ascii=False)
    p.image_url = data.images[0] if data.images else None
    db.commit()
    return {"ok": True}


@router.get("/api/products/{product_id}/kaspi-image")
def fetch_kaspi_image(product_id: int, request: Request, db: Session = Depends(get_db)):
    """Достаёт картинку товара со страницы Kaspi по kaspi_sku (og:image)."""
    import requests as req_lib
    import re
    from database import Product as _P

    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404)
    if not p.kaspi_sku or "_" not in p.kaspi_sku:
        raise HTTPException(status_code=400, detail="Нет полного kaspi_sku (нужен формат productId_offerId)")

    sku = p.kaspi_sku.split(",")[0].strip()
    url = f"https://kaspi.kz/shop/p/-{sku}/"

    try:
        r = req_lib.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Kaspi вернул {r.status_code}")

        match = re.search(r'og:image.*?content="([^"]+)"', r.text)
        if not match:
            return {"ok": False, "detail": "og:image не найден на странице Kaspi", "images": []}

        image_url = match.group(1)

        # Сохраняем сразу
        p.images = json.dumps([image_url], ensure_ascii=False)
        p.image_url = image_url
        db.commit()

        return {"ok": True, "image_url": image_url, "images": [image_url]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки с Kaspi: {str(e)[:200]}")


@router.post("/api/admin/fetch-kaspi-images")
def fetch_kaspi_images_bulk(request: Request, db: Session = Depends(get_db)):
    """Массовая загрузка картинок с Kaspi для товаров без фото."""
    import requests as req_lib
    import re
    import time as _time
    from database import Product as _P

    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    products = db.query(_P).filter(
        _P.kaspi_sku.isnot(None),
        _P.kaspi_sku.contains("_"),
        (_P.images.is_(None)) | (_P.images == "") | (_P.images == "[]"),
        (_P.image_url.is_(None)) | (_P.image_url == ""),
    ).limit(50).all()

    filled = 0
    failed = 0
    for p in products:
        sku = p.kaspi_sku.split(",")[0].strip()
        url = f"https://kaspi.kz/shop/p/-{sku}/"
        try:
            r = req_lib.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            if r.status_code != 200:
                failed += 1
                continue
            match = re.search(r'og:image.*?content="([^"]+)"', r.text)
            if match:
                image_url = match.group(1)
                p.images = json.dumps([image_url], ensure_ascii=False)
                p.image_url = image_url
                filled += 1
            else:
                failed += 1
            _time.sleep(0.3)
        except Exception:
            failed += 1
            _time.sleep(0.5)

    db.commit()
    remaining = db.query(_P).filter(
        _P.kaspi_sku.isnot(None),
        (_P.images.is_(None)) | (_P.images == "") | (_P.images == "[]"),
    ).count()
    return {"filled": filled, "failed": failed, "remaining": remaining}


@router.get("/api/products/{product_id}/search-images")
def search_product_images(product_id: int, request: Request, db: Session = Depends(get_db)):
    """Ищет картинки через SerpApi (Google Images)."""
    import requests as req_lib
    from database import Product as _P

    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404)

    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="SERPAPI_KEY не задан")

    query = f"{p.brand or ''} {p.name}".strip()
    images = []

    try:
        r = req_lib.get("https://serpapi.com/search", params={
            "engine": "google_images",
            "q": query,
            "api_key": api_key,
            "num": 10,
            "hl": "ru",
            "gl": "kz",
        }, timeout=15)
        data = r.json()
        for item in data.get("images_results", []):
            link = item.get("original")
            if link:
                images.append(link)
    except Exception as e:
        print(f"[image search] ошибка: {e}")

    return {"images": images, "query": query}


@router.post("/api/products/{product_id}/ai-describe")
def ai_describe_product(product_id: int, db: Session = Depends(get_db)):
    """Генерирует описание + характеристики + SEO meta через OpenAI."""
    from database import Product as _P

    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")

    api_key = _get_openai_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY не настроен")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1024,
            messages=[{"role": "user", "content": _build_ai_prompt(p)}]
        )

        result = _parse_json_from_llm(response.choices[0].message.content)
        _apply_ai_result(p, result)
        db.commit()

        return {
            "ok": True,
            "description": p.description,
            "specs": json.loads(p.specs or "[]"),
            "meta_title": p.meta_title,
            "meta_description": p.meta_description,
            "meta_keywords": p.meta_keywords,
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI вернул некорректный JSON — попробуйте ещё раз")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка AI: {str(e)[:200]}")


@router.post("/api/admin/fill-descriptions")
async def fill_descriptions_bulk(request: Request, db: Session = Depends(get_db)):
    """Массовая генерация описаний/характеристик/SEO meta через OpenAI."""
    from database import Product as _P

    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    api_key = _get_openai_key(db)
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY не настроен")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    id_list = body.get("ids") if isinstance(body, dict) else None

    base_q = db.query(_P)
    if id_list:
        base_q = base_q.filter(_P.id.in_(id_list))
    else:
        base_q = base_q.filter((_P.description.is_(None)) | (_P.description == ""))
    products = base_q.all()

    done = 0
    errors = []
    for p in products:
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=1024,
                messages=[{"role": "user", "content": _build_ai_prompt(p)}]
            )
            result = _parse_json_from_llm(response.choices[0].message.content)
            _apply_ai_result(p, result)
            db.commit()
            done += 1
            time.sleep(0.3)
        except Exception as e:
            errors.append(f"{p.name}: {str(e)[:100]}")
            print(f"[fill-descriptions] ошибка {p.name}: {e}", flush=True)

    return {"ok": True, "done": done, "total": len(products), "errors": errors}


@router.post("/api/admin/fill-images")
def fill_images_bulk(request: Request, db: Session = Depends(get_db)):
    """Автоматически ищет и заполняет картинки для товаров без фото через SerpApi."""
    import requests as req_lib
    from database import Product as _P

    user = get_user_from_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)

    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="SERPAPI_KEY не задан")

    products = db.query(_P).filter(
        (_P.images.is_(None)) | (_P.images == "") | (_P.images == "[]")
    ).filter(
        (_P.image_url.is_(None)) | (_P.image_url == "")
    ).all()

    filled = 0
    skipped = 0
    errors = 0

    for p in products:
        try:
            query = f"{p.brand or ''} {p.name}".strip()
            r = req_lib.get("https://serpapi.com/search", params={
                "engine": "google_images", "q": query,
                "api_key": api_key, "num": 5, "hl": "ru", "gl": "kz",
            }, timeout=15)
            data = r.json()

            if "error" in data:
                print(f"[fill-images] SerpApi error: {data['error']}")
                errors += 1
                break

            items = data.get("images_results", [])
            if not items:
                skipped += 1
                continue

            links = [i["original"] for i in items if i.get("original")]
            if not links:
                skipped += 1
                continue

            p.images = json.dumps(links[:5], ensure_ascii=False)
            p.image_url = links[0]
            db.commit()
            filled += 1
            time.sleep(0.3)

        except Exception as e:
            print(f"[fill-images] ошибка для {p.name}: {e}", flush=True)
            errors += 1
            break

    return {
        "filled": filled,
        "skipped": skipped,
        "errors": errors,
        "remaining_without_images": db.query(_P).filter(
            (_P.images.is_(None)) | (_P.images == "") | (_P.images == "[]")
        ).count()
    }


@router.post("/api/ai/suggest")
def ai_suggest(data: AISuggestRequest):
    """AI подсказывает категорию, бренд, единицу и артикул по названию товара."""
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI не настроен")
    client = OpenAI(api_key=api_key)

    prompt = (
        f"Товар: «{data.name}»\n"
        "Это товар из строительной химии / инструментов / крепежа.\n"
        "Определи и верни JSON с полями:\n"
        "- category: одно из [Герметики, Пены монтажные, Дюбели и крепёж, Инструменты, Химия, Лента и скотч, Клей, Краски, Другое]\n"
        "- brand: бренд если есть в названии, иначе ''\n"
        "- unit: одно из [шт, кг, л, м, уп, рул]\n"
        "- sku_hint: короткий артикул латиницей (макс 15 символов), например TYT_SIL_280\n"
        "Верни только JSON без пояснений."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=200
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "category": result.get("category", "Другое"),
            "brand": result.get("brand", ""),
            "unit": result.get("unit", "шт"),
            "sku_hint": result.get("sku_hint", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
