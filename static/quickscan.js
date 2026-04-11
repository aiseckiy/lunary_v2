// ── Quick Scan — глобальный сканер в mobile header ────────────────────────────
(function() {

// Инжектим HTML один раз
const html = `
<div class="qs-overlay" id="qs-overlay" onclick="if(event.target===this)closeQuickScan()">
  <div class="qs-sheet">
    <div class="qs-header">
      <div class="qs-title">📷 Быстрое сканирование</div>
      <button class="qs-close" onclick="closeQuickScan()">✕</button>
    </div>
    <div class="qs-tabs">
      <div class="qs-tab active" id="qs-tab-cam" onclick="qsSwitchTab('cam')">📷 Камера</div>
      <div class="qs-tab" id="qs-tab-manual" onclick="qsSwitchTab('manual')">⌨️ Ввод</div>
    </div>
    <!-- Камера -->
    <div id="qs-section-cam">
      <div id="qs-reader" style="width:100%;border-radius:12px;overflow:hidden;margin-bottom:12px"></div>
      <button id="qs-btn-start" onclick="qsStartCamera()" style="width:100%;background:var(--accent);color:#fff;border:none;border-radius:10px;padding:13px;font-size:15px;font-weight:600;cursor:pointer">▶ Запустить камеру</button>
    </div>
    <!-- Ввод вручную -->
    <div id="qs-section-manual" style="display:none">
      <input class="qs-input" id="qs-input" placeholder="Штрихкод, артикул или название..." oninput="qsOnInput()" autocomplete="off">
    </div>
    <!-- Блок "присвоить штрихкод" -->
    <div id="qs-assign-box" style="display:none" class="qs-assign-box">
      <div class="qs-assign-code" id="qs-assign-code"></div>
      <div class="qs-assign-label">Штрихкод не найден. Найди товар и присвой ему этот код:</div>
      <input class="qs-input" id="qs-assign-search" placeholder="Поиск товара..." oninput="qsAssignSearch()" autocomplete="off" style="margin-bottom:8px">
    </div>
    <div class="qs-results" id="qs-results"></div>
  </div>
</div>`;
document.body.insertAdjacentHTML('beforeend', html);

let qsScanner = null;
let qsSearchTimer = null;
let qsPendingBarcode = null;
let qsAssignTimer = null;

window.openQuickScan = function() {
  document.getElementById('qs-overlay').classList.add('open');
  qsPendingBarcode = null;
  document.getElementById('qs-assign-box').style.display = 'none';
  document.getElementById('qs-results').innerHTML = '';
  qsSwitchTab('cam');
};

window.closeQuickScan = function() {
  document.getElementById('qs-overlay').classList.remove('open');
  if (qsScanner) { qsScanner.stop().catch(() => {}); qsScanner = null; }
  document.getElementById('qs-reader').innerHTML = '';
  document.getElementById('qs-btn-start') && (document.getElementById('qs-btn-start').style.display = 'block');
};

window.qsSwitchTab = function(tab) {
  document.getElementById('qs-tab-cam').classList.toggle('active', tab === 'cam');
  document.getElementById('qs-tab-manual').classList.toggle('active', tab === 'manual');
  document.getElementById('qs-section-cam').style.display = tab === 'cam' ? '' : 'none';
  document.getElementById('qs-section-manual').style.display = tab === 'manual' ? '' : 'none';
  if (tab === 'manual') setTimeout(() => document.getElementById('qs-input').focus(), 100);
};

window.qsStartCamera = function() {
  if (!window.isSecureContext) { qsSwitchTab('manual'); return; }
  document.getElementById('qs-btn-start').style.display = 'none';
  if (typeof Html5Qrcode === 'undefined') {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html5-qrcode/2.3.8/html5-qrcode.min.js';
    s.onload = () => qsInitCamera();
    document.head.appendChild(s);
  } else {
    qsInitCamera();
  }
};

function qsInitCamera() {
  qsScanner = new Html5Qrcode('qs-reader');
  qsScanner.start(
    { facingMode: 'environment' },
    { fps: 10, qrbox: { width: 280, height: 120 } },
    qsOnBarcode,
    () => {}
  ).catch(() => {
    qsSwitchTab('manual');
    document.getElementById('qs-btn-start').style.display = 'block';
  });
}

function qsBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 1800;
    g.gain.setValueAtTime(0.3, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
    o.connect(g); g.connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.15);
  } catch {}
}

async function qsOnBarcode(code) {
  qsBeep();
  if (qsScanner) { await qsScanner.stop(); qsScanner = null; document.getElementById('qs-reader').innerHTML = ''; }
  const res = await fetch(`/api/products/barcode/${encodeURIComponent(code)}`);
  if (res.ok) {
    const p = await res.json();
    closeQuickScan();
    // Если на странице склада — открыть модал, иначе перейти
    if (typeof openMoveModal === 'function') openMoveModal(p.id);
    else window.location.href = `/?open=${p.id}`;
  } else {
    qsShowAssign(code);
  }
}

function qsShowAssign(code) {
  qsPendingBarcode = code;
  document.getElementById('qs-assign-code').textContent = `Штрихкод: ${code}`;
  document.getElementById('qs-assign-box').style.display = 'block';
  document.getElementById('qs-assign-search').value = '';
  document.getElementById('qs-results').innerHTML = '';
  document.getElementById('qs-btn-start') && (document.getElementById('qs-btn-start').style.display = 'block');
  setTimeout(() => document.getElementById('qs-assign-search').focus(), 100);
}

window.qsAssignSearch = function() {
  clearTimeout(qsAssignTimer);
  const q = document.getElementById('qs-assign-search').value.trim();
  if (q.length < 2) { document.getElementById('qs-results').innerHTML = ''; return; }
  qsAssignTimer = setTimeout(() => qsDoSearch(q, true), 300);
};

window.qsOnInput = function() {
  clearTimeout(qsSearchTimer);
  const q = document.getElementById('qs-input').value.trim();
  if (q.length < 2) { document.getElementById('qs-results').innerHTML = ''; return; }
  qsSearchTimer = setTimeout(() => qsDoSearch(q, false), 300);
};

async function qsDoSearch(q, isAssign) {
  const res = await fetch(`/api/products/search?q=${encodeURIComponent(q)}`);
  const products = await res.json();
  const container = document.getElementById('qs-results');
  if (!products.length) { container.innerHTML = '<div style="color:var(--text3);font-size:13px;padding:8px 0">Ничего не найдено</div>'; return; }
  container.innerHTML = products.slice(0, 8).map(p => `
    <div class="qs-result" onclick="${isAssign ? `qsAssignBarcode(${p.id})` : `qsOpenProduct(${p.id})`}">
      <div style="flex:1">
        <div class="qs-rname">${p.name}</div>
        <div class="qs-rmeta">${p.sku}${p.barcode ? ' • ' + p.barcode : ''}${isAssign ? '' : ''}</div>
      </div>
      ${isAssign
        ? `<button style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap">Присвоить</button>`
        : `<div class="qs-rstock ${p.stock <= (p.min_stock||5) ? 'low' : 'ok'}">${p.stock}</div>`
      }
    </div>`).join('');
}

window.qsAssignBarcode = async function(productId) {
  if (!qsPendingBarcode) return;
  const res = await fetch(`/api/products/${productId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ barcode: qsPendingBarcode })
  });
  if (res.ok) {
    closeQuickScan();
    qsToast(`✅ Штрихкод присвоен`);
    if (typeof load === 'function') load();
  } else {
    qsToast('Ошибка сохранения', true);
  }
};

window.qsOpenProduct = function(id) {
  closeQuickScan();
  if (typeof openMoveModal === 'function') openMoveModal(id);
  else window.location.href = `/?open=${id}`;
};

function qsToast(msg, isError) {
  let t = document.getElementById('qs-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'qs-toast';
    t.style.cssText = 'position:fixed;bottom:90px;left:50%;transform:translateX(-50%);padding:11px 20px;border-radius:10px;font-size:14px;font-weight:600;color:#fff;z-index:700;pointer-events:none;opacity:0;transition:opacity .3s;white-space:nowrap';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.background = isError ? 'var(--red)' : 'var(--green)';
  t.style.opacity = '1';
  setTimeout(() => { t.style.opacity = '0'; }, 2500);
}

})();
