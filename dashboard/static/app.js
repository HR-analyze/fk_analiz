const PIE_COLORS = ['#e8514f', '#f0a63c', '#7c5cf5', '#38bdf8', '#ec4899', '#3fbf94',
                    '#f97316', '#0ea5e9', '#a855f7', '#64748b', '#14b8a6', '#f43f5e'];
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const num = (v) => Number(v || 0).toLocaleString('ru-RU');
const dec = (v) => Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 });

let meta = null;
let timer = null;

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

function barChart(items, opts = {}) {
  if (!items.length || items.every((i) => !i.value)) return '<div class="empty">Нет данных за выбранный период</div>';
  const color = opts.color || '#3b6ef5';
  const rotate = opts.rotate === undefined ? items.length > 12 : !!opts.rotate;
  const W = 760, H = opts.height || 310;
  const padL = 64, padR = 14, padT = 14, padB = rotate ? 92 : 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = niceMax(Math.max(...items.map((i) => i.value)));
  const slot = plotW / items.length;
  const barW = Math.max(4, Math.min(slot * 0.62, 62));
  const parts = [];

  for (let t = 0; t <= 4; t++) {
    const y = padT + plotH - (plotH * t) / 4;
    const v = (max * t) / 4;
    parts.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#eef1f6" stroke-width="1"/>`);
    parts.push(`<text x="${padL - 10}" y="${y + 4}" text-anchor="end" font-size="11.5" fill="#8a94a6">${esc(dec(v))}</text>`);
  }

  items.forEach((item, i) => {
    const h = max ? (item.value / max) * plotH : 0;
    const x = padL + slot * i + (slot - barW) / 2;
    const y = padT + plotH - h;
    parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(h, item.value > 0 ? 2 : 0).toFixed(1)}" rx="4" fill="${color}"><title>${esc(item.label)}: ${esc(dec(item.value))}</title></rect>`);
    const lx = padL + slot * i + slot / 2;
    if (rotate) {
      parts.push(`<text transform="translate(${lx.toFixed(1)},${padT + plotH + 14}) rotate(-40)" text-anchor="end" font-size="11.5" fill="#6b7687">${esc(item.label)}</text>`);
    } else {
      parts.push(`<text x="${lx.toFixed(1)}" y="${padT + plotH + 20}" text-anchor="middle" font-size="11.5" fill="#6b7687">${esc(item.label)}</text>`);
    }
  });

  parts.push(`<line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="#d9dfe8" stroke-width="1"/>`);
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${parts.join('')}</svg>`;
}

function pieChart(items, opts = {}) {
  const data = items.filter((i) => i.value > 0);
  if (!data.length) return '<div class="empty">Нет данных за выбранный период</div>';
  const colors = opts.colors || PIE_COLORS;
  const W = 760, H = 300, cx = W / 2, cy = H / 2 - 4;
  const r = 118, inner = opts.donut ? 68 : 0;
  const total = data.reduce((s, i) => s + i.value, 0);
  const parts = [];

  if (data.length === 1) {
    parts.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${colors[0]}"><title>${esc(data[0].label)}: ${esc(dec(data[0].value))}</title></circle>`);
    if (inner) parts.push(`<circle cx="${cx}" cy="${cy}" r="${inner}" fill="#fff"/>`);
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
      parts.push(`<path d="${d}" fill="${colors[i % colors.length]}" stroke="#fff" stroke-width="1.5"><title>${esc(item.label)}: ${esc(dec(item.value))} (${pct}%)</title></path>`);
      angle = end;
    });
  }

  const legend = data.map((item, i) =>
    `<span><i style="background:${colors[i % colors.length]}"></i>${esc(item.label)} — ${esc(dec(item.value))} (${((item.value / total) * 100).toFixed(1)}%)</span>`
  ).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${parts.join('')}</svg><div class="legend">${legend}</div>`;
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

function renderTable(el, columns, rows, cells) {
  if (!rows.length) {
    el.innerHTML = `<tbody><tr><td class="empty" colspan="${columns.length}">Нет данных за выбранный период</td></tr></tbody>`;
    return;
  }
  const head = columns.map((c) => `<th${c.num ? ' class="num"' : ''}>${esc(c.title)}</th>`).join('');
  const body = rows.map((row) => '<tr>' + cells(row).map((v, i) =>
    `<td${columns[i].num ? ' class="num"' : ''}>${v}</td>`).join('') + '</tr>').join('');
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

function shortDate(iso) {
  return iso ? iso.slice(5) : '—';
}

function render(data) {
  renderKpis(data.kpi);
  $('chartDaily').innerHTML = barChart(data.daily.map((d) => ({ label: shortDate(d.date), value: d.qty })), { color: '#3b6ef5' });
  $('chartHourly').innerHTML = barChart(data.hourly, { color: '#7c5cf5', rotate: true });
  $('chartEquipment').innerHTML = barChart(data.by_equipment, { color: '#3fbf94', rotate: true });
  $('chartProducts').innerHTML = barChart(data.top_products, { color: '#f0a63c', rotate: true });
  $('chartReasons').innerHTML = pieChart(data.pause_reasons);
  $('chartWork').innerHTML = pieChart(data.work_vs_stop, { donut: true, colors: ['#3b6ef5', '#e8514f'] });

  $('dailyChips').innerHTML = data.daily.length
    ? data.daily.map((d) => `<div class="chip"><b>${esc(shortDate(d.date))}</b> · оп: ${num(d.ops)}, шт: ${num(d.qty)}, ср.длит: ${num(d.avg_duration)} мин, простои: ${num(d.stop)} мин</div>`).join('')
    : '<div class="empty">Нет данных за выбранный период</div>';

  renderTable($('equipmentTable'),
    [{ title: 'Оборудование' }, { title: 'Операций', num: true }, { title: 'Выпуск (шт)', num: true },
     { title: 'Работа', num: true }, { title: 'Простои', num: true },
     { title: 'Ср. произв. (шт/ч)', num: true }, { title: 'Загрузка', num: true }],
    data.equipment_timings,
    (r) => [`<span class="tag">${esc(r.equipment)}</span>`, num(r.ops), num(r.qty), hm(r.work), hm(r.stop), dec(r.avg_rate), dec(r.utilization) + '%']);

  renderTable($('productTable'),
    [{ title: 'Продукт' }, { title: 'Операций', num: true }, { title: 'Выпуск (шт)', num: true },
     { title: 'Ср. длит. (мин)', num: true }, { title: 'Ср. произв. (шт/ч)', num: true }, { title: 'Диапазон старта' }],
    data.product_timings,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, num(r.ops), num(r.qty), dec(r.avg_duration), dec(r.avg_rate), esc(r.start_range)]);

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
  $('sourceLine').textContent = `Источник: ${modes[src.mode] || src.mode || '—'} · снимок от ${fetched} · автообновление каждые ${meta ? meta.refresh_seconds : 60} с`;
  const shiftLabel = { all: 'все смены', day: 'дневная смена', night: 'ночная смена' }[$('shift').value] || 'все смены';
  $('periodLine').textContent = `Период ${$('dateFrom').value || 'начало'} – ${$('dateTo').value || 'сегодня'} · ${shiftLabel} · операций: ${num(data.kpi.operations)} · выпуск: ${num(data.kpi.quantity)} шт · обновлено ${fetched}`;
}

function showAlert(message) {
  const el = $('alert');
  if (!message) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = '⚠️ ' + message;
}

function query(force) {
  const p = new URLSearchParams({
    date_from: $('dateFrom').value,
    date_to: $('dateTo').value,
    equipment: $('equipment').value,
    product: $('product').value,
    shift: $('shift').value,
  });
  if (force) p.set('refresh', '1');
  return p.toString();
}

async function loadSummary(force) {
  try {
    const r = await fetch('/api/summary?' + query(force), { cache: 'no-store' });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || 'сервис недоступен');
    showAlert(data.source && data.source.error ? 'Данные показаны из кеша: ' + data.source.error : '');
    render(data);
  } catch (e) {
    showAlert('Не удалось загрузить данные: ' + e.message);
  }
}

function fillSelect(el, values, all) {
  el.innerHTML = `<option value="all">${all}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}

function defaultRange() {
  const max = meta.date_max || new Date().toISOString().slice(0, 10);
  const from = new Date(max + 'T00:00:00');
  from.setDate(from.getDate() - 6);
  const min = meta.date_min || '';
  const iso = from.toISOString().slice(0, 10);
  return [min && iso < min ? min : iso, max];
}

function resetFilters() {
  const [from, to] = defaultRange();
  $('dateFrom').value = from;
  $('dateTo').value = to;
  $('equipment').value = 'all';
  $('product').value = 'all';
  $('shift').value = 'all';
}

async function init() {
  try {
    const r = await fetch('/api/meta', { cache: 'no-store' });
    meta = await r.json();
    if (!r.ok || !meta.ok) throw new Error(meta.error || 'сервис недоступен');
  } catch (e) {
    showAlert('Не удалось получить справочники: ' + e.message);
    meta = { equipment: [], products: [], refresh_seconds: 60 };
  }
  fillSelect($('equipment'), meta.equipment || [], 'Все');
  fillSelect($('product'), meta.products || [], 'Все');
  if (meta.date_min) { $('dateFrom').min = meta.date_min; $('dateTo').min = meta.date_min; }
  if (meta.date_max) { $('dateFrom').max = meta.date_max; $('dateTo').max = meta.date_max; }
  resetFilters();
  await loadSummary(false);

  $('apply').addEventListener('click', () => loadSummary(false));
  $('reset').addEventListener('click', () => { resetFilters(); loadSummary(false); });
  $('refresh').addEventListener('click', () => loadSummary(true));
  ['dateFrom', 'dateTo', 'equipment', 'product', 'shift'].forEach((id) =>
    $(id).addEventListener('change', () => loadSummary(false)));

  const every = Math.max(15, Number(meta.refresh_seconds) || 60) * 1000;
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) loadSummary(false); }, every);
}

init();
