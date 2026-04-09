from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os

from database import get_db, init_db
import crud

app = FastAPI(title="Lunary OS", version="1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Pydantic схемы ──────────────────────────────────────────
class ProductCreate(BaseModel):
    name: str
    sku: str
    barcode: Optional[str] = None
    category: str = "Общее"
    unit: str = "шт"
    min_stock: int = 5
    brand: Optional[str] = None  # если пусто — определяется автоматически


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    min_stock: Optional[int] = None
    brand: Optional[str] = None


class MovementCreate(BaseModel):
    product_id: int
    quantity: int
    type: str  # income, sale, writeoff, return
    source: str = "manual"
    note: Optional[str] = None


class StockAdjust(BaseModel):
    quantity: int
    type: str
    source: str = "manual"
    note: Optional[str] = None


# ─── Startup ─────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()


# ─── Товары ──────────────────────────────────────────────────
@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    stocks = crud.get_all_stocks(db)
    return [
        {
            "id": s["product"].id,
            "name": s["product"].name,
            "sku": s["product"].sku,
            "barcode": s["product"].barcode,
            "category": s["product"].category,
            "unit": s["product"].unit,
            "min_stock": s["product"].min_stock,
            "stock": s["stock"],
            "low": s["stock"] <= s["product"].min_stock,
            "brand": s["product"].brand or ""
        }
        for s in stocks
    ]


@app.post("/api/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    from database import Product as _P
    sku = data.sku.upper()
    if db.query(_P).filter(_P.sku == sku).first():
        raise HTTPException(status_code=409, detail="Артикул уже занят")
    p = crud.create_product(
        name=data.name, sku=sku, db=db,
        barcode=data.barcode, category=data.category,
        unit=data.unit, min_stock=data.min_stock, brand=data.brand
    )
    return {"id": p.id, "name": p.name, "sku": p.sku}


@app.get("/api/products/search")
def search_products(q: str, db: Session = Depends(get_db)):
    products = crud.find_product(q, db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "barcode": p.barcode,
            "stock": crud.get_stock(p.id, db),
            "unit": p.unit
        }
        for p in products
    ]


@app.get("/api/products/barcode/{barcode}")
def get_by_barcode(barcode: str, db: Session = Depends(get_db)):
    p = crud.get_product_by_barcode(barcode, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return {
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "barcode": p.barcode,
        "stock": crud.get_stock(p.id, db),
        "unit": p.unit,
        "min_stock": p.min_stock
    }


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    from database import Product as _P, Movement as _M
    p = db.query(_P).filter(_P.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    db.query(_M).filter(_M.product_id == product_id).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.put("/api/products/{product_id}")
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if "sku" in updates:
        updates["sku"] = updates["sku"].upper()
    p = crud.update_product(product_id, db, **updates)
    return {"id": p.id, "name": p.name, "sku": p.sku}


@app.get("/api/products/{product_id}/stock")
def get_stock(product_id: int, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    stock = crud.get_stock(product_id, db)
    return {"product_id": product_id, "name": p.name, "stock": stock, "unit": p.unit}


@app.post("/api/products/{product_id}/movement")
def add_movement(product_id: int, data: StockAdjust, db: Session = Depends(get_db)):
    p = crud.get_product_by_id(product_id, db)
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")

    if data.type not in ("income", "sale", "writeoff", "return", "adjustment"):
        raise HTTPException(status_code=400, detail="Неверный тип движения")

    m = crud.add_movement(product_id, data.quantity, data.type, db, data.source, data.note)
    new_stock = crud.get_stock(product_id, db)
    return {
        "movement_id": m.id,
        "product": p.name,
        "type": data.type,
        "quantity": data.quantity,
        "new_stock": new_stock
    }


@app.get("/api/products/{product_id}/history")
def get_history(product_id: int, db: Session = Depends(get_db)):
    movements = crud.get_movements(product_id, db)
    p = crud.get_product_by_id(product_id, db)
    type_labels = {
        "income": "📦 Приход",
        "sale": "🛒 Продажа",
        "writeoff": "🗑 Списание",
        "return": "↩️ Возврат",
        "adjustment": "✏️ Корректировка"
    }
    return [
        {
            "id": m.id,
            "type": m.type,
            "type_label": type_labels.get(m.type, m.type),
            "quantity": m.quantity,
            "source": m.source,
            "note": m.note,
            "date": m.created_at.strftime("%d.%m.%Y %H:%M")
        }
        for m in movements
    ]


# ─── Журнал всех операций ────────────────────────────────────
@app.get("/api/history")
def get_all_history(
    limit: int = 100,
    offset: int = 0,
    type: Optional[str] = None,
    source: Optional[str] = None,
    product_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    from database import Movement, Product
    from sqlalchemy import desc

    q = db.query(Movement, Product).join(Product, Movement.product_id == Product.id)
    if type:
        q = q.filter(Movement.type == type)
    if source:
        q = q.filter(Movement.source == source)
    if product_id:
        q = q.filter(Movement.product_id == product_id)

    total = q.count()
    rows = q.order_by(desc(Movement.created_at)).offset(offset).limit(limit).all()

    type_labels = {"income": "Приход", "sale": "Продажа", "writeoff": "Списание",
                   "return": "Возврат", "adjustment": "Корректировка"}
    return {
        "total": total,
        "items": [
            {
                "id": m.id,
                "product_id": p.id,
                "product_name": p.name,
                "product_sku": p.sku,
                "type": m.type,
                "type_label": type_labels.get(m.type, m.type),
                "quantity": m.quantity,
                "source": m.source or "manual",
                "note": m.note or "",
                "date": m.created_at.strftime("%d.%m.%Y"),
                "time": m.created_at.strftime("%H:%M"),
                "datetime_iso": m.created_at.isoformat(),
            }
            for m, p in rows
        ]
    }


@app.delete("/api/history/{movement_id}")
def delete_movement(movement_id: int, db: Session = Depends(get_db)):
    from database import Movement
    m = db.query(Movement).filter(Movement.id == movement_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ─── Алерты ──────────────────────────────────────────────────
@app.get("/api/alerts/low-stock")
def low_stock(db: Session = Depends(get_db)):
    items = crud.get_low_stock_products(db)
    return [
        {"id": p.id, "name": p.name, "stock": stock, "min_stock": p.min_stock}
        for p, stock in items
    ]


# ─── Дашборд ─────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("static/index.html") as f:
        return f.read()


@app.get("/scanner", response_class=HTMLResponse)
def scanner():
    with open("static/scanner.html") as f:
        return f.read()


@app.get("/history", response_class=HTMLResponse)
def history_page():
    with open("static/history.html") as f:
        return f.read()
