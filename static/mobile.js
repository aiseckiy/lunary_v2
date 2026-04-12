/* ============================================
   LUNARY OS — Mobile Burger Menu
   Инжектится на всех admin-страницах
   ============================================ */

(function() {
  const NAV_ITEMS = [
    { href: '/admin',             icon: '🏠', label: 'Товары' },
    { href: '/admin/scanner',     icon: '📷', label: 'Сканер' },
    { href: '/admin/history',     icon: '📋', label: 'История' },
    { href: '/admin/analytics',   icon: '📊', label: 'Аналитика' },
    { href: '/admin/kaspi',       icon: '🛒', label: 'Kaspi' },
    { href: '/admin/settings',    icon: '⚙️', label: 'Настройки' },
  ];

  function currentPath() {
    return location.pathname.replace(/\/$/, '') || '/admin';
  }

  function isActive(href) {
    const p = currentPath();
    if (href === '/admin') return p === '/admin';
    return p.startsWith(href);
  }

  function init() {
    injectStyles();
    buildDrawer();
    patchMobileHeader();
    removeBottomNav();
  }

  function injectStyles() {
    if (document.getElementById('lunary-mobile-css')) return;
    const s = document.createElement('style');
    s.id = 'lunary-mobile-css';
    s.textContent = `
/* ── Burger button ── */
.burger-btn {
  display: none;
  background: none;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 9px;
  width: 38px; height: 38px;
  align-items: center; justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: background .15s;
  font-size: 0;
}
.burger-btn:active { background: #f3f4f6; }
.burger-icon { display:flex; flex-direction:column; gap:4px; }
.burger-icon span {
  display: block; width: 18px; height: 2px;
  background: #111827; border-radius: 2px;
  transition: all .2s;
}

/* ── Drawer overlay ── */
.drawer-overlay {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,.35);
  z-index: 400;
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}
.drawer-overlay.open { display: block; }

/* ── Drawer panel ── */
.nav-drawer {
  position: fixed;
  top: 0; left: 0; bottom: 0;
  width: 280px;
  background: #fff;
  z-index: 401;
  transform: translateX(-100%);
  transition: transform .28s cubic-bezier(.4,0,.2,1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 4px 0 32px rgba(0,0,0,.12);
}
.nav-drawer.open { transform: translateX(0); }

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 20px 16px;
  border-bottom: 1px solid #f0f0f2;
}
.drawer-logo {
  font-size: 20px; font-weight: 800;
  letter-spacing: -.3px; color: #111827;
}
.drawer-logo span { color: #6366f1; }

.drawer-close {
  width: 32px; height: 32px;
  border: 1px solid #e5e7eb; border-radius: 8px;
  background: none; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; color: #6b7280;
  transition: background .15s;
}
.drawer-close:active { background: #f3f4f6; }

.drawer-nav {
  flex: 1;
  padding: 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.drawer-section {
  font-size: 10px; font-weight: 700;
  color: #9ca3af; text-transform: uppercase;
  letter-spacing: .08em;
  padding: 8px 10px 4px;
}

.drawer-link {
  display: flex; align-items: center; gap: 14px;
  padding: 13px 14px;
  border-radius: 12px;
  text-decoration: none;
  color: #374151;
  font-size: 15px; font-weight: 500;
  transition: all .15s;
  -webkit-tap-highlight-color: transparent;
}
.drawer-link:active { background: #f3f4f6; }
.drawer-link.active {
  background: #111827;
  color: #fff;
  font-weight: 600;
}
.drawer-link-icon { font-size: 22px; line-height: 1; width: 28px; text-align: center; }

.drawer-footer {
  padding: 16px 20px 28px;
  border-top: 1px solid #f0f0f2;
}
.drawer-store-link {
  display: flex; align-items: center; gap: 10px;
  padding: 11px 14px;
  border-radius: 10px;
  text-decoration: none;
  color: #9ca3af;
  font-size: 13px;
  transition: all .15s;
}
.drawer-store-link:active { background: #f9fafb; }

@media (max-width: 768px) {
  .burger-btn { display: flex; }
  .bottom-nav { display: none !important; }
  .mobile-header { padding: 12px 16px; }
  /* Make page title smaller on mobile */
  .page-title { font-size: 18px !important; }
  .page-sub { font-size: 12px !important; margin-bottom:14px !important; }
  /* Stats grid compact */
  .stats-grid { grid-template-columns: repeat(2, 1fr) !important; gap: 10px !important; }
  .stat-card { padding: 14px !important; }
  .stat-value { font-size: 22px !important; }
}
    `;
    document.head.appendChild(s);
  }

  function buildDrawer() {
    if (document.getElementById('lunary-drawer')) return;

    // Overlay
    const overlay = document.createElement('div');
    overlay.className = 'drawer-overlay';
    overlay.id = 'lunary-drawer-overlay';
    overlay.addEventListener('click', closeDrawer);

    // Drawer
    const drawer = document.createElement('div');
    drawer.className = 'nav-drawer';
    drawer.id = 'lunary-drawer';

    const navLinks = NAV_ITEMS.map(item => `
      <a class="drawer-link ${isActive(item.href) ? 'active' : ''}" href="${item.href}">
        <span class="drawer-link-icon">${item.icon}</span>
        ${item.label}
      </a>
    `).join('');

    drawer.innerHTML = `
      <div class="drawer-header">
        <div class="drawer-logo">Lunary <span>OS</span></div>
        <button class="drawer-close" onclick="window.__lunaryCloseDrawer()">✕</button>
      </div>
      <div class="drawer-nav">
        <div class="drawer-section">Управление</div>
        ${navLinks}
      </div>
      <div class="drawer-footer">
        <a class="drawer-store-link" href="/shop">
          <span style="font-size:18px">🛍</span> Перейти в магазин
        </a>
        <a class="drawer-store-link" href="#" onclick="logout()" style="margin-top:6px;color:#ef4444">
          <span style="font-size:18px">🚪</span> Выйти
        </a>
      </div>
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(drawer);
  }

  function patchMobileHeader() {
    const header = document.querySelector('.mobile-header');
    if (!header) return;

    // Add burger button if not exists
    if (header.querySelector('.burger-btn')) return;

    const btn = document.createElement('button');
    btn.className = 'burger-btn';
    btn.setAttribute('aria-label', 'Меню');
    btn.innerHTML = `<div class="burger-icon"><span></span><span></span><span></span></div>`;
    btn.addEventListener('click', openDrawer);

    // Добавляем в группу кнопок справа
    const group = header.querySelector('.mobile-header-actions') || header.querySelector('div[style*="gap:8px"]') || header.querySelector('div:last-child');
    if (group && group !== header.querySelector('.mobile-logo')?.parentElement) {
      group.appendChild(btn);
    } else {
      header.appendChild(btn);
    }
  }

  function removeBottomNav() {
    // Hide bottom nav on mobile via CSS (already in styles)
    // Also remove from DOM if screen is mobile
    if (window.innerWidth <= 768) {
      const bn = document.querySelector('.bottom-nav');
      if (bn) bn.style.display = 'none';
    }
  }

  function openDrawer() {
    document.getElementById('lunary-drawer')?.classList.add('open');
    document.getElementById('lunary-drawer-overlay')?.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  function closeDrawer() {
    document.getElementById('lunary-drawer')?.classList.remove('open');
    document.getElementById('lunary-drawer-overlay')?.classList.remove('open');
    document.body.style.overflow = '';
  }

  window.__lunaryCloseDrawer = closeDrawer;

  window.logout = async function() {
    await fetch('/api/auth/logout', {method: 'POST'});
    location.href = '/login';
  };

  // Run after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
