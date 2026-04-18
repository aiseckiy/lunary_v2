/**
 * nav.js — единая боковая навигация + группированные вкладки + мобильная нижняя панель.
 *
 * Структура (5 групп):
 *   📦 Каталог  · 🏭 Склад  · 💰 Продажи  · 📥 Данные  · ⚙️ Система
 *
 * Рендерит:
 *   - sidebar (#sidebar)       — десктоп
 *   - bottom-nav (#bottom-nav) — мобилка, 5 иконок под каждую группу
 *   - tab-bar в page-container — переключение между разделами внутри группы
 */
(function () {
  // Загрузка кастомной темы
  fetch('/api/admin/theme/css').then(r => r.text()).then(css => {
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }).catch(() => {});

  const path = window.location.pathname;

  // ── Группы вкладок (tab-bar внутри секции) ───────────────────
  const TAB_GROUPS = [
    {
      id: 'catalog',
      tabs: [
        { href: '/admin',            icon: '📦', label: 'Товары' },
        { href: '/admin/brands',     icon: '🏷️', label: 'Бренды' },
        { href: '/admin/categories', icon: '📂', label: 'Категории' },
      ]
    },
    {
      id: 'warehouse',
      tabs: [
        { href: '/admin/scanner',   icon: '📷', label: 'Сканер' },
        { href: '/admin/history',   icon: '📋', label: 'История' },
        { href: '/admin/audit',     icon: '📝', label: 'Инвентаризация' },
      ]
    },
    {
      id: 'sales',
      tabs: [
        { href: '/admin/kaspi',          icon: '🛒', label: 'Kaspi заказы' },
        { href: '/admin/shop-orders',    icon: '🛍️', label: 'Магазин заказы' },
        { href: '/admin/analytics',      icon: '📊', label: 'Аналитика' },
        { href: '/admin/export-preview', icon: '📤', label: 'Экспорт Kaspi' },
      ]
    },
    {
      id: 'data',
      tabs: [
        { href: '/admin/import-xlsx', icon: '📊', label: 'Импорт Excel' },
        { href: '/import',            icon: '📄', label: 'Импорт XML' },
        { href: '/pricelist',         icon: '🗂️', label: 'Накладные' },
        { href: '/merge',             icon: '🔀', label: 'Слияние' },
        { href: '/review',            icon: '✅', label: 'Ревью' },
        { href: '/uploads',           icon: '📁', label: 'Файлы' },
      ]
    },
    {
      id: 'system',
      tabs: [
        { href: '/admin/settings',  icon: '⚙️', label: 'Настройки' },
        { href: '/admin/theme',     icon: '🎨', label: 'Темы' },
        { href: '/admin/changelog', icon: '🚀', label: 'Обновления' },
        { href: '/admin/sitemap',   icon: '🗺️', label: 'Карта сайта' },
        { href: '/admin/bizmap',    icon: '🧭', label: 'Бизнес-процессы' },
      ]
    },
  ];

  // ── Навигационные группы (сайдбар) ───────────────────────────
  const GROUPS = [
    {
      id: 'catalog',
      label: 'Каталог',
      icon: '📦',
      links: [
        { href: '/admin',            icon: '📦', label: 'Товары' },
        { href: '/admin/brands',     icon: '🏷️', label: 'Бренды' },
        { href: '/admin/categories', icon: '📂', label: 'Категории' },
      ]
    },
    {
      id: 'warehouse',
      label: 'Склад',
      icon: '🏭',
      links: [
        { href: '/admin/scanner',  icon: '📷', label: 'Сканер' },
        { href: '/admin/history',  icon: '📋', label: 'История' },
        { href: '/admin/audit',    icon: '📝', label: 'Инвентаризация' },
      ]
    },
    {
      id: 'sales',
      label: 'Продажи',
      icon: '💰',
      links: [
        { href: '/admin/kaspi',          icon: '🛒', label: 'Kaspi заказы' },
        { href: '/admin/shop-orders',    icon: '🛍️', label: 'Магазин заказы', badge: 'orders-badge' },
        { href: '/admin/analytics',      icon: '📊', label: 'Аналитика' },
        { href: '/admin/export-preview', icon: '📤', label: 'Экспорт Kaspi' },
        { href: '/shop',                 icon: '🏪', label: 'Витрина' },
      ]
    },
    {
      id: 'data',
      label: 'Данные',
      icon: '📥',
      links: [
        { href: '/admin/import-xlsx', icon: '📊', label: 'Импорт Excel' },
        { href: '/import',            icon: '📄', label: 'Импорт XML' },
        { href: '/pricelist',         icon: '🗂️', label: 'Накладные' },
        { href: '/merge',             icon: '🔀', label: 'Слияние дублей' },
        { href: '/review',            icon: '✅', label: 'Ревью' },
        { href: '/uploads',           icon: '📁', label: 'Файлы' },
      ]
    },
    {
      id: 'system',
      label: 'Система',
      icon: '⚙️',
      admin_only: true,
      links: [
        { href: '/admin/settings',  icon: '⚙️', label: 'Настройки' },
        { href: '/admin/theme',     icon: '🎨', label: 'Темы' },
        { href: '/admin/changelog', icon: '🚀', label: 'Обновления' },
        { href: '/admin/sitemap',   icon: '🗺️', label: 'Карта сайта' },
        { href: '/admin/bizmap',    icon: '🧭', label: 'Бизнес-процессы' },
      ]
    },
  ];

  // ── Mobile bottom nav — 5 иконок под каждую группу ───────────
  // Href — первая ссылка внутри группы (дефолтная точка входа)
  const BNAV_LINKS = [
    { id: 'catalog',   href: '/admin',            icon: '📦', label: 'Каталог' },
    { id: 'warehouse', href: '/admin/scanner',    icon: '📷', label: 'Склад' },
    { id: 'sales',     href: '/admin/kaspi',      icon: '💰', label: 'Продажи' },
    { id: 'data',      href: '/admin/import-xlsx', icon: '📥', label: 'Данные' },
    { id: 'system',    href: '/admin/settings',   icon: '⚙️', label: 'Ещё' },
  ];

  function isActive(href) {
    if (href === '/admin') return path === '/admin';
    if (href === '/shop') return path === '/shop';
    return path.startsWith(href);
  }

  function currentGroupId() {
    for (const g of GROUPS) {
      for (const l of g.links) {
        if (isActive(l.href)) return g.id;
      }
    }
    return null;
  }

  function currentTabGroup() {
    const gid = currentGroupId();
    return TAB_GROUPS.find(g => g.id === gid) || null;
  }

  // ── Стили nav (инжектируем один раз) ─────────────────────────
  const navStyle = document.createElement('style');
  navStyle.textContent = `
    /* Tab-bar в sidebar-страницах */
    .nav-tab-bar {
      display: flex; align-items: center; gap: 2px;
      padding: 10px 0 0; margin-bottom: 18px;
      border-bottom: 2px solid var(--border, #e5e7eb);
      overflow-x: auto; scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
      flex-shrink: 0;
    }
    .nav-tab-bar::-webkit-scrollbar { display: none; }
    .nav-tab-bar a {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 7px 14px 9px;
      font-size: 13px; font-weight: var(--fw-medium, 500);
      color: var(--text2, #6b7280);
      text-decoration: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px; white-space: nowrap;
      border-radius: 6px 6px 0 0;
      transition: color .15s, background .15s;
    }
    .nav-tab-bar a:hover { color: var(--text, #111); background: #f3f4f6; }
    .nav-tab-bar a.active {
      color: var(--accent, #6366f1);
      border-bottom-color: var(--accent, #6366f1);
      font-weight: var(--fw-semibold, 600);
    }

    /* Tab-bar в standalone страницах (без sidebar) */
    .standalone-tab-bar {
      display: flex; align-items: center; gap: 2px;
      padding: 0 24px;
      background: var(--surface, #fff);
      border-bottom: 1px solid var(--border, #e5e7eb);
      overflow-x: auto; scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
    }
    .standalone-tab-bar::-webkit-scrollbar { display: none; }
    .standalone-tab-bar a {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 10px 14px;
      font-size: 13px; font-weight: var(--fw-medium, 500);
      color: var(--text2, #6b7280);
      text-decoration: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px; white-space: nowrap;
      transition: color .15s;
    }
    .standalone-tab-bar a:hover { color: var(--text, #111); }
    .standalone-tab-bar a.active {
      color: var(--accent, #6366f1);
      border-bottom-color: var(--accent, #6366f1);
      font-weight: var(--fw-semibold, 600);
    }
  `;
  document.head.appendChild(navStyle);

  // ── Рендер таб-бара ──────────────────────────────────────────
  function renderTabBar(group, className) {
    const div = document.createElement('nav');
    div.className = className;
    div.setAttribute('role', 'tablist');
    div.setAttribute('aria-label', 'Навигация по разделу');
    group.tabs.forEach(t => {
      const a = document.createElement('a');
      a.href = t.href;
      a.innerHTML = `<span>${t.icon}</span>${t.label}`;
      if (isActive(t.href)) {
        a.className = 'active';
        a.setAttribute('aria-current', 'page');
      }
      div.appendChild(a);
    });
    return div;
  }

  function injectTabs() {
    const group = currentTabGroup();
    if (!group || group.tabs.length <= 1) return;

    const pageContainer = document.querySelector('.page-container');
    if (pageContainer) {
      pageContainer.insertBefore(renderTabBar(group, 'nav-tab-bar'), pageContainer.firstChild);
      return;
    }
    const header = document.querySelector('.header');
    if (header) {
      header.insertAdjacentElement('afterend', renderTabBar(group, 'standalone-tab-bar'));
      return;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectTabs);
  } else {
    injectTabs();
  }

  // ── Сайдбар ──────────────────────────────────────────────────
  const sidebar = document.getElementById('sidebar');
  if (sidebar) {
    let userRole = 'admin';
    fetch('/api/auth/me').then(r => r.ok ? r.json() : null).then(me => {
      if (me) userRole = me.role;
      buildSidebar(userRole);
    }).catch(() => buildSidebar('admin'));

    function buildSidebar(role) {
      const isAdmin = role === 'admin';
      let html = `<div class="sidebar-logo">Lunary <span>OS</span></div>`;
      for (const group of GROUPS) {
        if (group.admin_only && !isAdmin) continue;
        html += `<div class="sidebar-section">${group.label}</div>`;
        for (const l of group.links) {
          const active = isActive(l.href) ? ' active' : '';
          const badgeHtml = l.badge
            ? `<span id="${l.badge}" style="display:none;background:#ef4444;color:#fff;border-radius:10px;font-size:11px;font-weight:700;padding:1px 7px;margin-left:4px"></span>`
            : '';
          html += `<a class="nav-link${active}" href="${l.href}">${l.icon} ${l.label}${badgeHtml}</a>`;
        }
      }
      html += `<div class="sidebar-spacer"></div><div class="sidebar-footer">Lunary OS v2</div>`;
      sidebar.innerHTML = html;
    }
  }

  // ── Нижняя панель (мобильная) — по 1 иконке на группу ────────
  const bottomNav = document.getElementById('bottom-nav');
  if (bottomNav) {
    const activeGroupId = currentGroupId();
    bottomNav.className = 'bottom-nav';
    bottomNav.innerHTML = `<div class="bottom-nav-inner">
      ${BNAV_LINKS.map(l => {
        const isActiveGroup = l.id === activeGroupId;
        return `<a class="bnav-item${isActiveGroup ? ' active' : ''}" href="${l.href}" data-group="${l.id}">
          <span style="font-size:20px;line-height:1">${l.icon}</span>
          <span style="font-size:10px;margin-top:3px">${l.label}</span>
        </a>`;
      }).join('')}
    </div>`;
  }
})();
