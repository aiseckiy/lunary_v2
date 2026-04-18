# Lunary OS — правила проекта для Claude

## Что это за проект
Система управления складом + Kaspi заказы для магазина строительных материалов в Казахстане.
Сайт: https://www.lunary.kz

## Стек
- **Backend**: FastAPI + SQLAlchemy + PostgreSQL (Railway)
- **Frontend**: Vanilla HTML/CSS/JS (без фреймворков), файлы в `static/`
- **AI**: OpenAI gpt-4o-mini
- **Деплой**: Railway, смотрит на ветку `main`
- **Навигация**: общий `static/nav.js` — сайдбар + таб-бары

## Правила кода

### Общие
- Коммитить и пушить только в `main` — Railway деплоит автоматически
- Не создавать отдельные ветки без явной просьбы
- Не добавлять комментарии и docstrings к коду который не менялся
- Не добавлять обработку ошибок для ситуаций которые не могут произойти

### Backend (api.py)
- Миграции колонок: добавлять в `new_columns` список в `database.py`, не через alembic
- Авторизация: `_is_staff(user)` = admin или manager, `_is_admin(user)` = только admin
- SEO страницы товаров рендерятся сервером в `shop_product_page()`

### Frontend (static/*.html)
- Все страницы используют `static/nav.js` для сайдбара и таб-баров
- Стили пишутся inline или в `<style>` тега внутри файла — нет общего CSS файла
- Переменные CSS: `--accent` (фиолетовый), `--green`, `--text`, `--surface`
- Toast уведомления: `toast('сообщение', 'success'|'error')`

## Архитектура навигации
4 группы с вкладками:
- **Склад**: /admin, /admin/history, /admin/analytics, /admin/scanner
- **Продажи**: /admin/kaspi, /admin/shop-orders, /shop
- **Данные**: /import, /admin/import-xlsx, /pricelist, /merge, /review, /uploads, /admin/export-preview
- **Система**: /admin/settings, /admin/theme, /admin/changelog, /admin/sitemap, /admin/bizmap

## Важные решения (почему так)
- `time.sleep(8)` в run.py — Railway убивает старый процесс при деплое, боту нужно время пока API поднимется
- Два AI SDK (openai + anthropic) — anthropic добавлен но не используется (кончились кредиты), всё идёт через OpenAI
- `show_in_shop` вместо `category == 'Kaspi'` — фильтр магазина, чтобы можно было показывать любые товары
- Standalone страницы (import, merge и др.) не имеют sidebar layout — они загружают nav.js в конце body
