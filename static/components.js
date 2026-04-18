/**
 * components.js — единая библиотека UI-компонентов Lunary OS.
 * Использование: window.LX.drawer({...}), window.LX.modal({...}) и т.д.
 */
(function () {
  const ns = {};

  // ── Стили (инжектятся один раз) ──────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
/* ═══ Overlay (backdrop) ═══ */
.lx-overlay {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.48);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
  opacity: 0; transition: opacity .22s ease;
  z-index: 1000;
}
.lx-overlay.show { opacity: 1; }

/* ═══ Drawer (desktop) / Bottom Sheet (mobile) ═══ */
.lx-drawer {
  position: fixed; top: 0; right: 0; bottom: 0;
  width: min(520px, 100vw);
  background: var(--surface, #fff);
  box-shadow: -10px 0 40px rgba(0, 0, 0, 0.12);
  transform: translateX(100%);
  transition: transform .28s cubic-bezier(0.22, 0.61, 0.36, 1);
  z-index: 1001;
  display: flex; flex-direction: column;
}
.lx-drawer.show { transform: translateX(0); }

.lx-drawer-header {
  display: flex; align-items: center; gap: 12px;
  padding: 18px 22px;
  border-bottom: 1px solid var(--border, #e5e7eb);
  flex-shrink: 0;
}
.lx-drawer-title {
  flex: 1; font-size: 17px;
  font-weight: var(--fw-bold, 700);
  color: var(--text, #111);
  line-height: 1.3;
}
.lx-drawer-close {
  background: #f3f4f6; border: none; border-radius: 50%;
  width: 32px; height: 32px;
  color: var(--text2, #555); font-size: 20px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  line-height: 1; transition: background .15s;
}
.lx-drawer-close:hover { background: #e5e7eb; }

.lx-drawer-tabs {
  display: flex; gap: 2px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border, #e5e7eb);
  overflow-x: auto; scrollbar-width: none;
  flex-shrink: 0;
}
.lx-drawer-tabs::-webkit-scrollbar { display: none; }
.lx-drawer-tab {
  padding: 10px 14px; font-size: 13px;
  font-weight: var(--fw-medium, 500);
  color: var(--text2, #6b7280);
  background: none; border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px; cursor: pointer;
  white-space: nowrap; transition: color .15s;
}
.lx-drawer-tab:hover { color: var(--text, #111); }
.lx-drawer-tab.active {
  color: var(--accent, #6366f1);
  border-bottom-color: var(--accent, #6366f1);
  font-weight: var(--fw-semibold, 600);
}

.lx-drawer-body {
  flex: 1; overflow-y: auto;
  padding: 20px 22px;
  -webkit-overflow-scrolling: touch;
}
.lx-drawer-footer {
  padding: 14px 22px;
  border-top: 1px solid var(--border, #e5e7eb);
  display: flex; gap: 10px; justify-content: flex-end;
  flex-shrink: 0;
  background: var(--surface, #fff);
}

/* Mobile → Bottom Sheet */
@media (max-width: 768px) {
  .lx-drawer {
    top: auto; right: 0; left: 0; bottom: 0;
    width: 100%; max-height: 92vh;
    border-radius: 18px 18px 0 0;
    transform: translateY(100%);
  }
  .lx-drawer.show { transform: translateY(0); }
  .lx-drawer-header::before {
    content: ''; position: absolute; top: 8px; left: 50%;
    transform: translateX(-50%);
    width: 40px; height: 4px; border-radius: 2px;
    background: #d1d5db;
  }
  .lx-drawer-header { position: relative; padding-top: 22px; }
}

/* ═══ Modal (centered popup) ═══ */
.lx-modal {
  position: fixed; top: 50%; left: 50%;
  transform: translate(-50%, -50%) scale(0.96);
  opacity: 0;
  background: var(--surface, #fff);
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
  width: min(480px, calc(100vw - 24px));
  max-height: calc(100vh - 48px);
  display: flex; flex-direction: column;
  z-index: 1001;
  transition: opacity .2s ease, transform .2s ease;
}
.lx-modal.show { opacity: 1; transform: translate(-50%, -50%) scale(1); }

.lx-modal-header {
  padding: 18px 22px 8px;
  display: flex; align-items: center; gap: 10px;
}
.lx-modal-title {
  flex: 1; font-size: 17px;
  font-weight: var(--fw-bold, 700);
  color: var(--text, #111);
}
.lx-modal-body {
  padding: 10px 22px 20px;
  overflow-y: auto; flex: 1;
}
.lx-modal-footer {
  padding: 12px 22px 18px;
  display: flex; gap: 10px; justify-content: flex-end;
  border-top: 1px solid var(--border, #e5e7eb);
}

/* ═══ Dropdown menu ═══ */
.lx-dropdown {
  position: absolute;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  min-width: 180px; z-index: 1002;
  opacity: 0; transform: translateY(-4px);
  transition: opacity .15s, transform .15s;
  pointer-events: none;
}
.lx-dropdown.show { opacity: 1; transform: translateY(0); pointer-events: auto; }
.lx-dropdown-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px;
  font-size: 14px; color: var(--text, #111);
  background: none; border: none; border-radius: 7px;
  width: 100%; text-align: left;
  cursor: pointer; transition: background .1s;
}
.lx-dropdown-item:hover { background: #f3f4f6; }
.lx-dropdown-item.danger { color: var(--red, #ef4444); }
.lx-dropdown-item.danger:hover { background: #fee2e2; }
.lx-dropdown-divider {
  height: 1px; background: var(--border, #e5e7eb);
  margin: 5px 0;
}

/* Mobile → bottom sheet с крупными кнопками */
@media (max-width: 768px) {
  .lx-dropdown {
    position: fixed !important;
    left: 0 !important; right: 0 !important;
    bottom: 0 !important; top: auto !important;
    border-radius: 18px 18px 0 0;
    border: none;
    padding: 10px 10px calc(14px + env(safe-area-inset-bottom, 0px));
    transform: translateY(100%);
    min-width: 0;
  }
  .lx-dropdown.show { transform: translateY(0); }
  .lx-dropdown-item { padding: 14px 16px; font-size: 15px; }
}

/* ═══ Skeleton ═══ */
.lx-skeleton {
  background: linear-gradient(90deg,
    #eceef2 0%, #f5f6f9 40%, #eceef2 80%);
  background-size: 200% 100%;
  animation: lxShimmer 1.4s ease-in-out infinite;
  border-radius: 6px;
}
@keyframes lxShimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.lx-skeleton-row {
  display: flex; gap: 14px; align-items: center;
  padding: 14px 0;
  border-bottom: 1px solid var(--border, #e5e7eb);
}

/* ═══ Empty & Error states ═══ */
.lx-empty, .lx-error-state {
  text-align: center; padding: 56px 24px;
  display: flex; flex-direction: column;
  align-items: center; gap: 8px;
}
.lx-empty-icon, .lx-error-state-icon {
  font-size: 48px; line-height: 1; margin-bottom: 8px;
  opacity: 0.75;
}
.lx-empty-title, .lx-error-state-title {
  font-size: 16px; font-weight: var(--fw-semibold, 600);
  color: var(--text, #111);
}
.lx-empty-desc, .lx-error-state-desc {
  font-size: 13px; color: var(--text3, #9ca3af);
  max-width: 320px;
}
.lx-empty-cta, .lx-error-state-cta {
  margin-top: 14px;
  padding: 10px 22px;
  background: var(--accent, #6366f1);
  color: #fff; border: none;
  border-radius: 10px;
  font-size: 14px; font-weight: var(--fw-semibold, 600);
  cursor: pointer;
}
.lx-error-state-icon { color: var(--red, #ef4444); }

/* ═══ FAB (Floating Action Button) ═══ */
.lx-fab {
  position: fixed;
  right: 18px;
  bottom: calc(18px + env(safe-area-inset-bottom, 0px));
  width: 56px; height: 56px;
  border-radius: 50%;
  background: var(--accent, #6366f1);
  color: #fff; border: none;
  font-size: 26px; font-weight: var(--fw-semibold, 600);
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.38);
  cursor: pointer; z-index: 900;
  display: flex; align-items: center; justify-content: center;
  transition: transform .15s, box-shadow .15s;
}
.lx-fab:hover { transform: translateY(-2px); box-shadow: 0 12px 28px rgba(99, 102, 241, 0.45); }
.lx-fab:active { transform: scale(0.96); }

/* FAB над bottom nav на мобилке */
@media (max-width: 768px) {
  .lx-fab { bottom: calc(82px + env(safe-area-inset-bottom, 0px)); }
}

/* ═══ Toast ═══ */
.lx-toast-container {
  position: fixed;
  bottom: calc(24px + env(safe-area-inset-bottom, 0px));
  left: 50%; transform: translateX(-50%);
  z-index: 2000;
  display: flex; flex-direction: column;
  gap: 8px; align-items: center;
  pointer-events: none;
}
@media (max-width: 768px) {
  .lx-toast-container { bottom: calc(84px + env(safe-area-inset-bottom, 0px)); }
}
.lx-toast {
  background: var(--text, #111); color: #fff;
  padding: 11px 20px; border-radius: 12px;
  font-size: 14px; font-weight: var(--fw-medium, 500);
  opacity: 0; transform: translateY(10px);
  transition: opacity .25s, transform .25s;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  max-width: calc(100vw - 32px);
  white-space: normal; text-align: center;
}
.lx-toast.show { opacity: 1; transform: translateY(0); }
.lx-toast.success { background: var(--green, #10b981); }
.lx-toast.error { background: var(--red, #ef4444); }
  `;
  document.head.appendChild(style);

  // ── Utility: close on ESC ───────────────────────────────────
  function onEsc(fn) {
    const h = (e) => { if (e.key === 'Escape') fn(); };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }

  // ── Drawer ───────────────────────────────────────────────────
  // Usage: LX.drawer({ title, tabs: [{id, label, render(el)}], footer, onClose })
  ns.drawer = function ({ title = '', tabs = null, body = '', footer = null, onClose = null } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'lx-overlay';

    const el = document.createElement('div');
    el.className = 'lx-drawer';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');

    let tabsHtml = '';
    if (tabs && tabs.length) {
      tabsHtml = `<div class="lx-drawer-tabs">${
        tabs.map((t, i) => `<button class="lx-drawer-tab${i === 0 ? ' active' : ''}" data-tab="${t.id}">${t.label}</button>`).join('')
      }</div>`;
    }

    el.innerHTML = `
      <div class="lx-drawer-header">
        <div class="lx-drawer-title">${title}</div>
        <button class="lx-drawer-close" aria-label="Закрыть">×</button>
      </div>
      ${tabsHtml}
      <div class="lx-drawer-body"></div>
      ${footer ? '<div class="lx-drawer-footer"></div>' : ''}
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(el);
    document.body.style.overflow = 'hidden';

    const bodyEl = el.querySelector('.lx-drawer-body');
    const footerEl = el.querySelector('.lx-drawer-footer');

    // Render initial
    if (tabs && tabs.length) {
      tabs[0].render && tabs[0].render(bodyEl);
      el.querySelectorAll('.lx-drawer-tab').forEach(btn => {
        btn.addEventListener('click', () => {
          el.querySelectorAll('.lx-drawer-tab').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          bodyEl.innerHTML = '';
          const t = tabs.find(x => x.id === btn.dataset.tab);
          t && t.render && t.render(bodyEl);
        });
      });
    } else if (typeof body === 'function') {
      body(bodyEl);
    } else if (typeof body === 'string') {
      bodyEl.innerHTML = body;
    } else if (body instanceof HTMLElement) {
      bodyEl.appendChild(body);
    }

    if (footer && footerEl) {
      if (typeof footer === 'function') footer(footerEl);
      else if (typeof footer === 'string') footerEl.innerHTML = footer;
      else if (footer instanceof HTMLElement) footerEl.appendChild(footer);
    }

    requestAnimationFrame(() => {
      overlay.classList.add('show');
      el.classList.add('show');
    });

    function close() {
      overlay.classList.remove('show');
      el.classList.remove('show');
      setTimeout(() => {
        overlay.remove();
        el.remove();
        document.body.style.overflow = '';
        unbindEsc();
        onClose && onClose();
      }, 280);
    }

    overlay.addEventListener('click', close);
    el.querySelector('.lx-drawer-close').addEventListener('click', close);
    const unbindEsc = onEsc(close);

    return { el, close, bodyEl, footerEl };
  };

  // ── Modal ────────────────────────────────────────────────────
  // Usage: LX.modal({ title, body, footer, onClose })
  ns.modal = function ({ title = '', body = '', footer = null, onClose = null } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'lx-overlay';

    const el = document.createElement('div');
    el.className = 'lx-modal';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');

    el.innerHTML = `
      <div class="lx-modal-header">
        <div class="lx-modal-title">${title}</div>
        <button class="lx-drawer-close" aria-label="Закрыть">×</button>
      </div>
      <div class="lx-modal-body"></div>
      ${footer ? '<div class="lx-modal-footer"></div>' : ''}
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(el);
    document.body.style.overflow = 'hidden';

    const bodyEl = el.querySelector('.lx-modal-body');
    const footerEl = el.querySelector('.lx-modal-footer');

    if (typeof body === 'function') body(bodyEl);
    else if (typeof body === 'string') bodyEl.innerHTML = body;
    else if (body instanceof HTMLElement) bodyEl.appendChild(body);

    if (footer && footerEl) {
      if (typeof footer === 'function') footer(footerEl);
      else if (typeof footer === 'string') footerEl.innerHTML = footer;
      else if (footer instanceof HTMLElement) footerEl.appendChild(footer);
    }

    requestAnimationFrame(() => {
      overlay.classList.add('show');
      el.classList.add('show');
    });

    function close() {
      overlay.classList.remove('show');
      el.classList.remove('show');
      setTimeout(() => {
        overlay.remove();
        el.remove();
        document.body.style.overflow = '';
        unbindEsc();
        onClose && onClose();
      }, 220);
    }

    overlay.addEventListener('click', close);
    el.querySelector('.lx-drawer-close').addEventListener('click', close);
    const unbindEsc = onEsc(close);

    return { el, close, bodyEl, footerEl };
  };

  // ── Confirm (quick modal) ────────────────────────────────────
  // Usage: LX.confirm({ title, message, okText, danger }).then(ok => ...)
  ns.confirm = function ({ title = 'Подтвердите действие', message = '', okText = 'Подтвердить', cancelText = 'Отмена', danger = false } = {}) {
    return new Promise(resolve => {
      const { close } = ns.modal({
        title,
        body: `<div style="font-size:14px;color:var(--text2,#555);line-height:1.5">${message}</div>`,
        footer: (el) => {
          const cancel = document.createElement('button');
          cancel.textContent = cancelText;
          cancel.style.cssText = 'padding:10px 18px;border-radius:10px;border:1px solid var(--border,#e5e7eb);background:#fff;color:var(--text,#111);font-size:14px;font-weight:600;cursor:pointer';
          cancel.onclick = () => { close(); resolve(false); };

          const ok = document.createElement('button');
          ok.textContent = okText;
          ok.style.cssText = `padding:10px 18px;border-radius:10px;border:none;background:${danger ? 'var(--red,#ef4444)' : 'var(--accent,#6366f1)'};color:#fff;font-size:14px;font-weight:600;cursor:pointer`;
          ok.onclick = () => { close(); resolve(true); };

          el.appendChild(cancel);
          el.appendChild(ok);
        },
        onClose: () => resolve(false),
      });
    });
  };

  // ── Dropdown ─────────────────────────────────────────────────
  // Usage: LX.dropdown(triggerEl, [{ icon, label, danger, onClick }])
  let _activeDropdown = null;
  ns.dropdown = function (trigger, items) {
    if (_activeDropdown) _activeDropdown.close();

    const menu = document.createElement('div');
    menu.className = 'lx-dropdown';

    items.forEach(it => {
      if (it.divider) {
        menu.appendChild(Object.assign(document.createElement('div'), { className: 'lx-dropdown-divider' }));
        return;
      }
      const btn = document.createElement('button');
      btn.className = 'lx-dropdown-item' + (it.danger ? ' danger' : '');
      btn.innerHTML = `<span style="font-size:16px;line-height:1">${it.icon || ''}</span><span>${it.label}</span>`;
      btn.onclick = (e) => {
        e.stopPropagation();
        close();
        it.onClick && it.onClick();
      };
      menu.appendChild(btn);
    });

    document.body.appendChild(menu);

    // Mobile: ignore positioning (CSS handles it as bottom sheet)
    const isMobile = window.innerWidth <= 768;
    if (!isMobile) {
      const rect = trigger.getBoundingClientRect();
      const menuWidth = 200;
      const rightSpace = window.innerWidth - rect.left;
      const top = rect.bottom + 6 + window.scrollY;
      const left = rightSpace < menuWidth ? rect.right - menuWidth + window.scrollX : rect.left + window.scrollX;
      menu.style.top = top + 'px';
      menu.style.left = left + 'px';
    }

    requestAnimationFrame(() => menu.classList.add('show'));

    function close() {
      menu.classList.remove('show');
      setTimeout(() => menu.remove(), 160);
      document.removeEventListener('click', onOutside);
      _activeDropdown = null;
    }

    function onOutside(e) { if (!menu.contains(e.target) && e.target !== trigger) close(); }
    setTimeout(() => document.addEventListener('click', onOutside), 0);

    _activeDropdown = { close };
    return { close };
  };

  // ── Skeleton ─────────────────────────────────────────────────
  // Usage: LX.skeleton(el, { rows: 5 }) — заменяет содержимое скелетонами
  ns.skeleton = function (target, { rows = 5, height = 16 } = {}) {
    target.innerHTML = '';
    for (let i = 0; i < rows; i++) {
      const row = document.createElement('div');
      row.className = 'lx-skeleton-row';
      row.innerHTML = `
        <div class="lx-skeleton" style="width:44px;height:44px;border-radius:8px;flex-shrink:0"></div>
        <div style="flex:1;display:flex;flex-direction:column;gap:8px">
          <div class="lx-skeleton" style="width:65%;height:${height}px"></div>
          <div class="lx-skeleton" style="width:40%;height:${height - 4}px"></div>
        </div>
        <div class="lx-skeleton" style="width:60px;height:${height}px;flex-shrink:0"></div>
      `;
      target.appendChild(row);
    }
  };

  // ── Empty state ──────────────────────────────────────────────
  // Usage: LX.empty(el, { icon, title, desc, cta: {label, onClick} })
  ns.empty = function (target, { icon = '📭', title = 'Пусто', desc = '', cta = null } = {}) {
    target.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'lx-empty';
    wrap.innerHTML = `
      <div class="lx-empty-icon">${icon}</div>
      <div class="lx-empty-title">${title}</div>
      ${desc ? `<div class="lx-empty-desc">${desc}</div>` : ''}
    `;
    if (cta) {
      const btn = document.createElement('button');
      btn.className = 'lx-empty-cta';
      btn.textContent = cta.label;
      btn.onclick = cta.onClick;
      wrap.appendChild(btn);
    }
    target.appendChild(wrap);
  };

  // ── Error state ──────────────────────────────────────────────
  ns.errorState = function (target, { title = 'Ошибка загрузки', desc = '', onRetry = null } = {}) {
    target.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'lx-error-state';
    wrap.innerHTML = `
      <div class="lx-error-state-icon">⚠️</div>
      <div class="lx-error-state-title">${title}</div>
      ${desc ? `<div class="lx-error-state-desc">${desc}</div>` : ''}
    `;
    if (onRetry) {
      const btn = document.createElement('button');
      btn.className = 'lx-error-state-cta';
      btn.textContent = 'Повторить';
      btn.onclick = onRetry;
      wrap.appendChild(btn);
    }
    target.appendChild(wrap);
  };

  // ── FAB ──────────────────────────────────────────────────────
  // Usage: LX.fab({ icon: '+', label: 'Добавить', onClick })
  ns.fab = function ({ icon = '+', label = '', onClick = null } = {}) {
    // Убираем старый FAB если есть
    const old = document.querySelector('.lx-fab');
    if (old) old.remove();

    const btn = document.createElement('button');
    btn.className = 'lx-fab';
    btn.setAttribute('aria-label', label);
    if (label) btn.setAttribute('title', label);
    btn.textContent = icon;
    btn.onclick = onClick;
    document.body.appendChild(btn);
    return btn;
  };

  // ── Toast ────────────────────────────────────────────────────
  // Usage: LX.toast('Сохранено', 'success')
  let _toastContainer = null;
  ns.toast = function (msg, type = '') {
    if (!_toastContainer) {
      _toastContainer = document.createElement('div');
      _toastContainer.className = 'lx-toast-container';
      document.body.appendChild(_toastContainer);
    }
    const t = document.createElement('div');
    t.className = 'lx-toast' + (type ? ' ' + type : '');
    t.textContent = msg;
    _toastContainer.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => {
      t.classList.remove('show');
      setTimeout(() => t.remove(), 300);
    }, 2800);
  };

  // ── URL state helpers ────────────────────────────────────────
  // Usage: LX.url.get('tab') / LX.url.set({tab: 'kaspi', page: 2})
  ns.url = {
    get(key) { return new URLSearchParams(location.search).get(key); },
    getAll() {
      const params = {};
      new URLSearchParams(location.search).forEach((v, k) => { params[k] = v; });
      return params;
    },
    set(obj, { replace = false } = {}) {
      const params = new URLSearchParams(location.search);
      Object.entries(obj).forEach(([k, v]) => {
        if (v === null || v === undefined || v === '') params.delete(k);
        else params.set(k, String(v));
      });
      const url = location.pathname + (params.toString() ? '?' + params.toString() : '');
      if (replace) history.replaceState(null, '', url);
      else history.pushState(null, '', url);
    },
  };

  // ── Export ───────────────────────────────────────────────────
  window.LX = ns;
  // Обратная совместимость: window.toast(msg, type)
  if (!window.toast) window.toast = ns.toast;
})();
