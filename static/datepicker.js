/**
 * Lunary DateRangePicker
 * Кастомный выбор диапазона дат с календарём и пресетами
 *
 * Использование:
 *   const dp = new DateRangePicker({
 *     trigger: '#dp-trigger',      // кнопка-триггер
 *     onApply: (from, to) => {}    // from, to — строки YYYY-MM-DD
 *   });
 */
class DateRangePicker {
  constructor({ trigger, onApply }) {
    this.triggerEl = typeof trigger === 'string' ? document.querySelector(trigger) : trigger;
    this.onApply = onApply;
    this.from = null;
    this.to = null;
    this.selecting = null; // 'from' | 'to'
    this.viewYear = new Date().getFullYear();
    this.viewMonth = new Date().getMonth();
    this._build();
    this._attach();
  }

  _fmt(d) { return d ? d.toISOString().slice(0, 10) : null; }

  _parse(s) {
    if (!s) return null;
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  _build() {
    // overlay backdrop
    this.backdrop = document.createElement('div');
    this.backdrop.className = 'drp2-backdrop';
    this.backdrop.addEventListener('click', () => this.close());

    // panel
    this.panel = document.createElement('div');
    this.panel.className = 'drp2-panel';
    this.panel.innerHTML = `
      <div class="drp2-presets">
        <button class="drp2-preset" data-preset="today">Сегодня</button>
        <button class="drp2-preset" data-preset="yesterday">Вчера</button>
        <button class="drp2-preset" data-preset="week">Последние 7 дней</button>
        <button class="drp2-preset" data-preset="month">Последние 30 дней</button>
        <button class="drp2-preset" data-preset="3month">Последние 3 месяца</button>
        <button class="drp2-preset" data-preset="year">Последний год</button>
        <button class="drp2-preset" data-preset="all">Всё время</button>
      </div>
      <div class="drp2-right">
        <div class="drp2-cal-nav">
          <button class="drp2-nav-btn" id="drp2-prev">‹</button>
          <span class="drp2-cal-title" id="drp2-title"></span>
          <button class="drp2-nav-btn" id="drp2-next">›</button>
        </div>
        <div class="drp2-cal" id="drp2-cal"></div>
        <div class="drp2-footer">
          <span class="drp2-range-label" id="drp2-range-label">Выберите дату начала</span>
          <button class="drp2-apply" id="drp2-apply">Применить</button>
        </div>
      </div>
    `;

    document.body.appendChild(this.backdrop);
    document.body.appendChild(this.panel);

    this.panel.querySelector('#drp2-prev').addEventListener('click', () => { this._shiftMonth(-1); this._renderCal(); });
    this.panel.querySelector('#drp2-next').addEventListener('click', () => { this._shiftMonth(1); this._renderCal(); });
    this.panel.querySelector('#drp2-apply').addEventListener('click', () => this._apply());
    this.panel.querySelectorAll('.drp2-preset').forEach(btn => {
      btn.addEventListener('click', () => this._applyPreset(btn.dataset.preset));
    });
  }

  _shiftMonth(dir) {
    this.viewMonth += dir;
    if (this.viewMonth > 11) { this.viewMonth = 0; this.viewYear++; }
    if (this.viewMonth < 0)  { this.viewMonth = 11; this.viewYear--; }
  }

  _renderCal() {
    const months = ['Январь','Февраль','Март','Апрель','Май','Июнь',
                    'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
    this.panel.querySelector('#drp2-title').textContent = `${months[this.viewMonth]} ${this.viewYear}`;

    const cal = this.panel.querySelector('#drp2-cal');
    const days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    let html = '<div class="drp2-weekdays">' + days.map(d => `<span>${d}</span>`).join('') + '</div><div class="drp2-days">';

    const first = new Date(this.viewYear, this.viewMonth, 1);
    const last  = new Date(this.viewYear, this.viewMonth + 1, 0);
    let startDow = (first.getDay() + 6) % 7; // Пн=0

    for (let i = 0; i < startDow; i++) html += '<span class="drp2-day drp2-empty"></span>';

    const fromD = this._parse(this.from);
    const toD   = this._parse(this.to);
    const today = this._fmt(new Date());

    for (let d = 1; d <= last.getDate(); d++) {
      const date = new Date(this.viewYear, this.viewMonth, d);
      const iso  = this._fmt(date);
      let cls = 'drp2-day';
      if (iso === this.from) cls += ' drp2-day-start';
      if (iso === this.to)   cls += ' drp2-day-end';
      if (fromD && toD && date > fromD && date < toD) cls += ' drp2-day-range';
      if (iso === today) cls += ' drp2-day-today';
      html += `<span class="${cls}" data-date="${iso}">${d}</span>`;
    }
    html += '</div>';
    cal.innerHTML = html;

    cal.querySelectorAll('.drp2-day[data-date]').forEach(el => {
      el.addEventListener('click', () => this._pickDay(el.dataset.date));
    });

    // label
    const lbl = this.panel.querySelector('#drp2-range-label');
    if (!this.from && !this.to) lbl.textContent = 'Выберите дату начала';
    else if (this.from && !this.to) lbl.textContent = `С ${this._label(this.from)} — выберите конец`;
    else lbl.textContent = `${this._label(this.from)} — ${this._label(this.to)}`;
  }

  _label(iso) {
    if (!iso) return '';
    const [y, m, d] = iso.split('-');
    const months = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
    return `${parseInt(d)} ${months[parseInt(m)-1]} ${y}`;
  }

  _pickDay(iso) {
    if (!this.from || (this.from && this.to)) {
      // начать новый выбор
      this.from = iso; this.to = null;
    } else {
      // второй клик
      if (iso < this.from) { this.to = this.from; this.from = iso; }
      else { this.to = iso; }
    }
    this._renderCal();
  }

  _applyPreset(preset) {
    const fmt = d => this._fmt(d);
    const now = new Date();
    const today = fmt(now);
    const shift = (d, days) => { const x = new Date(d); x.setDate(x.getDate() + days); return x; };
    const shiftM = (d, m) => { const x = new Date(d); x.setMonth(x.getMonth() + m); return x; };

    if (preset === 'today')     { this.from = today; this.to = today; }
    else if (preset === 'yesterday') {
      const y = fmt(shift(now, -1)); this.from = y; this.to = y;
    }
    else if (preset === 'week')   { this.from = fmt(shift(now, -6)); this.to = today; }
    else if (preset === 'month')  { this.from = fmt(shiftM(now, -1)); this.to = today; }
    else if (preset === '3month') { this.from = fmt(shiftM(now, -3)); this.to = today; }
    else if (preset === 'year')   { this.from = fmt(shiftM(now, -12)); this.to = today; }
    else if (preset === 'all')    { this.from = null; this.to = null; }

    // обновить вид на месяц from
    if (this.from) {
      const d = this._parse(this.from);
      this.viewYear = d.getFullYear();
      this.viewMonth = d.getMonth();
    }
    this._renderCal();

    // Для пресетов сразу применяем
    this._apply();
  }

  _apply() {
    this.close();
    if (this.onApply) this.onApply(this.from, this.to);
    this._updateTrigger();
  }

  _updateTrigger() {
    if (!this.triggerEl) return;
    if (!this.from && !this.to) {
      this.triggerEl.textContent = '📅 Период';
      this.triggerEl.classList.remove('active');
    } else {
      const label = this.from === this.to
        ? this._label(this.from)
        : `${this._label(this.from)} — ${this._label(this.to || this.from)}`;
      this.triggerEl.textContent = `📅 ${label}`;
      this.triggerEl.classList.add('active');
    }
  }

  _attach() {
    if (!this.triggerEl) return;
    this.triggerEl.addEventListener('click', (e) => {
      e.stopPropagation();
      this.isOpen ? this.close() : this.open();
    });
  }

  open() {
    this.isOpen = true;
    this._renderCal();

    // позиционирование
    const rect = this.triggerEl.getBoundingClientRect();
    const panelW = 560;
    let left = rect.left;
    if (left + panelW > window.innerWidth - 12) left = window.innerWidth - panelW - 12;
    if (left < 12) left = 12;

    let top = rect.bottom + 8;
    if (top + 380 > window.innerHeight) top = rect.top - 380 - 8;

    this.panel.style.left = left + 'px';
    this.panel.style.top = top + window.scrollY + 'px';

    this.panel.classList.add('drp2-open');
    this.backdrop.classList.add('drp2-open');
  }

  close() {
    this.isOpen = false;
    this.panel.classList.remove('drp2-open');
    this.backdrop.classList.remove('drp2-open');
  }

  // Установить диапазон программно
  setRange(from, to) {
    this.from = from;
    this.to = to;
    if (from) {
      const d = this._parse(from);
      this.viewYear = d.getFullYear();
      this.viewMonth = d.getMonth();
    }
    this._updateTrigger();
  }
}
