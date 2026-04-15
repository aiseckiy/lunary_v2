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
  const PAGE_META = {
    '/admin': { label: 'Склад', title: 'Операционная панель склада', sub: 'Быстрый контроль остатков, карточек и повседневных действий по товарам.' },
    '/admin/history': { label: 'Журнал', title: 'История движений', sub: 'Все изменения по складу в одном потоке: продажи, приход, списания и корректировки.' },
    '/admin/analytics': { label: 'Аналитика', title: 'Продажи и динамика', sub: 'Смотри, что двигается быстрее всего, где проседает запас и какие категории приносят выручку.' },
    '/admin/scanner': { label: 'Сканер', title: 'Быстрые складские действия', sub: 'Сканируй штрихкод, сразу находи товар и меняй остаток без лишних переходов.' },
    '/admin/kaspi': { label: 'Продажи', title: 'Kaspi заказы', sub: 'Следи за потоком заказов, синхронизацией и оперативно реагируй на изменения статусов.' },
    '/admin/shop-orders': { label: 'Магазин', title: 'Заказы сайта', sub: 'Новые обращения, контакты клиентов и обработка заказов в одном месте.' },
    '/admin/settings': { label: 'Система', title: 'Настройки и доступы', sub: 'Управление профилем, сотрудниками, контентом магазина и ключевыми параметрами системы.' },
    '/admin/theme': { label: 'Дизайн', title: 'Визуальная система', sub: 'Токены, цвета и визуальные настройки бренда без ручной правки кода.' },
  };

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
      gap: 8px;
      padding: 10px 0 0;
      margin-bottom: 18px;
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
      padding: 9px 14px;
      font-size: 13px;
      font-weight: 600;
      color: #55645a;
      text-decoration: none;
      white-space: nowrap;
      border-radius: 999px;
      border: 1px solid #d7e0d7;
      background: rgba(255,255,255,.72);
      transition: color .15s, background .15s, border-color .15s, transform .15s;
    }
    .nav-tab-bar a:hover {
      color: #16211b;
      background: #fff;
      transform: translateY(-1px);
    }
    .nav-tab-bar a.active {
      color: #ffffff;
      background: linear-gradient(135deg, var(--accent, #0d7a5f), #0f5d50);
      border-color: transparent;
      font-weight: 600;
    }

    /* Для standalone страниц (без sidebar) — таб-бар в header */
    .standalone-tab-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 24px 0;
      background: transparent;
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
      font-weight: 600;
      color: #55645a;
      text-decoration: none;
      white-space: nowrap;
      border-radius: 999px;
      border: 1px solid #d7e0d7;
      background: rgba(255,255,255,.72);
      transition: color .15s, background .15s, transform .15s;
    }
    .standalone-tab-bar a:hover {
      color: #16211b;
      background: #fff;
      transform: translateY(-1px);
    }
    .standalone-tab-bar a.active {
      color: #fff;
      background: linear-gradient(135deg, var(--accent, #0d7a5f), #0f5d50);
      border-color: transparent;
      font-weight: 600;
    }
  `;
  document.head.appendChild(tabStyle);

  function getCurrentMeta() {
    const exact = PAGE_META[path];
    if (exact) return exact;
    for (const [prefix, meta] of Object.entries(PAGE_META)) {
      if (prefix !== '/admin' && path.startsWith(prefix)) return meta;
    }
    return {
      label: 'Lunary OS',
      title: document.title.replace(/\s*[—-]\s*Lunary.*$/i, '').trim() || 'Рабочая область',
      sub: 'Центральная панель для работы с ассортиментом, заказами, импортом и настройками магазина.'
    };
  }

  function injectPageHero() {
    const container = document.querySelector('.page-container');
    if (!container || container.querySelector('.page-hero')) return;

    const titleEl = container.querySelector('.page-title');
    const subEl = container.querySelector('.page-sub');
    if (!titleEl) return;

    const meta = getCurrentMeta();
    const hero = document.createElement('section');
    hero.className = 'page-hero';

    const kicker = document.createElement('div');
    kicker.className = 'page-kicker';
    kicker.textContent = meta.label;

    hero.appendChild(kicker);
    hero.appendChild(titleEl);
    if (subEl) {
      hero.appendChild(subEl);
    } else if (meta.sub) {
      const sub = document.createElement('div');
      sub.className = 'page-sub';
      sub.textContent = meta.sub;
      hero.appendChild(sub);
    }

    container.insertBefore(hero, container.firstChild);
  }

  function injectStandaloneHero() {
    const header = document.querySelector('.header');
    if (!header || document.querySelector('.standalone-hero')) return;
    const h1 = header.querySelector('h1');
    if (!h1) return;

    const meta = getCurrentMeta();
    const hero = document.createElement('section');
    hero.className = 'standalone-hero';

    const kicker = document.createElement('div');
    kicker.className = 'page-kicker';
    kicker.textContent = meta.label;
    hero.appendChild(kicker);
    hero.appendChild(h1);

    const p = header.querySelector('p, .page-sub');
    if (p) {
      hero.appendChild(p);
    } else if (meta.sub) {
      const sub = document.createElement('p');
      sub.textContent = meta.sub;
      hero.appendChild(sub);
    }

    header.parentNode.insertBefore(hero, header.nextSibling);
  }

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
    injectPageHero();
    injectStandaloneHero();
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
    // Определяем роль пользователя — для скрытия admin-only пунктов
    const _ADMIN_ONLY_LINKS = new Set(['/admin/settings', '/admin/theme', '/admin/changelog', '/admin/sitemap', '/admin/bizmap']);
    let userRole = 'admin'; // по умолчанию показываем всё

    fetch('/api/auth/me').then(r => r.ok ? r.json() : null).then(me => {
      if (me) userRole = me.role;
      buildSidebar(userRole);
    }).catch(() => buildSidebar('admin'));

    function buildSidebar(role) {
      const isManagerOnly = role === 'manager';
      const meta = getCurrentMeta();
      let html = `<div class="sidebar-logo">Lunary <span>OS</span></div>`;
      html += `
        <div class="sidebar-meta">
          <div class="sidebar-meta-label">${meta.label}</div>
          <div class="sidebar-meta-title">${meta.title}</div>
          <div class="sidebar-meta-sub">${meta.sub}</div>
        </div>
      `;

      for (const group of GROUPS) {
        const visibleLinks = group.links.filter(l => !(isManagerOnly && _ADMIN_ONLY_LINKS.has(l.href)));
        if (!visibleLinks.length) continue;
        html += `<div class="sidebar-section">${group.label}</div>`;
        for (const l of visibleLinks) {
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
