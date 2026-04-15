"""Merge: слияние Kaspi-товаров со справочником накладных."""
import re
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
import crud

router = APIRouter(tags=["merge"])


STOPWORDS = {"и","в","на","с","для","из","по","шт","мл","л","кг","г","см","мм","м","гр","х","x","the","of","for","pcs"}


def _words(text):
    return {w for w in re.split(r'[\s\-_/,.()\[\]]+', (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def _match_score(a_words, b_words):
    if not a_words or not b_words:
        return 0
    common = a_words & b_words
    return len(common) / max(len(a_words), len(b_words))


def _extract_qty(text):
    """Возвращает (число_в_базовой_единице, единица) или None."""
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(кг|г|гр|kg|g|л|л\.|мл|ml|l|м|mm|см|cm|шт|pcs|pc)', (text or "").lower())
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit in ("кг", "kg"):
        return (val * 1000, "g")
    if unit in ("г", "гр", "g"):
        return (val, "g")
    if unit in ("л", "л.", "l"):
        return (val * 1000, "ml")
    if unit in ("мл", "ml"):
        return (val, "ml")
    return (val, unit)


def _qty_penalty(name_a, name_b):
    qa = _extract_qty(name_a)
    qb = _extract_qty(name_b)
    if qa is None or qb is None:
        return 1.0
    if qa[1] != qb[1]:
        return 1.0
    return 1.0 if abs(qa[0] - qb[0]) < 0.01 else 0.05


@router.get("/api/merge-preview")
def merge_preview(db: Session = Depends(get_db)):
    from database import Product as _P, PriceListItem
    kaspi_products = db.query(_P).filter(_P.category == "Kaspi").all()
    ref_items = db.query(PriceListItem).all()

    kaspi_words = {kp.id: _words(kp.name) for kp in kaspi_products}

    kaspi_by_sku = {kp.kaspi_sku.upper(): kp for kp in kaspi_products if kp.kaspi_sku}
    kaspi_by_barcode = {kp.barcode.upper(): kp for kp in kaspi_products if kp.barcode}
    kaspi_by_article = {kp.kaspi_article.upper(): kp for kp in kaspi_products if kp.kaspi_article}
    ref_by_id = {ri.id: ri for ri in ref_items}

    candidates = []
    for kp in kaspi_products:
        if kp.linked_ref_id and kp.linked_ref_id in ref_by_id:
            ri = ref_by_id[kp.linked_ref_id]
            candidates.append((2.0, "linked", kp, ri))

    for ri in ref_items:
        ri_article = (ri.article or "").upper()
        matched_kp = None
        if ri_article:
            matched_kp = (kaspi_by_article.get(ri_article)
                          or kaspi_by_sku.get(ri_article)
                          or kaspi_by_barcode.get(ri_article))
        if not matched_kp and ri_article and len(ri_article) >= 5:
            for kp in kaspi_products:
                if ri_article in kp.name.upper():
                    matched_kp = kp
                    break
        if matched_kp:
            penalty = _qty_penalty(ri.name, matched_kp.name)
            candidates.append((1.0 * penalty, "sku", matched_kp, ri))

    for ri in ref_items:
        ri_words = _words(ri.name)
        for kp in kaspi_products:
            score = _match_score(ri_words, kaspi_words[kp.id])
            if score >= 0.4:
                penalty = _qty_penalty(ri.name, kp.name)
                candidates.append((round(score * penalty, 3), "fuzzy", kp, ri))

    candidates.sort(key=lambda c: -c[0])

    used_kaspi = set()
    used_ref = set()
    pairs = []
    for score, match_type, kp, ri in candidates:
        if kp.id in used_kaspi or ri.id in used_ref:
            continue
        used_kaspi.add(kp.id)
        used_ref.add(ri.id)
        pairs.append({
            "kaspi_id": kp.id,
            "kaspi_name": kp.name,
            "kaspi_sku": kp.kaspi_sku,
            "kaspi_price": kp.price,
            "other_id": ri.id,
            "other_name": ri.name,
            "other_sku": ri.article or "",
            "other_supplier": ri.supplier or "",
            "other_cost_price": ri.cost_price,
            "match_type": match_type,
            "score": score,
        })

    matched_ref_ids = used_ref
    unmatched_ref = [
        {"id": ri.id, "name": ri.name, "sku": ri.article or "",
         "cost_price": ri.cost_price, "supplier": ri.supplier or ""}
        for ri in ref_items if ri.id not in matched_ref_ids
    ]

    pairs.sort(key=lambda p: (-int(p["match_type"] == "linked"), -int(p["match_type"] == "sku"), -p["score"]))

    kaspi_list = [{"id": kp.id, "name": kp.name, "kaspi_sku": kp.kaspi_sku,
                   "price": kp.price, "brand": kp.brand} for kp in kaspi_products]
    other_list = [{"id": ri.id, "name": ri.name, "sku": ri.article or "",
                   "cost_price": ri.cost_price, "supplier": ri.supplier or ""} for ri in ref_items]

    synced = [{"id": kp.id, "name": kp.name, "kaspi_sku": kp.kaspi_sku,
               "price": kp.price, "cost_price": kp.cost_price, "supplier": kp.supplier or "",
               "supplier_article": kp.supplier_article or ""}
              for kp in kaspi_products if kp.cost_price or kp.supplier]

    return {
        "pairs": pairs,
        "unmatched_other": unmatched_ref,
        "kaspi_list": kaspi_list,
        "other_list": other_list,
        "total_kaspi": len(kaspi_products),
        "total_other": len(ref_items),
        "synced": synced,
        "total_synced": len(synced),
    }


@router.post("/api/merge-confirm")
def merge_confirm(body: dict, db: Session = Depends(get_db)):
    """body: {"pairs": [{"kaspi_id": X, "other_id": Y}], "fields": [...], "force": bool}"""
    from database import Product as _P, PriceListItem
    selected = body.get("pairs", [])
    fields = set(body.get("fields", ["cost_price", "supplier", "supplier_article"]))

    def clean_article(val):
        if not val:
            return None
        for prefix in ("KSP_", "PL-"):
            if val.upper().startswith(prefix):
                return None
        return val

    force = body.get("force", False)
    merged = 0
    for pair in selected:
        kaspi_p = db.query(_P).filter(_P.id == pair["kaspi_id"]).first()
        ref_item = db.query(PriceListItem).filter(PriceListItem.id == pair["other_id"]).first()
        if not kaspi_p or not ref_item:
            continue

        if "name" in fields and ref_item.name:
            if force or not kaspi_p.name:
                kaspi_p.name = ref_item.name
        if "cost_price" in fields and ref_item.cost_price:
            if force or not kaspi_p.cost_price:
                kaspi_p.cost_price = ref_item.cost_price
        if "supplier" in fields and ref_item.supplier:
            if force or not kaspi_p.supplier:
                kaspi_p.supplier = ref_item.supplier
        if "supplier_article" in fields:
            article_val = clean_article(ref_item.article)
            if article_val and (force or not kaspi_p.supplier_article):
                kaspi_p.supplier_article = article_val

        kaspi_p.linked_ref_id = ref_item.id
        merged += 1
    db.commit()
    return {"merged": merged}


@router.post("/api/fill-brands")
def fill_brands(db: Session = Depends(get_db)):
    """Авто-заполнение бренда по названию товара для всех у кого бренд пустой."""
    from database import Product as _P
    filled = 0
    for p in db.query(_P).filter(_P.brand.is_(None)).all():
        brand = crud.detect_brand(p.name)
        if brand:
            p.brand = brand
            filled += 1
    db.commit()
    return {"filled": filled}


@router.get("/merge")
def merge_page():
    return FileResponse("static/merge.html")


@router.delete("/api/reset-nakladnye")
def reset_nakladnye(db: Session = Depends(get_db)):
    """Удаляет все товары категории Накладные и их движения."""
    from database import Product as _P, Movement
    products = db.query(_P).filter(_P.category == "Накладные").all()
    ids = [p.id for p in products]
    if ids:
        db.query(Movement).filter(Movement.product_id.in_(ids)).delete(synchronize_session=False)
        db.query(_P).filter(_P.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": len(ids)}
