"""Аналитика: overview, ABC, revenue, forecast (Holt+CI), admin stock forecast.

Вынесено из api.py как часть рефакторинга монолита на APIRouter-модули.
"""
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from helpers import parse_order_date, filter_orders_by_date

router = APIRouter(tags=["analytics"])


# ══════════════════════════════════════════════════════
# Helpers для прогноза спроса (только для этого модуля)
# ══════════════════════════════════════════════════════
def _holt_forecast(y, horizon=4, alpha=None, beta=None):
    """Holt's linear (double-exp) smoothing. Возвращает forecast + residual std.
    Если alpha/beta не заданы — грид-поиск по минимальному in-sample SSE."""
    import numpy as np
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 2:
        return np.full(horizon, y[-1] if n else 0), 0.0

    def _fit(a, b):
        L = np.zeros(n); T = np.zeros(n)
        L[0] = y[0]; T[0] = y[1] - y[0]
        for t in range(1, n):
            L[t] = a * y[t] + (1 - a) * (L[t-1] + T[t-1])
            T[t] = b * (L[t] - L[t-1]) + (1 - b) * T[t-1]
        resid = y - (L + T)
        return L, T, float(np.sum(resid ** 2)), float(np.std(resid))

    if alpha is None or beta is None:
        best = None
        for a in (0.1, 0.3, 0.5, 0.7, 0.9):
            for b in (0.05, 0.15, 0.3, 0.5):
                _, _, sse, _ = _fit(a, b)
                if best is None or sse < best[0]:
                    best = (sse, a, b)
        alpha, beta = best[1], best[2]

    L, T, _, std = _fit(alpha, beta)
    fc = np.array([max(0.0, L[-1] + (h + 1) * T[-1]) for h in range(horizon)])
    return fc, std


def _holdout_rmse(y, model_fn, h=4):
    """RMSE на holdout: последние h недель. Если данных мало — None."""
    import numpy as np
    if len(y) < h + 3:
        return None
    train, test = y[:-h], y[-h:]
    try:
        fc = model_fn(train, h)
        return float(np.sqrt(np.mean((np.asarray(fc) - test) ** 2)))
    except Exception:
        return None


# ══════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════
@router.get("/api/analytics/overview")
def analytics_overview(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Общая статистика по заказам + P&L"""
    from database import KaspiOrder, SiteSetting, Product as _P

    COMPLETED_STATES = {"Выдан", "ARCHIVE"}
    CANCELLED_STATES = {"Отменен", "CANCELLED", "Возврат"}

    all_rows = db.query(KaspiOrder).filter(
        KaspiOrder.state.in_(list(COMPLETED_STATES) + list(CANCELLED_STATES))
    ).all()
    rows = filter_orders_by_date(all_rows, date_from, date_to)

    total_orders = len(rows)
    completed = sum(1 for r in rows if r.state in COMPLETED_STATES)
    cancelled = sum(1 for r in rows if r.state in CANCELLED_STATES)

    total_revenue = sum(r.total or 0 for r in rows if r.state in COMPLETED_STATES)
    avg_order = int(total_revenue / completed) if completed else 0

    settings = {s.key: s.value for s in db.query(SiteSetting).all()}
    kaspi_commission_pct = float(settings.get("kaspi_commission_pct", "8"))
    tax_pct = float(settings.get("tax_pct", "4"))

    commission = int(total_revenue * kaspi_commission_pct / 100)
    tax = int(total_revenue * tax_pct / 100)

    cost_total = 0
    for r in rows:
        if r.state not in COMPLETED_STATES:
            continue
        if r.product_id and r.quantity:
            p_obj = db.query(_P).filter(_P.id == r.product_id).first()
            if p_obj and p_obj.cost_price:
                cost_total += int(r.quantity) * int(p_obj.cost_price)

    gross_profit = total_revenue - commission - tax - cost_total

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order": avg_order,
        "completed": completed,
        "cancelled": cancelled,
        "conversion_rate": round(completed / total_orders * 100, 1) if total_orders else 0,
        "commission": commission,
        "commission_pct": kaspi_commission_pct,
        "tax": tax,
        "tax_pct": tax_pct,
        "cost_total": cost_total,
        "gross_profit": gross_profit,
    }


@router.get("/api/analytics/abc")
def analytics_abc(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """ABC-анализ товаров по выручке"""
    from database import KaspiOrder
    from collections import defaultdict

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.product_name.isnot(None)).all()
    rows = filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    if not rows:
        return {"products": [], "total_revenue": 0}

    agg: dict = defaultdict(lambda: {"sku": "", "category": "", "revenue": 0, "qty": 0, "orders": 0})
    for r in rows:
        k = r.product_name
        agg[k]["sku"] = r.sku or ""
        agg[k]["category"] = r.category or ""
        agg[k]["revenue"] += r.total or 0
        agg[k]["qty"] += r.quantity or 1
        agg[k]["orders"] += 1

    items = sorted(
        [{"name": k, **v} for k, v in agg.items()],
        key=lambda x: x["revenue"], reverse=True
    )

    total_rev = sum(i["revenue"] for i in items)
    cumulative = 0
    for item in items:
        cumulative += item["revenue"]
        pct = cumulative / total_rev * 100
        item["abc"] = "A" if pct <= 80 else ("B" if pct <= 95 else "C")
        item["revenue_pct"] = round(item["revenue"] / total_rev * 100, 1)

    return {"products": items, "total_revenue": total_rev}


@router.get("/api/analytics/revenue")
def analytics_revenue(
    period: str = "month",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Выручка по периодам (month / week / day)"""
    from database import KaspiOrder
    from collections import defaultdict

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.order_date.isnot(None)).all()
    rows = filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )

    single_day = date_from and date_to and date_from == date_to

    by_period: dict = defaultdict(lambda: {"revenue": 0, "orders": 0})
    for r in rows:
        d = parse_order_date(r.order_date)
        if not d:
            continue
        if single_day:
            key = d.strftime("%Y-%m-%d %H:00")
        elif period == "day":
            key = d.strftime("%Y-%m-%d")
        elif period == "week":
            key = d.strftime("%Y-W%W")
        else:
            key = d.strftime("%Y-%m")
        by_period[key]["revenue"] += r.total or 0
        by_period[key]["orders"] += 1

    points = sorted([
        {"period": k, "revenue": v["revenue"], "orders": v["orders"]}
        for k, v in by_period.items()
    ], key=lambda x: x["period"])

    return {"points": points, "period": period}


@router.get("/api/analytics/forecast")
def analytics_forecast(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    lead_time: int = 14,
    db: Session = Depends(get_db)
):
    """Прогноз спроса: Holt smoothing + линейный тренд + сезонность + выбор
    лучшей модели по holdout RMSE + confidence interval."""
    import numpy as np
    from database import KaspiOrder, Product as _P, Movement as _M
    from collections import defaultdict
    from sqlalchemy import func
    import datetime

    COMPLETED = {"Выдан", "ARCHIVE"}
    all_rows = db.query(KaspiOrder).filter(KaspiOrder.product_name.isnot(None)).all()
    rows = filter_orders_by_date(
        [r for r in all_rows if r.state in COMPLETED], date_from, date_to
    )
    all_rows_full = [r for r in all_rows if r.state in COMPLETED]

    stocks_q = (
        db.query(_P.name, func.coalesce(func.sum(_M.quantity), 0))
        .outerjoin(_M, _M.product_id == _P.id)
        .group_by(_P.name)
        .all()
    )
    current_stock = {name: int(s) for name, s in stocks_q}

    by_product: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = parse_order_date(r.order_date)
        if not d:
            continue
        week = d.strftime("%Y-W%W")
        by_product[r.product_name][week] += r.quantity or 1

    full_by_product: dict = defaultdict(lambda: defaultdict(int))
    for r in all_rows_full:
        d = parse_order_date(r.order_date)
        if not d:
            continue
        week = d.strftime("%Y-W%W")
        full_by_product[r.product_name][week] += r.quantity or 1

    top_names = sorted(by_product.keys(), key=lambda n: sum(by_product[n].values()), reverse=True)[:20]

    now = datetime.date.today()
    cur_week_num = int(now.strftime("%W"))

    def _model_linear(train, h):
        x = np.arange(len(train))
        c = np.polyfit(x, train, 1)
        return [max(0.0, float(np.polyval(c, len(train) + i))) for i in range(h)]

    def _model_ma4(train, h):
        ma = float(np.mean(train[-4:])) if len(train) >= 4 else float(np.mean(train))
        return [max(0.0, ma) for _ in range(h)]

    def _model_holt(train, h):
        fc, _ = _holt_forecast(train, h)
        return fc.tolist()

    results = []
    for name in top_names:
        week_data = by_product[name]
        weeks = sorted(week_data.keys())
        if len(weeks) < 3:
            continue
        y = np.array([week_data[w] for w in weeks], dtype=float)

        candidates = {
            "linear": (_model_linear, _holdout_rmse(y, _model_linear)),
            "ma4":    (_model_ma4,    _holdout_rmse(y, _model_ma4)),
            "holt":   (_model_holt,   _holdout_rmse(y, _model_holt)),
        }
        scored = [(k, v[1]) for k, v in candidates.items() if v[1] is not None]
        if scored:
            best_name = min(scored, key=lambda t: t[1])[0]
        else:
            best_name = "holt"
        best_fn = candidates[best_name][0]

        _, residual_std = _holt_forecast(y, 4)
        forecast_raw = best_fn(y, 4)

        x = np.arange(len(y))
        coeffs = np.polyfit(x, y, 1)
        trend = float(coeffs[0])

        ma4 = float(np.mean(y[-4:])) if len(y) >= 4 else float(np.mean(y))

        full_data = full_by_product[name]
        seasonal_factor = 1.0
        seasonal_weeks_found = 0
        for offset in range(4):
            w_num = (cur_week_num + offset) % 52
            key_this = f"{now.year}-W{w_num:02d}"
            key_prev = f"{now.year - 1}-W{w_num:02d}"
            val_prev = full_data.get(key_prev, 0)
            val_this = full_data.get(key_this, 0)
            if val_prev > 0:
                seasonal_weeks_found += 1
                seasonal_factor += val_this / val_prev
        if seasonal_weeks_found > 0:
            seasonal_factor = seasonal_factor / seasonal_weeks_found
            seasonal_factor = max(0.3, min(3.0, seasonal_factor))

        forecast = [max(0, round(v * seasonal_factor)) for v in forecast_raw]
        ci_margin = 1.96 * residual_std * seasonal_factor
        forecast_low = [max(0, round(v - ci_margin)) for v in forecast]
        forecast_high = [round(v + ci_margin) for v in forecast]

        mean_y = float(np.mean(y)) if len(y) > 0 else 1
        std_y = float(np.std(y)) if len(y) > 1 else 0
        cv = round(std_y / mean_y, 2) if mean_y > 0 else 0

        stock = current_stock.get(name, 0)
        weekly_rate = ma4 * seasonal_factor if ma4 > 0 else 0
        days_left = round((stock / weekly_rate) * 7) if weekly_rate > 0 else None

        if days_left is not None and days_left < lead_time:
            recommendation = "urgent"
        elif days_left is not None and days_left < lead_time * 2:
            recommendation = "order"
        elif trend < -0.2 and cv < 0.5:
            recommendation = "watch"
        else:
            recommendation = "ok"

        order_qty = None
        if weekly_rate > 0 and recommendation in ("urgent", "order"):
            safety_stock = weekly_rate * max(0.5, cv) * 1.5
            target = weekly_rate * 4 + safety_stock
            order_qty = max(0, round(target - stock))

        results.append({
            "name": name,
            "weeks": weeks[-8:],
            "history": [int(week_data[w]) for w in weeks[-8:]],
            "forecast_4w": forecast,
            "forecast_low": forecast_low,
            "forecast_high": forecast_high,
            "model": best_name,
            "trend": round(trend, 2),
            "trend_dir": "up" if trend > 0.1 else ("down" if trend < -0.1 else "flat"),
            "ma4": round(ma4, 1),
            "seasonal_factor": round(seasonal_factor, 2),
            "volatility": cv,
            "volatility_label": "стабильный" if cv < 0.4 else ("умеренный" if cv < 0.8 else "непредсказуемый"),
            "stock": stock,
            "days_left": days_left,
            "recommendation": recommendation,
            "order_qty": order_qty,
            "lead_time": lead_time,
        })

    results.sort(key=lambda x: (
        0 if x["recommendation"] == "urgent" else
        1 if x["recommendation"] == "order" else
        2 if x["recommendation"] == "watch" else 3,
        -sum(x["history"])
    ))
    return {"products": results}


@router.get("/api/admin/forecast")
def stock_forecast(db: Session = Depends(get_db)):
    """Прогноз: когда закончится товар на основе движений за последние 30 дней"""
    from database import Product as _P, Movement as _M
    from sqlalchemy import func
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=30)

    sales = db.query(_M.product_id, func.sum(-_M.quantity).label("sold")) \
        .filter(_M.move_type.in_(["sale", "kaspi_sale"]), _M.created_at >= cutoff) \
        .group_by(_M.product_id).all()
    sales_map = {s.product_id: float(s.sold) for s in sales}

    stocks = db.query(_M.product_id, func.sum(_M.quantity).label("stock")) \
        .group_by(_M.product_id).all()
    stock_map = {s.product_id: max(float(s.stock), 0) for s in stocks}

    products = db.query(_P).filter(_P.category == "Kaspi").all()

    result = []
    for p in products:
        stock = stock_map.get(p.id, 0)
        sold_30d = sales_map.get(p.id, 0)
        daily_rate = sold_30d / 30 if sold_30d > 0 else 0

        if daily_rate > 0:
            days_left = stock / daily_rate
            status = "critical" if days_left < 7 else ("warning" if days_left < 14 else "ok")
        else:
            days_left = None
            status = "no_sales"

        result.append({
            "id": p.id,
            "name": p.name,
            "brand": p.brand or "",
            "stock": stock,
            "sold_30d": sold_30d,
            "daily_rate": round(daily_rate, 2),
            "days_left": round(days_left, 1) if days_left is not None else None,
            "status": status,
        })

    order = {"critical": 0, "warning": 1, "ok": 2, "no_sales": 3}
    result.sort(key=lambda x: (order[x["status"]], x["days_left"] if x["days_left"] is not None else 9999))
    return result
