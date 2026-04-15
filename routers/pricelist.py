"""Pricelist (справочник накладных): импорт XML, поиск, сравнение цен."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from helpers import save_upload

router = APIRouter(tags=["pricelist"])


@router.post("/api/pricelist/import")
async def pricelist_import(
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Импортирует XML в справочник накладных. Старые записи из того же файла заменяются."""
    import xml.etree.ElementTree as ET
    from database import PriceListItem

    if not file:
        raise HTTPException(status_code=400, detail="Файл не передан")

    content = await file.read()
    filename = file.filename or "unknown.xml"

    try:
        root = ET.fromstring(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга XML: {e}")

    товары = root.find("Товары")
    if товары is None:
        raise HTTPException(status_code=400, detail="Нет тега <Товары> в XML")

    existing = {
        (i.article or "", i.supplier or ""): i
        for i in db.query(PriceListItem).filter(PriceListItem.source_file == filename).all()
    }

    created = 0
    updated = 0
    seen_keys = set()

    for товар in товары.findall("Товар"):
        name_el    = товар.find("Наименование")
        price_el   = товар.find("ЦенаЗакупки")
        supplier_el= товар.find("Поставщик")
        article_el = товар.find("Артикул")
        unit_el    = товар.find("ЕдИзм")

        name     = (name_el.text or "").strip()    if name_el    is not None else ""
        supplier = (supplier_el.text or "").strip() if supplier_el is not None else ""
        article  = (article_el.text or "").strip() if article_el is not None else ""
        unit     = (unit_el.text or "шт").strip()  if unit_el    is not None else "шт"
        cost_raw = (price_el.text or "0").strip()  if price_el   is not None else "0"

        if not name:
            continue
        try:
            cost_price = int(float(cost_raw))
        except Exception:
            cost_price = None

        key = (article, supplier)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        if key in existing:
            item = existing[key]
            item.name = name
            item.cost_price = cost_price
            item.unit = unit
            item.is_new = False
            updated += 1
        else:
            db.add(PriceListItem(
                name=name,
                article=article or None,
                supplier=supplier or None,
                cost_price=cost_price,
                unit=unit,
                source_file=filename,
                is_new=True,
            ))
            created += 1

    removed = 0
    for key, item in existing.items():
        if key not in seen_keys:
            db.delete(item)
            removed += 1

    db.commit()
    save_upload(content, filename, "pricelist_ref", created + updated, db)
    return {"created": created, "updated": updated, "removed": removed, "file": filename}


@router.get("/api/pricelist/search")
def pricelist_search(q: str = "", supplier: str = "", limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """Поиск по справочнику накладных."""
    from database import PriceListItem
    from sqlalchemy import or_
    query = db.query(PriceListItem)
    if q:
        words = [w for w in q.strip().split() if w]
        for w in words:
            like = f"%{w}%"
            query = query.filter(or_(
                PriceListItem.name.ilike(like),
                PriceListItem.article.ilike(like),
            ))
    if supplier:
        query = query.filter(PriceListItem.supplier == supplier)
    total = query.count()
    items = query.order_by(PriceListItem.name).offset(offset).limit(limit).all()
    return {
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "article": i.article or "",
                "supplier": i.supplier or "",
                "cost_price": i.cost_price,
                "unit": i.unit,
                "source_file": i.source_file or "",
                "is_new": bool(i.is_new),
            }
            for i in items
        ],
        "total": total,
    }


@router.get("/api/pricelist/suppliers")
def pricelist_suppliers(db: Session = Depends(get_db)):
    from database import PriceListItem
    rows = db.query(PriceListItem.supplier).distinct().filter(PriceListItem.supplier.isnot(None)).all()
    return {"suppliers": sorted([r[0] for r in rows if r[0]])}


@router.get("/api/pricelist/price-check")
def pricelist_price_check(db: Session = Depends(get_db)):
    """Сравнение закупочных цен справочника с текущими ценами Kaspi-карточек."""
    from database import PriceListItem, Product as _P
    ref_items = db.query(PriceListItem).filter(PriceListItem.cost_price.isnot(None)).all()
    kaspi_products = db.query(_P).filter(_P.category == "Kaspi").all()

    kaspi_by_sku      = {p.kaspi_sku.upper(): p for p in kaspi_products if p.kaspi_sku}
    kaspi_by_article  = {p.kaspi_article.upper(): p for p in kaspi_products if p.kaspi_article}
    kaspi_by_barcode  = {p.barcode.upper(): p for p in kaspi_products if p.barcode}
    kaspi_by_sup_art  = {p.supplier_article.upper(): p for p in kaspi_products if p.supplier_article}

    rows = []
    for ri in ref_items:
        art = (ri.article or "").upper()
        kp = None
        if art:
            kp = kaspi_by_article.get(art) or kaspi_by_sku.get(art) or kaspi_by_barcode.get(art) or kaspi_by_sup_art.get(art)

        ref_price = ri.cost_price
        cur_price = kp.cost_price if kp else None
        if cur_price and ref_price:
            diff = round(ref_price - cur_price, 2)
            pct = round((ref_price - cur_price) / cur_price * 100, 1) if cur_price else None
        else:
            diff = None
            pct = None

        rows.append({
            "ref_id":      ri.id,
            "ref_name":    ri.name,
            "article":     ri.article or "",
            "supplier":    ri.supplier or "",
            "ref_price":   ref_price,
            "kaspi_id":    kp.id if kp else None,
            "kaspi_name":  kp.name if kp else None,
            "cur_price":   cur_price,
            "diff":        diff,
            "pct":         pct,
            "matched":     kp is not None,
        })

    rows.sort(key=lambda r: (r["matched"], -abs(r["diff"] or 0)))
    return {
        "rows": rows,
        "total": len(rows),
        "matched": sum(1 for r in rows if r["matched"]),
        "changed": sum(1 for r in rows if r["diff"] and r["diff"] != 0),
    }


@router.get("/api/pricelist/stats")
def pricelist_stats(db: Session = Depends(get_db)):
    from database import PriceListItem, UploadedFile
    from sqlalchemy import func
    total = db.query(PriceListItem).count()
    raw_file = db.query(PriceListItem.source_file, func.count()).group_by(PriceListItem.source_file).all()
    by_supplier = dict(db.query(PriceListItem.supplier, func.count()).group_by(PriceListItem.supplier).all())

    uploads = db.query(UploadedFile).filter(UploadedFile.file_type == "pricelist_ref").order_by(UploadedFile.uploaded_at.desc()).all()
    upload_dates = {}
    for u in uploads:
        if u.original_name not in upload_dates:
            upload_dates[u.original_name] = u.uploaded_at.strftime("%d.%m.%Y %H:%M") if u.uploaded_at else ""

    files = []
    for fname, cnt in raw_file:
        if fname:
            files.append({"name": fname, "count": cnt, "uploaded_at": upload_dates.get(fname, "")})

    return {"total": total, "files": files, "by_supplier": {k or "": v for k, v in by_supplier.items()}}


@router.delete("/api/pricelist/clear")
def pricelist_clear(source_file: str = "", db: Session = Depends(get_db)):
    from database import PriceListItem
    q = db.query(PriceListItem)
    if source_file:
        q = q.filter(PriceListItem.source_file == source_file)
    deleted = q.delete()
    db.commit()
    return {"deleted": deleted}


@router.get("/pricelist")
def pricelist_page():
    return FileResponse("static/pricelist.html")
