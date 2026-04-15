/**
 * nav.js — единая боковая навигация + группированные вкладки для всех страниц.
 */
(function () {
  fetch('/api/admin/theme/css').then(r => r.text()).then(css => {
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }).catch(() => {});

  const path = window.location.pathname;

  // ── Группы вкладок (переключение внутри секции) ──────────────
  const TAB_GROUPS = [
    {
      id: 'warehouse',
      tabs: [
        { href: '/admin',           icon: '📦', label: 'Товары' },
        { href: '/admin/history',   icon: '📋', label: 'История' },
        { href: '/admin/analytics', icon: '📊', label: 'Аналитика' },
        { href: '/admin/scanner',   icon: '📷', label: 'Сканер' },
      ]
    },
    {
      id: 'sales',
      tabs: [
        { href: '/admin/kaspi',       icon: '🛒', label: 'Kaspi заказы' },
        { href: '/admin/shop-orders', icon: '🛍️', label: 'Заказы магазина' },
        { href: '/shop',              icon: '🏪', label: 'Магазин' },
      ]
    },
    {
      id: 'data',
      tabs: [
        { href: '/import',                icon: '📥', label: 'Импорт XML' },
        { href: '/admin/import-xlsx',     icon: '📊', label: 'Импорт Excel' },
        { href: '/pricelist',             icon: '🗂️', label: 'Накладные' },
        { href: '/merge',                 icon: '🔀', label: 'Слияние' },
        { href: '/review',                icon: '✅', label: 'Проверка' },
        { href: '/uploads',               icon: '📁', label: 'Файлы' },
        { href: '/admin/export-preview',  icon: '🔍', label: 'Проверка XML' },
      ]
    },
    {
      id: 'system',
      tabs: [
        { href: '/admin/settings',  icon: '⚙️', label: 'Настройки' },
        { href: '/admin/theme',     icon: '🎨', label: 'Тема' },
        { href: '/admin/changelog', icon: '🚀', label: 'Обновления' },
        { href: '/admin/sitemap',   icon: '🗺️', label: 'Карта сайта' },
        { href: '/admin/bizmap',    icon: '🧭', label: 'Бизнес-процессы' },
      ]
    },
  ];

  // ── Навигационные группы (сайдбар) ───────────────────────────
  const GROUPS = [
    {
      label: 'Склад',
      links: [
        { href: '/admin',           icon: '📦', label: 'Товары' },
        { href: '/admin/scanner',   icon: '📷', label: 'Сканер' },
        { href: '/admin/history',   icon: '📋', label: 'История' },
        { href: '/admin/analytics', icon: '📊', label: 'Аналитика' },
      ]
    },
    {
      label: 'Продажи',
      links: [
        { href: '/admin/kaspi',       icon: '🛒', label: 'Kaspi заказы' },
        { href: '/admin/shop-orders', icon: '🛍️', label: 'Заказы магазина', badge: 'orders-badge' },
        { href: '/shop',              icon: '🏪', label: 'Магазин' },
      ]
    },
    {
      label: 'Импорт и данные',
      links: [
        { href: '/import',           icon: '📥', label: 'Импорт XML' },
        { href: '/admin/import-xlsx', icon: '📊', label: 'Импорт Excel' },
        { href: '/pricelist', icon: '🗂️', label: 'Накладные' },
        { href: '/merge',     icon: '🔀', label: 'Слияние товаров' },
        { href: '/review',    icon: '✅', label: 'Проверка' },
        { href: '/uploads',   icon: '📁', label: 'Файлы' },
        { href: '/admin/export-preview', icon: '🔍', label: 'Проверка XML' },
      ]
    },
    {
      label: 'Система',
      links: [
        { href: '/admin/settings',  icon: '⚙️', label: 'Настройки' },
        { href: '/admin/theme',     icon: '🎨', label: 'Тема' },
        { href: '/admin/changelog', icon: '🚀', label: 'Обновления' },
        { href: '/admin/sitemap',   icon: '🗺️', label: 'Карта сайта' },
        { href: '/admin/bizmap',    icon: '🧭', label: 'Бизнес-процессы' },
      ]
    },
  ];

  function isActive(href) {
    if (href === '/admin') return path === '/admin';
    return path.startsWith(href);
  }

  function currentTabGroup() {
    for (const g of TAB_GROUPS) {
      for (const t of g.tabs) {
        if (isActive(t.href)) return g;
      }
    }
    return null;
  }

  // ── Стили таб-бара (инжектируем один раз) ────────────────────
  const tabStyle = document.createElement('style');
  tabStyle.textContent = `
    .nav-tab-bar {
      display: flex;
      align-items: center;
      gap: 2px;
      padding: 10px 0 0;
      margin-bottom: 18px;
      border-bottom: 2px solid #e5e7eb;
      overflow-x: auto;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
      flex-shrink: 0;
    }
    .nav-tab-bar::-webkit-scrollbar { display: none; }
    .nav-tab-bar a {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 14px 9px;
      font-size: 13px;
      font-weight: 500;
      color: #6b7280;
      text-decoration: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      white-space: nowrap;
      border-radius: 6px 6px 0 0;
      transition: color .15s, background .15s;
    }
    .nav-tab-bar a:hover {
      color: #111827;
      background: #f3f4f6;
    }
    .nav-tab-bar a.active {
      color: var(--accent, #6c63ff);
      border-bottom-color: var(--accent, #6c63ff);
      font-weight: 600;
    }

    /* Для standalone страниц (без sidebar) — таб-бар в header */
    .standalone-tab-bar {
      display: flex;
      align-items: center;
      gap: 2px;
      padding: 0 24px;
      background: #fff;
      border-bottom: 1px solid #e5e7eb;
      overflow-x: auto;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
    }
    .standalone-tab-bar::-webkit-scrollbar { display: none; }
    .standalone-tab-bar a {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 500;
      color: #6b7280;
      text-decoration: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      white-space: nowrap;
      transition: color .15s;
    }
    .standalone-tab-bar a:hover { color: #111827; }
    .standalone-tab-bar a.active {
      color: var(--accent, #6c63ff);
      border-bottom-color: var(--accent, #6c63ff);
      font-weight: 600;
    }
  `;
  document.head.appendChild(tabStyle);

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

  // ── Инжектировать таб-бар в нужное место ─────────────────────
  function injectTabs() {
    const group = currentTabGroup();
    if (!group) return;

    // Вариант 1: sidebar страница — в начало .page-container
    const pageContainer = document.querySelector('.page-container');
    if (pageContainer) {
      const bar = renderTabBar(group, 'nav-tab-bar');
      pageContainer.insertBefore(bar, pageContainer.firstChild);
      return;
    }

    // Вариант 2: standalone страница — после .header
    const header = document.querySelector('.header');
    if (header) {
      const bar = renderTabBar(group, 'standalone-tab-bar');
      header.insertAdjacentElement('afterend', bar);
      return;
    }
  }

  // Инжектируем после загрузки DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectTabs);
  } else {
    injectTabs();
  }

  // ── Сайдбар ──────────────────────────────────────────────────
  const sidebar = document.getElementById('sidebar');
  if (sidebar) {
    let html = `<div class="sidebar-logo">Lunary <span>OS</span></div>`;

    for (const group of GROUPS) {
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

  // ── Нижняя панель (мобильная) ─────────────────────────────────
  const BNAV_LINKS = [
    { href: '/admin',             icon: '📦', label: 'Склад' },
    { href: '/admin/scanner',     icon: '📷', label: 'Сканер' },
    { href: '/admin/history',     icon: '📋', label: 'История' },
    { href: '/admin/kaspi',       icon: '🛒', label: 'Kaspi' },
    { href: '/admin/shop-orders', icon: '🛍️', label: 'Заказы' },
    { href: '/shop',              icon: '🏪', label: 'Магазин' },
  ];

  const bottomNav = document.getElementById('bottom-nav');
  if (bottomNav) {
    bottomNav.className = 'bottom-nav';
    bottomNav.innerHTML = `<div class="bottom-nav-inner">
      ${BNAV_LINKS.map(l => {
        const active = isActive(l.href) ? ' active' : '';
        return `<a class="bnav-item${active}" href="${l.href}">${l.icon} ${l.label}</a>`;
      }).join('\n      ')}
    </div>`;
  }
})();
