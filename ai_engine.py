"""
AI Engine — понимает свободный текст и вызывает нужные функции склада
"""
import os
import json
import logging
from datetime import datetime
from collections import defaultdict, deque
import crud
from database import SessionLocal

logger = logging.getLogger(__name__)

def _get_openai_client():
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY не задан")
    return OpenAI(api_key=key)

# Память разговора: user_id -> последние 20 сообщений
_conversation: dict = defaultdict(lambda: deque(maxlen=20))

# Журнал изменений: user_id -> список действий
_action_log: dict = defaultdict(list)

SYSTEM_PROMPT = (
    "Ты — умный помощник склада Lunary. "
    "Помогаешь управлять товарными остатками строительной химии (герметики, пены, краски). "
    "Понимаешь свободный текст на русском и вызываешь нужные функции. "
    "Помнишь контекст разговора — если пользователь говорит 'отмени' или 'это неправильно', "
    "ты понимаешь о чём речь из истории. "
    "После выполнения действий чётко сообщай что именно было изменено. "
    "Отвечай кратко и по делу на русском языке."
)


def get_user_log(user_id: int) -> str:
    """Возвращает последние действия пользователя"""
    actions = _action_log[user_id]
    if not actions:
        return "📋 Журнал пуст — изменений через AI ещё не было"
    lines = ["📋 *Последние действия AI:*\n"]
    for a in actions[-15:]:
        lines.append(f"`{a['time']}` {a['text']}")
    return "\n".join(lines)


def _log_action(user_id: int, text: str):
    """Записывает действие в журнал"""
    _action_log[user_id].append({
        "time": datetime.now().strftime("%d.%m %H:%M"),
        "text": text
    })

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "Узнать остаток товара на складе",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название, артикул или часть названия товара"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_movement",
            "description": "Записать движение товара: продажа, приход, списание или возврат",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название или артикул товара"},
                    "quantity": {"type": "integer", "description": "Количество (всегда положительное)"},
                    "move_type": {
                        "type": "string",
                        "enum": ["sale", "income", "writeoff", "return"],
                        "description": "Тип: sale=продажа, income=приход, writeoff=списание, return=возврат"
                    }
                },
                "required": ["query", "quantity", "move_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_stock",
            "description": "Установить точный остаток товара (когда говорят 'их всего X', 'установи X', 'сейчас X штук')",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Название или артикул товара"},
                    "quantity": {"type": "integer", "description": "Точное количество которое должно быть"}
                },
                "required": ["query", "quantity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock",
            "description": "Показать товары с низким остатком, что нужно закупить",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "Статистика продаж и склада за период",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Найти товары по запросу",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"]
            }
        }
    }
]


def _exec_get_stock(query: str, db) -> str:
    products = crud.find_product(query, db)
    if not products:
        return f"Товар '{query}' не найден"
    lines = []
    for p in products[:5]:
        stock = crud.get_stock(p.id, db)
        icon = "🔴" if stock <= p.min_stock else "🟢"
        lines.append(f"{icon} {p.name}: *{stock} {p.unit}*")
    return "\n".join(lines)


def _exec_add_movement(query: str, quantity: int, move_type: str, db, user_id: int = 0) -> str:
    products = crud.find_product(query, db)
    if not products:
        return f"Товар '{query}' не найден"

    labels = {"sale": "Продажа", "income": "Приход", "writeoff": "Списание", "return": "Возврат"}
    icons = {"sale": "🛒", "income": "📦", "writeoff": "🗑", "return": "↩️"}
    results = []

    for p in products[:3]:
        crud.add_movement(p.id, quantity, move_type, db, source="ai_bot")
        new_stock = crud.get_stock(p.id, db)
        icon = "🔴" if new_stock <= p.min_stock else "🟢"
        warn = " ⚠️ Мало, закупи!" if new_stock <= p.min_stock else ""
        result_line = (
            f"{icons[move_type]} {labels[move_type]}: {p.name}\n"
            f"   {quantity} {p.unit} → остаток: {icon} *{new_stock}*{warn}"
        )
        results.append(result_line)
        # Записываем в журнал
        _log_action(user_id, f"{icons[move_type]} {labels[move_type]} {quantity} шт — {p.name} (остаток: {new_stock})")

    return "\n\n".join(results)


def _exec_set_stock(query: str, quantity: int, db, user_id: int = 0) -> str:
    products = crud.find_product(query, db)
    if not products:
        return f"Товар '{query}' не найден"

    p = products[0]
    current = crud.get_stock(p.id, db)
    diff = quantity - current
    if diff == 0:
        return f"✅ {p.name}: остаток уже {quantity} шт, ничего не изменилось"

    move_type = "adjustment"
    crud.add_movement(p.id, diff, move_type, db, source="ai_bot", note=f"корректировка: {current}→{quantity}")
    _log_action(user_id, f"✏️ Корректировка: {p.name} {current}→{quantity} шт")
    icon = "🔴" if quantity <= p.min_stock else "🟢"
    return f"✏️ Скорректировано: {p.name}\n   Было: {current} → Стало: {icon} *{quantity} шт*"


def _exec_get_low_stock(db) -> str:
    items = crud.get_low_stock_products(db)
    if not items:
        return "✅ Всё в норме, низких остатков нет"
    lines = ["⚠️ *Нужно закупить:*\n"]
    for p, stock in items[:10]:
        need = max(p.min_stock - stock + 10, 10)
        lines.append(f"🔴 {p.name}\n   Осталось: {stock} | Докупи: ~{need} {p.unit}")
    return "\n\n".join(lines)


def _exec_get_stats(db) -> str:
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from database import Movement

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0)
    week = now - timedelta(days=7)

    today_sales = abs(db.query(func.sum(Movement.quantity)).filter(
        Movement.type == "sale", Movement.created_at >= today
    ).scalar() or 0)

    week_sales = abs(db.query(func.sum(Movement.quantity)).filter(
        Movement.type == "sale", Movement.created_at >= week
    ).scalar() or 0)

    all_stocks = crud.get_all_stocks(db)
    total_units = sum(s["stock"] for s in all_stocks)
    low = sum(1 for s in all_stocks if s["stock"] <= s["product"].min_stock)

    return (
        f"📊 *Статистика:*\n\n"
        f"Сегодня продано: *{today_sales} шт*\n"
        f"За 7 дней: *{week_sales} шт*\n\n"
        f"На складе: *{total_units} шт* ({len(all_stocks)} позиций)\n"
        f"Заканчивается: *{low}* позиций"
    )


def _exec_search(query: str, db) -> str:
    products = crud.find_product(query, db)
    if not products:
        return f"По запросу '{query}' ничего не найдено"
    lines = [f"🔍 Найдено {len(products)}:\n"]
    for p in products[:8]:
        stock = crud.get_stock(p.id, db)
        icon = "🔴" if stock <= p.min_stock else "🟢"
        lines.append(f"{icon} {p.name} — *{stock} {p.unit}* | `{p.sku}`")
    return "\n".join(lines)


async def process_ai_message(text: str, user_id: int = 0) -> str:
    """Главная функция — принимает текст, возвращает ответ"""
    db = SessionLocal()
    try:
        # Строим историю: системный промпт + предыдущие сообщения + новое
        history = list(_conversation[user_id])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": text}]

        # Первый вызов — AI определяет что делать
        client = _get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )

        msg = response.choices[0].message

        # Если AI не вызвал функцию — просто отвечает текстом
        if not msg.tool_calls:
            return msg.content

        # Выполняем все вызовы функций и собираем результаты
        messages.append(msg)
        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "get_stock":
                result = _exec_get_stock(args["query"], db)
            elif name == "add_movement":
                result = _exec_add_movement(args["query"], args["quantity"], args["move_type"], db, user_id)
            elif name == "set_stock":
                result = _exec_set_stock(args["query"], args["quantity"], db, user_id)
            elif name == "get_low_stock":
                result = _exec_get_low_stock(db)
            elif name == "get_stats":
                result = _exec_get_stats(db)
            elif name == "search_products":
                result = _exec_search(args["query"], db)
            else:
                result = "Неизвестная функция"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })

        # Второй вызов — AI формулирует итоговый ответ по результатам функций
        final = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        reply = final.choices[0].message.content

        # Сохраняем диалог в историю
        _conversation[user_id].append({"role": "user", "content": text})
        _conversation[user_id].append({"role": "assistant", "content": reply})

        return reply

    except Exception as e:
        logger.error(f"AI error: {e}")
        return "⚠️ AI временно недоступен. Используй команды: /list, /low, /stats"
    finally:
        db.close()
