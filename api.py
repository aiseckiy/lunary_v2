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
            "low": s["stock"] <= s["product"].min_stock
        }
        for s in stocks
    ]


@app.post("/api/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    p = crud.create_product(
        name=data.name, sku=data.sku, db=db,
        barcode=data.barcode, category=data.category,
        unit=data.unit, min_stock=data.min_stock
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
