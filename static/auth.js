/**
 * Lunary OS — Auth helper
 * Хранит ADMIN_KEY в localStorage, добавляет заголовок X-Admin-Key к API запросам
 */

(function () {
  const KEY_STORE = 'lunary_admin_key';

  // Получить ключ из localStorage
  window.getAdminKey = function () {
    return localStorage.getItem(KEY_STORE) || '';
  };

  // Обёртка над fetch — добавляет X-Admin-Key если задан
  window.apiFetch = function (url, options = {}) {
    const key = window.getAdminKey();
    if (key) {
      options.headers = Object.assign({ 'X-Admin-Key': key }, options.headers || {});
    }
    return fetch(url, options).then(async res => {
      if (res.status === 401) {
        // Ключ неверный — сбросить и показать форму
        localStorage.removeItem(KEY_STORE);
        showAuthModal('Неверный ключ доступа. Введите снова:');
        throw new Error('Unauthorized');
      }
      return res;
    });
  };

  // Показать модал ввода ключа
  function showAuthModal(msg) {
    document.getElementById('auth-overlay').classList.add('open');
    if (msg) document.getElementById('auth-msg').textContent = msg;
  }

  // Сохранить ключ и закрыть модал
  window.saveAdminKey = function () {
    const val = document.getElementById('auth-key-input').value.trim();
    if (!val) return;
    localStorage.setItem(KEY_STORE, val);
    document.getElementById('auth-overlay').classList.remove('open');
    window.location.reload();
  };

  // При нажатии Enter в поле
  window.authKeyEnter = function (e) {
    if (e.key === 'Enter') window.saveAdminKey();
  };

  // Инициализация: проверить нужен ли ключ
  document.addEventListener('DOMContentLoaded', async function () {
    // Если ADMIN_KEY не задан на сервере — ключ не нужен
    const res = await fetch('/api/auth/check').catch(() => null);
    if (!res) return;
    const data = await res.json().catch(() => ({}));
    if (!data.required) return; // Авторизация не нужна

    const key = window.getAdminKey();
    if (!key) {
      showAuthModal('Введите ключ доступа для входа в систему:');
    }
  });
})();
