"""SEO server-rendered: /shop/product/{id} с meta, /robots.txt, /sitemap.xml.

Всё что видят боты поисковиков и шарилки соцсетей. Рендерится сервером
чтобы meta/OG/schema.org приехали ещё до JS.
"""
import html as _html
import json as _json
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db

router = APIRouter(tags=["seo"])

BASE_URL = "https://www.lunary.kz"


@router.get("/shop/product/{product_id}", response_class=HTMLResponse)
def shop_product_page(product_id: int, db: Session = Depends(get_db)):
    """Серверный рендер страницы товара с полными meta-тегами и schema.org."""
    from database import Product as _P, Movement as _M

    row = (
        db.query(_P, func.coalesce(func.sum(_M.quantity), 0).label("stock"))
        .outerjoin(_M, _M.product_id == _P.id)
        .filter(_P.id == product_id, _P.category == "Kaspi")
        .group_by(_P.id)
        .first()
    )

    with open("static/product.html", encoding="utf-8") as f:
        tmpl = f.read()

    if not row:
        return tmpl

    p, stock = row
    name = _html.escape(p.name or "Товар")
    brand = _html.escape(p.brand or "")
    canon = f"{BASE_URL}/shop/product/{product_id}"
    avail = "https://schema.org/InStock" if stock > 0 else "https://schema.org/OutOfStock"
    price_str = str(int(p.price)) if p.price else ""

    # SEO meta: используем заполненные AI или авто-генерация
    title = _html.escape(p.meta_title.strip()) if p.meta_title and p.meta_title.strip() \
        else f"{name} купить в Алматы | LUNARY"

    desc_raw = (p.meta_description or "").strip()
    if not desc_raw:
        base = (p.description or f"{brand} {name}").strip()
        price_part = f" Цена {int(p.price):,} ₸.".replace(",", " ") if p.price else ""
        desc_raw = base[:130] + price_part if price_part else base[:155]
    description = _html.escape(desc_raw[:165])

    keywords_raw = (p.meta_keywords or "").strip()
    if not keywords_raw:
        parts = [p.name or ""]
        if p.brand:
            parts.append(p.brand)
        if p.category:
            parts.append(p.category)
        parts += ["купить", "цена", "Алматы", "Казахстан", "LUNARY"]
        keywords_raw = ", ".join(parts)
    keywords = _html.escape(keywords_raw[:300])

    image = p.image_url or f"{BASE_URL}/static/og-default.jpg"

    schema = ""
    if p.price:
        # description с fallback: meta_description → description → auto-gen
        desc = (p.meta_description or p.description or "").strip()
        if not desc:
            parts = []
            if p.brand:
                parts.append(p.brand)
            if p.name:
                parts.append(p.name)
            base = " ".join(parts) or "Товар"
            desc = f"{base}. Купить в Алматы, доставка по Казахстану. Цена {int(p.price):,} ₸.".replace(",", " ")
        desc = desc[:500]

        # Доставка: бесплатно от 5000 ₸, иначе 1000 ₸ (Алматы)
        free_threshold = 5000
        shipping_rate = 0 if p.price >= free_threshold else 1000

        shipping_details = {
            "@type": "OfferShippingDetails",
            "shippingRate": {
                "@type": "MonetaryAmount",
                "value": str(shipping_rate),
                "currency": "KZT",
            },
            "shippingDestination": {
                "@type": "DefinedRegion",
                "addressCountry": "KZ",
                "addressRegion": "Алматы",
            },
            "deliveryTime": {
                "@type": "ShippingDeliveryTime",
                "handlingTime": {
                    "@type": "QuantitativeValue",
                    "minValue": 0,
                    "maxValue": 1,
                    "unitCode": "DAY",
                },
                "transitTime": {
                    "@type": "QuantitativeValue",
                    "minValue": 1,
                    "maxValue": 2,
                    "unitCode": "DAY",
                },
            },
        }

        # Политика возврата: 14 дней, Алматы, бесплатный возврат
        return_policy = {
            "@type": "MerchantReturnPolicy",
            "applicableCountry": "KZ",
            "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
            "merchantReturnDays": 14,
            "returnMethod": "https://schema.org/ReturnByMail",
            "returnFees": "https://schema.org/FreeReturn",
        }

        schema_data = {
            "@context": "https://schema.org/",
            "@type": "Product",
            "name": p.name or "",
            "brand": {"@type": "Brand", "name": p.brand or ""},
            "description": desc,
            "image": image,
            "url": canon,
            "offers": {
                "@type": "Offer",
                "priceCurrency": "KZT",
                "price": price_str,
                "availability": avail,
                "seller": {"@type": "Organization", "name": "LUNARY"},
                "shippingDetails": shipping_details,
                "hasMerchantReturnPolicy": return_policy,
            },
        }
        schema = f'<script type="application/ld+json">{_json.dumps(schema_data, ensure_ascii=False)}</script>'

    seo_head = f"""<title>{title}</title>
<meta name="description" content="{description}">
<meta name="keywords" content="{keywords}">
<link rel="canonical" href="{canon}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="product">
<meta property="og:url" content="{canon}">
<meta property="og:image" content="{_html.escape(image)}">
<meta property="og:site_name" content="LUNARY">
<meta property="product:price:amount" content="{price_str}">
<meta property="product:price:currency" content="KZT">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{_html.escape(image)}">
{schema}"""

    tmpl = tmpl.replace("<title>Товар — LUNARY</title>", seo_head, 1)
    return tmpl


@router.get("/robots.txt")
def robots_txt():
    content = """User-agent: *
Allow: /shop
Allow: /shop/product/
Disallow: /admin
Disallow: /api/
Disallow: /import
Disallow: /pricelist
Disallow: /merge
Disallow: /review
Disallow: /uploads
Disallow: /login

Sitemap: https://www.lunary.kz/sitemap.xml
"""
    return PlainTextResponse(content)


@router.get("/sitemap.xml")
def sitemap_xml(db: Session = Depends(get_db)):
    from database import Product as _P

    today = datetime.utcnow().strftime("%Y-%m-%d")

    products = db.query(_P).filter(_P.show_in_shop == True, _P.price.isnot(None)).all()  # noqa: E712

    urls = [f"""  <url>
    <loc>{BASE_URL}/shop</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
    <lastmod>{today}</lastmod>
  </url>"""]

    for p in products:
        images = []
        try:
            imgs = _json.loads(p.images or "[]")
            if isinstance(imgs, list):
                images = [i for i in imgs if isinstance(i, str) and i.startswith("http")]
        except Exception:
            pass
        if not images and p.image_url and p.image_url.startswith("http"):
            images = [p.image_url]

        image_tags = ""
        for img_url in images[:5]:
            name_escaped = (p.name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            image_tags += f"""
    <image:image>
      <image:loc>{img_url}</image:loc>
      <image:title>{name_escaped}</image:title>
    </image:image>"""

        urls.append(f"""  <url>
    <loc>{BASE_URL}/shop/product/{p.id}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <lastmod>{today}</lastmod>{image_tags}
  </url>""")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
    xml += '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
    xml += '\n'.join(urls)
    xml += '\n</urlset>'

    return Response(content=xml, media_type="application/xml")
