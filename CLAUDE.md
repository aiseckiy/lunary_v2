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
- Все страницы подключают `static/components.js` + `static/nav.js` (в таком порядке)
- Стили пишутся inline или в `<style>` тега внутри файла — нет общего CSS файла
- Переменные CSS: `--accent` (фиолетовый), `--green`, `--text`, `--surface`
- **UI-компоненты**: `window.LX` (из components.js) — `LX.drawer()`, `LX.modal()`, `LX.confirm()`, `LX.dropdown()`, `LX.skeleton()`, `LX.empty()`, `LX.errorState()`, `LX.fab()`, `LX.toast()`, `LX.url.get/set()`
- На мобилке `LX.drawer` и `LX.dropdown` автоматически превращаются в bottom sheet
- Toast: предпочтительно `LX.toast()` (старый `toast()` работает через обратную совместимость)

## Архитектура навигации
5 групп в сайдбаре + 5 иконок в мобильном bottom nav:
- **📦 Каталог**: /admin · /admin/brands · /admin/categories
- **🏭 Склад**: /admin/scanner · /admin/history · /admin/audit
- **💰 Продажи**: /admin/kaspi · /admin/shop-orders · /admin/analytics · /admin/export-preview · /shop
- **📥 Данные**: /admin/import-xlsx · /import · /pricelist · /merge · /review · /uploads
- **⚙️ Система (admin only)**: /admin/settings · /admin/theme · /admin/changelog · /admin/sitemap · /admin/bizmap

UI-паттерны (утверждены):
- **Страница** — разделы верхнего уровня
- **Drawer справа (bottom sheet на мобилке)** — редактирование одной сущности
- **Модалка** — подтверждения / создание (использовать `LX.confirm()` для yes/no)
- **Dropdown по клику** — меню действий (`LX.dropdown`)
- **Inline edit** — быстрая правка в таблице
- **Toast** — уведомления успех/ошибка
- **FAB** — главное действие раздела на мобилке

## Важные решения (почему так)
- `time.sleep(8)` в run.py — Railway убивает старый процесс при деплое, боту нужно время пока API поднимется
- Два AI SDK (openai + anthropic) — anthropic добавлен но не используется (кончились кредиты), всё идёт через OpenAI
- `show_in_shop` вместо `category == 'Kaspi'` — фильтр магазина, чтобы можно было показывать любые товары
- Standalone страницы (import, merge и др.) не имеют sidebar layout — они загружают nav.js в конце body
