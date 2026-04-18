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

  // ── Embed-режим: страница загружена в iframe на unified-странице.
  // Скрываем всю навигацию (sidebar, bottom nav, tab-bar, header).
  const isEmbed = new URLSearchParams(location.search).get('embed') === '1';
  if (isEmbed) {
    const embedStyle = document.createElement('style');
    embedStyle.textContent = `
      #sidebar, #bottom-nav, .sidebar, .bottom-nav,
      .nav-tab-bar, .standalone-tab-bar,
      .mobile-header, .app-nav { display: none !important; }
      body { padding-left: 0 !important; padding-bottom: 0 !important; padding-top: 0 !important; }
      .layout, .main-content, .page-container { margin-left: 0 !important; padding-top: 0 !important; }
      .header { display: none !important; }
    `;
    document.head.appendChild(embedStyle);
    document.body && document.body.classList.add('embed-mode');
    return; // Embed-режим — сайдбар/bottom-nav/tab-bar не рендерим
  }

  // ── Навигационные группы (сайдбар) ───────────────────────────
  // Каждая группа = одна объединённая страница с табами внутри.
  // Старые URL (/admin/kaspi, /merge, /admin/theme…) продолжают работать,
  // но в сайдбаре больше не показываются — они доступны через табы
  // внутри unified-страниц.
  const GROUPS = [
    {
      id: 'catalog',
      label: 'Каталог',
      links: [
        { href: '/admin/catalog', icon: '📦', label: 'Каталог товаров', matchPrefix: ['/admin/catalog', '/admin/brands', '/admin/categories'], matchExact: ['/admin'] },
      ]
    },
    {
      id: 'warehouse',
      label: 'Склад',
      links: [
        { href: '/admin/warehouse', icon: '🏭', label: 'Склад', matchPrefix: ['/admin/warehouse', '/admin/scanner', '/admin/history', '/admin/audit'] },
      ]
    },
    {
      id: 'sales',
      label: 'Продажи',
      links: [
        { href: '/admin/orders', icon: '💰', label: 'Заказы', badge: 'orders-badge', matchPrefix: ['/admin/orders', '/admin/kaspi', '/admin/shop-orders', '/admin/analytics', '/admin/export-preview'] },
        { href: '/shop',         icon: '🏪', label: 'Витрина магазина' },
      ]
    },
    {
      id: 'data',
      label: 'Данные',
      links: [
        { href: '/admin/io',      icon: '📥', label: 'Импорт/Экспорт', matchPrefix: ['/admin/io', '/admin/import-xlsx', '/admin/export-preview', '/import', '/pricelist', '/uploads'] },
        { href: '/admin/quality', icon: '🧹', label: 'Качество данных', matchPrefix: ['/admin/quality', '/merge', '/review'] },
      ]
    },
    {
      id: 'system',
      label: 'Система',
      admin_only: true,
      links: [
        { href: '/admin/system', icon: '⚙️', label: 'Настройки', matchPrefix: ['/admin/system', '/admin/settings', '/admin/theme', '/admin/changelog', '/admin/sitemap', '/admin/bizmap'] },
      ]
    },
  ];

  // ── Mobile bottom nav — 5 иконок под каждую группу ───────────
  const BNAV_LINKS = [
    { id: 'catalog',   href: '/admin/catalog',   icon: '📦', label: 'Каталог' },
    { id: 'warehouse', href: '/admin/warehouse', icon: '🏭', label: 'Склад' },
    { id: 'sales',     href: '/admin/orders',    icon: '💰', label: 'Продажи' },
    { id: 'data',      href: '/admin/io',        icon: '📥', label: 'Данные' },
    { id: 'system',    href: '/admin/system',    icon: '⚙️', label: 'Ещё' },
  ];

  function isActive(link) {
    // Для объектов links из GROUPS
    if (typeof link === 'object') {
      if (link.matchExact && link.matchExact.includes(path)) return true;
      if (link.matchPrefix) {
        return link.matchPrefix.some(p => path === p || path.startsWith(p + '/') || path.startsWith(p + '?'));
      }
      return pathMatchesHref(link.href);
    }
    // Для простых строк (href)
    return pathMatchesHref(link);
  }

  function pathMatchesHref(href) {
    if (href === '/admin') return path === '/admin';
    if (href === '/shop') return path === '/shop';
    return path === href || path.startsWith(href + '/');
  }

  function currentGroupId() {
    for (const g of GROUPS) {
      for (const l of g.links) {
        if (isActive(l)) return g.id;
      }
    }
    return null;
  }

  // Таб-бар больше не инжектируется — он теперь часть unified-страниц
  // (catalog.html, orders.html, io.html, quality.html, system.html).

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
