/**
 * nav.js — единая боковая и нижняя навигация для всех страниц.
 */
(function () {
  fetch('/api/admin/theme/css').then(r => r.text()).then(css => {
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }).catch(() => {});

  const path = window.location.pathname;

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
        { href: '/import',    icon: '📥', label: 'Импорт XML' },
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
