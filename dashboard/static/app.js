// Валидированная категориальная палитра (dataviz: все гейты пройдены на светлой поверхности).
const PIE_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300',
                    '#4a3aa7', '#e34948', '#0f766e', '#9a3412', '#6d28d9', '#be123c'];
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const num = (v) => Number(v || 0).toLocaleString('ru-RU');
const dec = (v) => Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
const isNarrow = () => window.innerWidth < 640;

const FILTERS = {
  dateFrom: '', dateTo: '', equipment: 'all', product: 'all',
  employee: 'all', shift: 'all', hour: '',
};
const FILTER_LABELS = {
  equipment: 'Оборудование', product: 'Продукт', employee: 'Сотрудник',
  shift: 'Смена', hour: 'Час', date: 'Дата',
};
const SHIFT_NAMES = { all: 'все смены', day: 'дневная смена', night: 'ночная смена' };

let meta = null;
let timer = null;
let lastData = null;

function formatMoment(iso) {
  if (!iso) return '—';
  const opts = { dateStyle: 'short', timeStyle: 'medium' };
  if (meta && meta.timezone) opts.timeZone = meta.timezone;
  try {
    return new Date(iso).toLocaleString('ru-RU', opts);
  } catch (e) {
    return new Date(iso).toLocaleString('ru-RU');
  }
}

function hm(minutes) {
  const m = Math.round(Number(minutes) || 0);
  if (m < 60) return m + ' мин';
  return Math.floor(m / 60) + ' ч ' + String(m % 60).padStart(2, '0') + ' мин';
}

function niceMax(value) {
  if (!(value > 0)) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const norm = value / exp;
  const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => norm <= s) || 10;
  return step * exp;
}

/* ─── графики ─────────────────────────────────────────────────────────────── */

function barChart(items, opts = {}) {
  if (!items.length || items.every((i) => !i.value)) return '<div class="empty">Нет данных за выбранный период</div>';
  const narrow = isNarrow();
  const color = opts.color || '#3b6ef5';
  const rotate = opts.rotate === undefined ? items.length > 12 : !!opts.rotate;
  const W = narrow ? 460 : 760;
  const H = opts.height || (narrow ? 260 : 310);
  const padL = narrow ? 52 : 64;
  const padR = 10;
  const padT = 12;
  const padB = rotate ? (narrow ? 78 : 92) : 38;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const max = niceMax(Math.max(...items.map((i) => i.value)));
  const slot = plotW / items.length;
  const barW = Math.max(4, Math.min(slot * 0.62, 62));
  const labelEvery = narrow && items.length > 14 ? Math.ceil(items.length / 8) : 1;
  const font = narrow ? 12 : 11.5;
  const parts = [];

  for (let t = 0; t <= 4; t++) {
    const y = padT + plotH - (plotH * t) / 4;
    parts.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#eef1f6" stroke-width="1"/>`);
    parts.push(`<text x="${padL - 8}" y="${y + 4}" text-anchor="end" font-size="${font - 0.5}" fill="#8a94a6">${esc(dec((max * t) / 4))}</text>`);
  }

  items.forEach((item, i) => {
    const h = max ? (item.value / max) * plotH : 0;
    const x = padL + slot * i + (slot - barW) / 2;
    const y = padT + plotH - h;
    const active = opts.activeValue !== undefined && String(item.key ?? item.label) === String(opts.activeValue);
    const attrs = opts.filterKey
      ? ` data-filter="${esc(opts.filterKey)}" data-value="${esc(item.key ?? item.label)}"`
      : '';
    const label = `${item.label}: ${dec(item.value)}${opts.unit ? ' ' + opts.unit : ''}`;
    // Прозрачная зона на всю колонку — палец на мобилке попадает даже мимо самого столбца.
    parts.push(`<rect class="hit${opts.filterKey ? ' clickable' : ''}${active ? ' active' : ''}" x="${(padL + slot * i).toFixed(1)}" y="${padT}" width="${slot.toFixed(1)}" height="${plotH}" fill="transparent"${attrs} data-tip="${esc(label)}"/>`);
    parts.push(`<rect class="bar${active ? ' active' : ''}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(h, item.value > 0 ? 2 : 0).toFixed(1)}" rx="4" fill="${color}" pointer-events="none"/>`);
    if (i % labelEvery === 0) {
      const lx = padL + slot * i + slot / 2;
      if (rotate) {
        parts.push(`<text transform="translate(${lx.toFixed(1)},${padT + plotH + 13}) rotate(-40)" text-anchor="end" font-size="${font}" fill="#6b7687" pointer-events="none">${esc(item.label)}</text>`);
      } else {
        parts.push(`<text x="${lx.toFixed(1)}" y="${padT + plotH + 19}" text-anchor="middle" font-size="${font}" fill="#6b7687" pointer-events="none">${esc(item.label)}</text>`);
      }
    }
  });

  parts.push(`<line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="#d9dfe8" stroke-width="1" pointer-events="none"/>`);
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${parts.join('')}</svg>`;
}

function pieChart(items, opts = {}) {
  const data = items.filter((i) => i.value > 0);
  if (!data.length) return '<div class="empty">Нет данных за выбранный период</div>';
  const narrow = isNarrow();
  const colors = opts.colors || PIE_COLORS;
  const W = narrow ? 440 : 760;
  const H = narrow ? 250 : 300;
  const cx = W / 2, cy = H / 2 - 4;
  const r = narrow ? 100 : 118;
  const inner = opts.donut ? r * 0.58 : 0;
  const total = data.reduce((s, i) => s + i.value, 0);
  const parts = [];

  if (data.length === 1) {
    parts.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${colors[0]}" data-tip="${esc(data[0].label + ': ' + dec(data[0].value))}"/>`);
    if (inner) parts.push(`<circle cx="${cx}" cy="${cy}" r="${inner}" fill="#fff" pointer-events="none"/>`);
  } else {
    let angle = -Math.PI / 2;
    data.forEach((item, i) => {
      const sweep = (item.value / total) * Math.PI * 2;
      const end = angle + sweep;
      const large = sweep > Math.PI ? 1 : 0;
      const p = (rad, a) => `${(cx + rad * Math.cos(a)).toFixed(2)} ${(cy + rad * Math.sin(a)).toFixed(2)}`;
      const d = inner
        ? `M ${p(r, angle)} A ${r} ${r} 0 ${large} 1 ${p(r, end)} L ${p(inner, end)} A ${inner} ${inner} 0 ${large} 0 ${p(inner, angle)} Z`
        : `M ${cx} ${cy} L ${p(r, angle)} A ${r} ${r} 0 ${large} 1 ${p(r, end)} Z`;
      const pct = ((item.value / total) * 100).toFixed(1);
      parts.push(`<path class="slice" d="${d}" fill="${colors[i % colors.length]}" stroke="#fff" stroke-width="2" data-tip="${esc(`${item.label}: ${dec(item.value)} (${pct}%)`)}"/>`);
      angle = end;
    });
  }

  const legend = data.map((item, i) =>
    `<span><i style="background:${colors[i % colors.length]}"></i>${esc(item.label)} — ${esc(dec(item.value))} (${((item.value / total) * 100).toFixed(1)}%)</span>`
  ).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${parts.join('')}</svg><div class="legend">${legend}</div>`;
}

/* ─── таблицы и карточки ──────────────────────────────────────────────────── */

function renderTable(el, columns, rows, cells, rowFilter) {
  if (!rows.length) {
    el.innerHTML = `<tbody><tr><td class="empty" colspan="${columns.length}">Нет данных за выбранный период</td></tr></tbody>`;
    return;
  }
  const head = columns.map((c) => `<th${c.num ? ' class="num"' : ''}>${esc(c.title)}</th>`).join('');
  const body = rows.map((row) => {
    const f = rowFilter ? rowFilter(row) : null;
    const active = f && String(FILTERS[f.key]) === String(f.value);
    const attrs = f ? ` class="row-click${active ? ' active' : ''}" data-filter="${esc(f.key)}" data-value="${esc(f.value)}"` : '';
    return `<tr${attrs}>` + cells(row).map((v, i) =>
      `<td${columns[i].num ? ' class="num"' : ''} data-label="${esc(columns[i].title)}">${v}</td>`).join('') + '</tr>';
  }).join('');
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

function renderKpis(k) {
  const cards = [
    ['Операций', num(k.operations), k.open_operations ? `из них открыто: ${num(k.open_operations)}` : 'за период'],
    ['Выпуск (шт)', num(k.quantity), 'всего'],
    ['Средняя длит.', `${num(k.avg_duration)} мин`, 'на операцию'],
    ['Производительность', `${dec(k.avg_rate)} шт/ч`, 'средняя'],
    ['Простои', num(k.pauses), `суммарно ${hm(k.stop_minutes)}`],
    ['Коэф. использования', `${dec(k.utilization)}%`, 'работа / (работа + простои)'],
  ];
  $('kpis').innerHTML = cards.map(([label, value, note]) =>
    `<div class="kpi"><div class="k-label">${esc(label)}</div><div class="k-value">${esc(value)}</div><div class="k-note">${esc(note)}</div></div>`
  ).join('');
}

/* ─── фильтры ─────────────────────────────────────────────────────────────── */

function activeFilters() {
  const out = [];
  if (FILTERS.dateFrom && FILTERS.dateFrom === FILTERS.dateTo) {
    out.push({ key: 'date', value: FILTERS.dateFrom, text: `Дата: ${FILTERS.dateFrom}` });
  }
  ['equipment', 'product', 'employee'].forEach((k) => {
    if (FILTERS[k] && FILTERS[k] !== 'all') out.push({ key: k, value: FILTERS[k], text: `${FILTER_LABELS[k]}: ${FILTERS[k]}` });
  });
  if (FILTERS.shift !== 'all') out.push({ key: 'shift', value: FILTERS.shift, text: `Смена: ${SHIFT_NAMES[FILTERS.shift]}` });
  if (FILTERS.hour !== '') out.push({ key: 'hour', value: FILTERS.hour, text: `Час: ${String(FILTERS.hour).padStart(2, '0')}:00` });
  return out;
}

function renderChips() {
  const list = activeFilters();
  const bar = $('chips');
  bar.hidden = list.length === 0;
  bar.innerHTML = list.map((f) =>
    `<button class="fchip" data-clear="${esc(f.key)}">${esc(f.text)}<i>×</i></button>`).join('')
    + (list.length ? '<button class="fchip clear-all" data-clear="__all">Сбросить всё</button>' : '');
  $('filterHint').textContent = list.length ? list.map((f) => f.text).join(' · ') : 'все данные';
}

function setFilter(key, value) {
  if (key === 'date') {
    const same = FILTERS.dateFrom === value && FILTERS.dateTo === value;
    if (same) { resetDates(); } else { FILTERS.dateFrom = value; FILTERS.dateTo = value; }
  } else if (key === 'hour') {
    FILTERS.hour = String(FILTERS.hour) === String(value) ? '' : String(value);
  } else {
    FILTERS[key] = FILTERS[key] === value ? 'all' : value;
  }
  syncControls();
  loadSummary(false);
}

function clearFilter(key) {
  if (key === '__all') { resetFilters(); }
  else if (key === 'date') { resetDates(); }
  else if (key === 'hour') { FILTERS.hour = ''; }
  else { FILTERS[key] = 'all'; }
  syncControls();
  loadSummary(false);
}

function defaultRange() {
  const max = (meta && meta.date_max) || new Date().toISOString().slice(0, 10);
  const from = new Date(max + 'T00:00:00');
  from.setDate(from.getDate() - 6);
  const min = (meta && meta.date_min) || '';
  const iso = from.toISOString().slice(0, 10);
  return [min && iso < min ? min : iso, max];
}

function resetDates() {
  const [from, to] = defaultRange();
  FILTERS.dateFrom = from;
  FILTERS.dateTo = to;
}

function resetFilters() {
  resetDates();
  FILTERS.equipment = 'all';
  FILTERS.product = 'all';
  FILTERS.employee = 'all';
  FILTERS.shift = 'all';
  FILTERS.hour = '';
}

function syncControls() {
  $('dateFrom').value = FILTERS.dateFrom;
  $('dateTo').value = FILTERS.dateTo;
  $('equipment').value = FILTERS.equipment;
  $('product').value = FILTERS.product;
  $('employee').value = FILTERS.employee;
  $('shift').value = FILTERS.shift;
  $('hour').value = FILTERS.hour;
  renderChips();
}

function readControls() {
  FILTERS.dateFrom = $('dateFrom').value;
  FILTERS.dateTo = $('dateTo').value;
  FILTERS.equipment = $('equipment').value;
  FILTERS.product = $('product').value;
  FILTERS.employee = $('employee').value;
  FILTERS.shift = $('shift').value;
  FILTERS.hour = $('hour').value;
  renderChips();
}

/* ─── рендер ──────────────────────────────────────────────────────────────── */

function render(data) {
  lastData = data;
  renderKpis(data.kpi);

  $('chartDaily').innerHTML = barChart(
    data.daily.map((d) => ({ label: d.date.slice(5), key: d.date, value: d.qty })),
    { color: '#3b6ef5', filterKey: 'date', unit: 'шт',
      activeValue: FILTERS.dateFrom === FILTERS.dateTo ? FILTERS.dateFrom : undefined });

  $('chartHourly').innerHTML = barChart(
    data.hourly.map((h, i) => ({ label: h.label, key: String(i), value: h.value })),
    { color: '#7c5cf5', rotate: true, filterKey: 'hour', unit: 'шт/ч', activeValue: FILTERS.hour });

  const cap = isNarrow() ? 8 : 12;
  $('chartEquipment').innerHTML = barChart(data.by_equipment.slice(0, cap),
    { color: '#3fbf94', rotate: true, filterKey: 'equipment', unit: 'шт', activeValue: FILTERS.equipment });
  $('chartProducts').innerHTML = barChart(data.top_products.slice(0, cap),
    { color: '#f0a63c', rotate: true, filterKey: 'product', unit: 'шт', activeValue: FILTERS.product });
  $('chartEmployees').innerHTML = barChart((data.top_employees || []).slice(0, cap),
    { color: '#2a78d6', rotate: true, filterKey: 'employee', unit: 'шт', activeValue: FILTERS.employee });

  $('chartReasons').innerHTML = pieChart(data.pause_reasons);
  $('chartWork').innerHTML = pieChart(data.work_vs_stop, { donut: true, colors: ['#2a78d6', '#e34948'] });

  $('dailyChips').innerHTML = data.daily.length
    ? data.daily.map((d) => {
        const active = FILTERS.dateFrom === d.date && FILTERS.dateTo === d.date;
        return `<button class="chip${active ? ' active' : ''}" data-filter="date" data-value="${esc(d.date)}"><b>${esc(d.date.slice(5))}</b> · оп: ${num(d.ops)}, шт: ${num(d.qty)}, ср.длит: ${num(d.avg_duration)} мин, простои: ${num(d.stop)} мин</button>`;
      }).join('')
    : '<div class="empty">Нет данных за выбранный период</div>';

  renderTable($('employeeTable'),
    [{ title: 'Сотрудник' }, { title: 'Операций', num: true }, { title: 'Выпуск (шт)', num: true },
     { title: 'Доля', num: true }, { title: 'Ср. длит. (мин)', num: true }, { title: 'Ср. произв. (шт/ч)', num: true },
     { title: 'Простои', num: true }, { title: 'Загрузка', num: true }, { title: 'Дней', num: true }],
    data.employee_timings || [],
    (r) => [`<span class="tag">${esc(r.employee)}</span>`, num(r.ops), num(r.qty), dec(r.share) + '%',
            dec(r.avg_duration), dec(r.avg_rate), `${num(r.pauses)} · ${hm(r.stop)}`, dec(r.utilization) + '%', num(r.days)],
    (r) => ({ key: 'employee', value: r.employee }));

  renderTable($('equipmentTable'),
    [{ title: 'Оборудование' }, { title: 'Операций', num: true }, { title: 'Выпуск (шт)', num: true },
     { title: 'Работа', num: true }, { title: 'Простои', num: true },
     { title: 'Ср. произв. (шт/ч)', num: true }, { title: 'Загрузка', num: true }],
    data.equipment_timings,
    (r) => [`<span class="tag">${esc(r.equipment)}</span>`, num(r.ops), num(r.qty), hm(r.work), hm(r.stop), dec(r.avg_rate), dec(r.utilization) + '%'],
    (r) => ({ key: 'equipment', value: r.equipment }));

  renderTable($('productTable'),
    [{ title: 'Продукт' }, { title: 'Операций', num: true }, { title: 'Выпуск (шт)', num: true },
     { title: 'Ср. длит. (мин)', num: true }, { title: 'Ср. произв. (шт/ч)', num: true }, { title: 'Диапазон старта' }],
    data.product_timings,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, num(r.ops), num(r.qty), dec(r.avg_duration), dec(r.avg_rate), esc(r.start_range)],
    (r) => ({ key: 'product', value: r.product }));

  renderTable($('detailTable'),
    [{ title: 'Дата' }, { title: 'Оборудование' }, { title: 'Продукт' }, { title: 'Старт' },
     { title: 'Финиш' }, { title: 'Кол-во', num: true }, { title: 'Статус' }, { title: 'Оператор' }],
    data.detail,
    (r) => [esc(r.date || '—'), esc(r.equipment), `<span class="tag">${esc(r.product)}</span>`,
            esc(r.start_time || '—'), esc(r.end_time || '—'), num(r.qty),
            r.status === 'closed' ? 'завершена' : esc(r.status), esc(r.user_name)]);

  const src = data.source || {};
  const fetched = formatMoment(src.fetched_at);
  const modes = { db: 'PostgreSQL (только чтение)', api: 'API бота', demo: 'тестовые данные (demo)' };
  const skipped = (data.excluded_dates || []).length ? ` · скрыто дат: ${data.excluded_dates.join(', ')}` : '';
  $('sourceLine').textContent = `Источник: ${modes[src.mode] || src.mode || '—'} · снимок от ${fetched} · автообновление ${meta ? meta.refresh_seconds : 60} с${skipped}`;
  $('periodLine').textContent = `Период ${FILTERS.dateFrom || 'начало'} – ${FILTERS.dateTo || 'сегодня'} · ${SHIFT_NAMES[FILTERS.shift]} · операций: ${num(data.kpi.operations)} · выпуск: ${num(data.kpi.quantity)} шт · обновлено ${fetched}`;
}

function showAlert(message) {
  const el = $('alert');
  if (!message) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = '⚠️ ' + message;
}

function query(force) {
  const p = new URLSearchParams({
    date_from: FILTERS.dateFrom, date_to: FILTERS.dateTo,
    equipment: FILTERS.equipment, product: FILTERS.product,
    employee: FILTERS.employee, shift: FILTERS.shift,
  });
  if (FILTERS.hour !== '') p.set('hour', FILTERS.hour);
  if (force) p.set('refresh', '1');
  return p.toString();
}

async function loadSummary(force) {
  document.body.classList.add('loading');
  try {
    const r = await fetch('/api/summary?' + query(force), { cache: 'no-store' });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || 'сервис недоступен');
    showAlert(data.source && data.source.error ? 'Данные показаны из кеша: ' + data.source.error : '');
    render(data);
  } catch (e) {
    showAlert('Не удалось загрузить данные: ' + e.message);
  } finally {
    document.body.classList.remove('loading');
  }
}

/* ─── тултип ──────────────────────────────────────────────────────────────── */

function moveTip(e, text) {
  const tip = $('tip');
  tip.textContent = text;
  tip.hidden = false;
  const pad = 14;
  const rect = tip.getBoundingClientRect();
  let x = e.clientX + pad;
  let y = e.clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - pad;
  tip.style.left = Math.max(8, x) + 'px';
  tip.style.top = Math.max(8, y) + 'px';
}

function hideTip() { $('tip').hidden = true; }

/* ─── старт ───────────────────────────────────────────────────────────────── */

function fillSelect(el, values, all) {
  el.innerHTML = `<option value="all">${all}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}

function bindEvents() {
  // Клик по столбцу графика, чипу дня или строке таблицы = фильтр.
  document.addEventListener('click', (e) => {
    const clear = e.target.closest('[data-clear]');
    if (clear) { clearFilter(clear.dataset.clear); return; }
    const hit = e.target.closest('[data-filter]');
    if (hit) { hideTip(); setFilter(hit.dataset.filter, hit.dataset.value); }
  });

  document.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return;      // на тапе тултип только мешает
    const mark = e.target.closest('[data-tip]');
    if (mark) moveTip(e, mark.dataset.tip); else hideTip();
  });
  document.addEventListener('pointerleave', hideTip);
  window.addEventListener('blur', hideTip);

  $('apply').addEventListener('click', () => { readControls(); loadSummary(false); });
  $('reset').addEventListener('click', () => { resetFilters(); syncControls(); loadSummary(false); });
  $('refresh').addEventListener('click', () => loadSummary(true));
  ['dateFrom', 'dateTo', 'equipment', 'product', 'employee', 'shift', 'hour'].forEach((id) =>
    $(id).addEventListener('change', () => { readControls(); loadSummary(false); }));

  let width = window.innerWidth;
  window.addEventListener('resize', () => {
    // Перерисовываем только при смене режима — иначе лишние ререндеры на скролле мобилки.
    const narrowNow = window.innerWidth < 640;
    if (narrowNow !== (width < 640) && lastData) render(lastData);
    width = window.innerWidth;
  });
}

async function init() {
  try {
    const r = await fetch('/api/meta', { cache: 'no-store' });
    meta = await r.json();
    if (!r.ok || !meta.ok) throw new Error(meta.error || 'сервис недоступен');
  } catch (e) {
    showAlert('Не удалось получить справочники: ' + e.message);
    meta = { equipment: [], products: [], employees: [], refresh_seconds: 60 };
  }
  fillSelect($('equipment'), meta.equipment || [], 'Все');
  fillSelect($('product'), meta.products || [], 'Все');
  fillSelect($('employee'), meta.employees || [], 'Все');
  $('hour').innerHTML = '<option value="">Все</option>' +
    Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, '0')}:00</option>`).join('');
  if (meta.date_min) { $('dateFrom').min = meta.date_min; $('dateTo').min = meta.date_min; }
  if (meta.date_max) { $('dateFrom').max = meta.date_max; $('dateTo').max = meta.date_max; }
  if (!isNarrow()) $('filterPanel').open = true;

  resetFilters();
  syncControls();
  bindEvents();
  await loadSummary(false);

  const every = Math.max(15, Number(meta.refresh_seconds) || 60) * 1000;
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) loadSummary(false); }, every);
}

init();
