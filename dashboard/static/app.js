// Валидированная категориальная палитра (dataviz: все гейты пройдены на светлой поверхности).
const PIE_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300',
                    '#4a3aa7', '#e34948', '#0f766e', '#9a3412', '#6d28d9', '#be123c'];
const BELOW_COLOR = '#e34948';   // статусный: час ниже средней
const GHOST_COLOR = '#dde3ec';   // прошлый период: светлый столбец за текущим
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const num = (v) => Number(v || 0).toLocaleString('ru-RU');
const dec = (v) => Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
const isNarrow = () => window.innerWidth < 640;

const FILTERS = {
  dateFrom: '', dateTo: '', equipment: 'all', product: 'all',
  employee: 'all', shift: 'all', hour: '',
  // только у расстойки: тип печи; у расстойки и КЦ: статус; у КЦ: операция
  oven: 'all', status: 'all', operation: 'all',
};
const FILTER_LABELS = {
  equipment: 'Оборудование', product: 'Продукт', employee: 'Сотрудник',
  shift: 'Смена', hour: 'Час', date: 'Дата', oven: 'Печь', status: 'Статус', operation: 'Операция',
};
const SHIFT_NAMES = { all: 'все смены', day: 'дневная смена', night: 'ночная смена' };

let meta = null;
let timer = null;
let lastData = null;

// Источник данных: ФК (по умолчанию), БХМ или Расстойка. У каждого свои справочники и
// единица выпуска: ФК считает штуки, БХМ — килограммы теста, расстойка — штуки после печи.
// У расстойки своя страница (партии из двух этапов), ФК и БХМ делят общую. Выбор помнится в localStorage.
// Названия разделов: ФК — формовочный, БХМ — тестозаготовочный, расстойка — печной, КЦ — кондитерский цех, Гант — «Тайминги».
const SOURCES = { fk: { title: 'Формовочный цех', qty: 'шт' }, bhm: { title: 'Тестозаготовочный цех', qty: 'кг' },
                  proofing: { title: 'Печной цех', qty: 'шт', kind: 'proofing' },
                  kc: { title: 'Кондитерский цех', qty: 'шт', kind: 'kc' },
                  gantt: { title: 'Тайминги', qty: 'шт', kind: 'gantt' } };
const isProofing = () => SOURCE === 'proofing';
const isKc = () => SOURCE === 'kc';
const isGantt = () => SOURCE === 'gantt';
// Разделы со своей страницей: открываются и без API, ошибка видна только у них.
const OWN_PAGE = ['proofing', 'kc', 'gantt'];
const hasOwnPage = (src = SOURCE) => OWN_PAGE.includes(src);

function showSectionError(message) {
  if (isGantt()) showGanttError(message);
  else if (isKc()) showKcError(message);
  else showProofError(message);
}
let SOURCE = 'fk';
let UNIT = 'шт';
let RATE = 'шт/ч';
// Фильтры живут отдельно у каждого источника: продукты и сотрудники у ФК и БХМ разные.
const filtersBySource = {};
// Ответ, пришедший после переключения источника, рисовать нельзя — он про другой цех.
let loadSeq = 0;

const store = {
  get(k) { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

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

/* ─── сравнение с прошлым (LFL) ───────────────────────────────────────────── */

// better: 1 — рост хорошо, -1 — рост плохо (простои), 0 — без оценки (длительность, число операций).
// mode: pct — проценты, pp — процентные пункты, h — разница в часах.
function trend(change, better = 1, mode = 'pct') {
  if (change === null || change === undefined) return '<span class="tr flat">—</span>';
  const unit = { pct: '%', pp: ' п.п.', h: ' ч' }[mode];
  const flat = Math.abs(change) < 0.05;
  const cls = flat || !better ? 'flat' : ((change > 0) === (better > 0) ? 'good' : 'bad');
  const text = flat ? (mode === 'h' ? 'без изм.' : '0' + unit)
                    : `${change > 0 ? '▲' : '▼'} ${mode === 'h' ? Math.abs(change) : dec(Math.abs(change))}${unit}`;
  return `<span class="tr ${cls}">${esc(text)}</span>`;
}

function pctText(change) {
  if (change === null || change === undefined) return 'не с чем сравнить';
  return (change > 0 ? '+' : change < 0 ? '−' : '') + dec(Math.abs(change)) + '%';
}

function lflVs(lfl) {
  if (lfl.days === 7) return 'к прошлой неделе';
  if (lfl.days === 1) return 'к прошлому дню';
  return `к пред. ${lfl.days} дн.`;
}

function renderLflCaption(lfl) {
  const el = $('lflCaption');
  if (!lfl || !lfl.available) {
    el.hidden = false;
    el.textContent = 'Сравнение с прошлым периодом: ' + ((lfl && lfl.reason) || 'недоступно');
    return;
  }
  const cut = lfl.partial ? `, последний день до ${lfl.cutoff} — как сегодня` : '';
  const days = lfl.prev_days !== lfl.cur_days
    ? ` · ⚠ дней с данными: сейчас ${lfl.cur_days}, тогда ${lfl.prev_days} — суммы неравные`
    : '';
  el.hidden = false;
  el.textContent = lfl.prev_empty
    ? `За прошлый период ${lfl.prev_label} с этими фильтрами данных нет — сравнивать не с чем.`
    : `▲▼ — изменение к прошлому периоду ${lfl.prev_label} (${lfl.days} дн.${cut})${days}`;
}

function chartLegend(entries) {
  return '<div class="legend chart-legend">' + entries.map(([kind, color, text]) =>
    `<span><i class="lg-${kind}" style="${kind === 'dash' ? 'border-color' : 'background'}:${color}"></i>${esc(text)}</span>`
  ).join('') + '</div>';
}

function renderDailyLfl(lfl) {
  const el = $('dailyLfl');
  // Свежая неделя сверху: на неё смотрят первой.
  const weeks = lfl && lfl.available ? (lfl.weeks || []).slice().reverse() : [];
  if (!weeks.length) { el.innerHTML = ''; return; }
  const top = Math.max(1, ...weeks.map((w) => w.qty));
  let uneven = false;
  const rows = weeks.map((w, i) => {
    const warn = w.base_days > 0 && w.days !== w.base_days;
    uneven = uneven || warn;
    const tip = `${w.label}: ${num(w.qty)} ${UNIT}, простоев ${num(w.pauses)} · ` +
      (w.base_days
        ? `неделя раньше (${w.base_label}${w.partial ? ' до ' + lfl.cutoff : ''}): ${num(w.base_qty)} ${UNIT}, простоев ${num(w.base_pauses)}`
        : `за ${w.base_label} данных нет`) +
      (warn ? ` · дней с данными ${w.days} и ${w.base_days}` : '');
    // Синим — самая свежая неделя периода, даже если он закончился не сегодня;
    // partial только про подпись «до ЧЧ:ММ».
    return `<div class="wk${i === 0 ? ' now' : ''}" data-tip="${esc(tip)}">` +
      `<div class="wk-range">${esc(w.label)}${warn ? '<sup title="Разное число дней с данными">⚠</sup>' : ''}` +
      (w.partial ? `<small>до ${esc(lfl.cutoff)}</small>` : '') + '</div>' +
      `<div class="wk-bar"><i style="width:${(w.qty / top * 100).toFixed(1)}%"></i></div>` +
      `<div class="wk-val"><b>${esc(num(w.qty))}</b><span>${esc(UNIT)}</span>${trend(w.qty_change, 1)}</div>` +
      `<div class="wk-stop">простоев ${esc(num(w.pauses))} ${trend(w.pauses_change, -1)}</div>` +
      '</div>';
  }).join('');
  el.innerHTML = '<div class="lfl-title">Неделя к неделе</div>' +
    `<div class="wk-list">${rows}</div>` +
    (uneven ? '<p class="lfl-note">⚠ — в неделях разное число дней с данными, суммы сравниваются неточно</p>' : '');
}

// Клетка полосы по часам: оттенок — насколько час лучше или хуже прошлого периода.
function heatTone(change) {
  if (change === null || change === undefined) return 'none';
  if (change >= 10) return 'up2';
  if (change >= 2) return 'up1';
  if (change <= -10) return 'down2';
  if (change <= -2) return 'down1';
  return 'same';
}

function renderHourlyLfl(lfl, data) {
  const el = $('hourlyLfl');
  if (!data.hourly_target) { el.innerHTML = ''; return; }
  const ok = lfl && lfl.available && !lfl.prev_empty;
  const h = ok ? lfl.hourly : null;
  const stat = (label, value, extra) =>
    `<div class="hstat"><div class="hs-label">${esc(label)}</div><div class="hs-value">${value}</div>` +
    `<div class="hs-extra">${extra}</div></div>`;
  const stats = '<div class="hstats">' +
    stat(`Средняя, ${RATE}`, esc(dec(data.hourly_target)),
         ok ? `${trend(h.target_change, 1)}<span>было ${esc(dec(h.prev_target))}</span>` : '<span>за период</span>') +
    stat('Часов ниже средней', esc(num(data.hourly_below)),
         ok ? `${trend(data.hourly_below - h.prev_below, -1, 'h')}<span>было ${esc(num(h.prev_below))}</span>` : '<span>красные столбцы</span>') +
    '</div>';
  if (!ok) { el.innerHTML = stats; return; }
  const cells = data.hourly.map((x, i) => {
    const c = x.lfl_change;
    const text = c === null || c === undefined ? '—' : (c > 0 ? '+' : c < 0 ? '−' : '') + Math.round(Math.abs(c));
    const tip = `${x.label}: ${dec(x.value)} ${RATE} · прошлый период: ${dec(x.lfl_value)} ${RATE}, ${pctText(c)}`;
    return `<div class="hc ${heatTone(c)}" data-tip="${esc(tip)}"><span>${String(i).padStart(2, '0')}</span><b>${esc(text)}</b></div>`;
  }).join('');
  el.innerHTML = `<div class="lfl-title">К прошлому периоду ${esc(lfl.prev_label)}</div>` + stats +
    `<div class="heat-head"><span>По часам, % к тому же часу</span><span>лучше в <b>${num(h.better)}</b> ч · хуже в <b>${num(h.worse)}</b> ч</span></div>` +
    `<div class="heat">${cells}</div>` +
    '<div class="heat-key"><span><i class="hc up2"></i><i class="hc up1"></i>лучше</span>' +
    '<span><i class="hc same"></i>±2%</span><span><i class="hc down1"></i><i class="hc down2"></i>хуже</span>' +
    '<span class="hk-note">ярче — больше 10%</span></div>';
}

/* ─── графики ─────────────────────────────────────────────────────────────── */

function barChart(items, opts = {}) {
  if (!items.length || items.every((i) => !i.value)) return '<div class="empty">Нет данных за выбранный период</div>';
  const narrow = isNarrow();
  const color = opts.color || '#3b6ef5';
  const rotate = opts.rotate === undefined ? items.length > 12 : !!opts.rotate;
  const W = narrow ? 460 : 760;
  const H = opts.height || (narrow ? 260 : 310);
  const padL = narrow ? 56 : 64;
  const padR = narrow ? 14 : 10;
  const padT = 12;
  // Повёрнутым подписям место по самой длинной: «00:00» не требует запаса под названия линий.
  const longest = Math.max(...items.map((i) => String(i.label).length));
  const padB = rotate ? Math.min(narrow ? 78 : 92, Math.round(26 + longest * (narrow ? 12 : 11.5) * 0.45)) : 38;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const target = Number(opts.target) || 0;
  const compare = opts.compare || [];
  const max = niceMax(Math.max(...items.map((i) => i.value), ...compare.map((v) => Number(v) || 0), target));
  const slot = plotW / items.length;
  // С прошлым периодом рисуем «столбец в столбце»: широкий светлый — было,
  // узкий цветной поверх — стало. Так видно без засечек, выросли или просели.
  const paired = compare.length > 0;
  const barW = Math.max(4, Math.min(slot * (paired ? 0.74 : 0.62), 64));
  const curW = paired ? Math.max(3, barW * 0.58) : barW;
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
    const x = padL + slot * i + (slot - curW) / 2;
    const y = padT + plotH - h;
    const below = target > 0 && item.value > 0 && item.value < target;
    const fill = below ? BELOW_COLOR : color;
    const active = opts.activeValue !== undefined && String(item.key ?? item.label) === String(opts.activeValue);
    const attrs = opts.filterKey
      ? ` data-filter="${esc(opts.filterKey)}" data-value="${esc(item.key ?? item.label)}" role="button" tabindex="0"`
      : '';
    const tip = `${item.label}: ${dec(item.value)}${opts.unit ? ' ' + opts.unit : ''}`
      + (below ? ` · ниже средней на ${dec(target - item.value)}` : '')
      + (opts.compareTip ? opts.compareTip(i) : '');
    // Прозрачная зона на всю колонку — палец на мобилке попадает даже мимо самого столбца.
    parts.push(`<rect class="hit${opts.filterKey ? ' clickable' : ''}${active ? ' active' : ''}" x="${(padL + slot * i).toFixed(1)}" y="${padT}" width="${slot.toFixed(1)}" height="${plotH}" fill="transparent"${attrs} data-tip="${esc(tip)}"/>`);
    const was = Number(compare[i]) || 0;
    if (was > 0) {
      const gh = (was / max) * plotH;
      const gx = padL + slot * i + (slot - barW) / 2;
      parts.push(`<rect x="${gx.toFixed(1)}" y="${(padT + plotH - gh).toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(gh, 2).toFixed(1)}" rx="4" fill="${GHOST_COLOR}" pointer-events="none"/>`);
    }
    parts.push(`<rect class="bar${active ? ' active' : ''}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${curW.toFixed(1)}" height="${Math.max(h, item.value > 0 ? 2 : 0).toFixed(1)}" rx="${paired ? 3 : 4}" fill="${fill}"${paired ? ' stroke="#fff" stroke-width="1.5"' : ''} pointer-events="none"/>`);
    if (i % labelEvery === 0) {
      const lx = padL + slot * i + slot / 2;
      if (rotate) {
        const tx = Math.max(lx, padL - 2);
        parts.push(`<text transform="translate(${tx.toFixed(1)},${padT + plotH + 13}) rotate(-40)" text-anchor="end" font-size="${font}" fill="#6b7687" pointer-events="none">${esc(item.label)}</text>`);
      } else {
        parts.push(`<text x="${lx.toFixed(1)}" y="${padT + plotH + 19}" text-anchor="middle" font-size="${font}" fill="#6b7687" pointer-events="none">${esc(item.label)}</text>`);
      }
    }
  });

  if (target > 0) {
    const ty = padT + plotH - (target / max) * plotH;
    parts.push(`<line x1="${padL}" y1="${ty.toFixed(1)}" x2="${W - padR}" y2="${ty.toFixed(1)}" stroke="#101828" stroke-width="1.5" stroke-dasharray="6 4" pointer-events="none"/>`);
    const ly = ty - 7 < padT + 10 ? ty + 15 : ty - 7;
    parts.push(`<text x="${W - padR - 2}" y="${ly.toFixed(1)}" text-anchor="end" font-size="${font}" font-weight="700" fill="#101828" stroke="#fff" stroke-width="4" stroke-linejoin="round" paint-order="stroke" pointer-events="none">средняя ${esc(dec(target))}</text>`);
  }

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

/* ─── таблицы ─────────────────────────────────────────────────────────────── */

function renderTable(el, columns, rows, cells, rowFilter) {
  if (!rows.length) {
    el.innerHTML = `<tbody><tr><td class="empty" colspan="${columns.length}">Нет данных за выбранный период</td></tr></tbody>`;
    return;
  }
  const head = columns.map((c) => `<th${c.num ? ' class="num"' : ''}>${esc(c.title)}</th>`).join('');
  const body = rows.map((row) => {
    const f = rowFilter ? rowFilter(row) : null;
    const active = f && String(FILTERS[f.key]) === String(f.value);
    const attrs = f ? ` class="row-click${active ? ' active' : ''}" data-filter="${esc(f.key)}" data-value="${esc(f.value)}" role="button" tabindex="0"` : '';
    return `<tr${attrs}>` + cells(row).map((v, i) =>
      `<td${columns[i].num ? ' class="num"' : ''} data-label="${esc(columns[i].title)}">${v}</td>`).join('') + '</tr>';
  }).join('');
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

function fitValues(root) {
  // Число и единица не переносятся, поэтому длинное значение ужимаем по ширине карточки.
  root.querySelectorAll('.k-value').forEach((el) => {
    el.style.fontSize = '';
    const base = parseFloat(getComputedStyle(el).fontSize);
    let size = base;
    while (el.scrollWidth > el.clientWidth + 1 && size > base * 0.6) {
      size -= 1;
      el.style.fontSize = size + 'px';
    }
  });
}

function renderKpis(k, lfl) {
  // [подпись, значение, единица, пояснение, ключ LFL, направление «лучше», формат прошлого]
  const cards = [
    ['Операций', num(k.operations), '', k.open_operations ? `из них открыто: ${num(k.open_operations)}` : 'за период', 'operations', 0, num],
    ['Выпуск', num(k.quantity), UNIT, 'всего', 'quantity', 1, (v) => num(v) + ' ' + UNIT],
    ['Средняя длит.', num(k.avg_duration), 'мин', 'на операцию', 'avg_duration', 0, (v) => num(v) + ' мин'],
    ['Производительность', dec(k.avg_rate), RATE, 'средняя', 'avg_rate', 1, (v) => dec(v) + ' ' + RATE],
    ['Простои', num(k.pauses), '', `суммарно ${hm(k.stop_minutes)}`, 'pauses', -1, num],
    ['Коэф. использования', dec(k.utilization), '%', 'работа / (работа + простои)', 'utilization', 1, (v) => dec(v) + '%'],
  ];
  const ok = lfl && lfl.available && !lfl.prev_empty;
  $('kpis').innerHTML = cards.map(([label, value, unit, note, key, better, fmt]) => {
    let delta = '';
    if (ok && lfl.kpi[key]) {
      const c = lfl.kpi[key];
      let tip = `${lfl.prev_label}: ${fmt(c.prev)}`;
      if (key === 'pauses' && lfl.kpi.stop_minutes) {
        const m = lfl.kpi.stop_minutes;
        tip += ` · минут простоя: ${num(k.stop_minutes)} против ${num(m.prev)} (${pctText(m.change)})`;
      }
      if (c.change === null) tip += ' · в прошлом периоде ноль, процент не считается';
      delta = `<div class="k-delta" data-tip="${esc(tip)}">${trend(c.change, better, c.mode)}` +
        `<span class="k-vs">${esc(lflVs(lfl))}</span></div>`;
    }
    return `<div class="kpi"><div class="k-label">${esc(label)}</div>` +
      `<div class="k-value"><span class="k-num">${esc(value)}</span>` +
      (unit ? `<span class="k-unit">${esc(unit)}</span>` : '') + '</div>' +
      `<div class="k-note">${esc(note)}</div>${delta}</div>`;
  }).join('');
  fitValues($('kpis'));
}

/* ─── план / факт ─────────────────────────────────────────────────────────── */

function planClass(done) {
  if (done === null || done === undefined) return '';
  if (done >= 100) return ' ok';
  if (done >= 90) return ' warn';
  return ' bad';
}

function renderPlan(p) {
  const tiles = $('planTiles');
  const table = $('planTable');
  const panel = $('planPanel');
  if (!p || !p.has_plan) {
    $('planSkew').hidden = true;
    tiles.innerHTML = '<div class="plan-empty">План на выбранный период не загружен. Заполни колонку «План количество» в шаблоне и загрузи файл.</div>';
    $('planDates').textContent = 'план не загружен';
    panel.hidden = true;
    return;
  }
  const short = Number(p.shortfall || 0);
  const missing = p.positions - p.positions_done;
  // План разрезается только по дате, оборудованию и продукту. При фильтре по
  // сотруднику, смене или часу факт сужается, а план нет — это уже не «выполнение».
  const skew = p.unsupported_filters || [];
  const doneLabel = skew.length ? 'Доля от полного плана' : 'Выполнение';
  tiles.innerHTML = [
    ['План', num(p.total_plan), 'шт', `позиций: ${num(p.positions)}`, ''],
    ['Факт', num(p.total_fact), 'шт', p.diff >= 0 ? `+${num(p.diff)} шт к плану` : `${num(p.diff)} шт`, ''],
    [doneLabel, p.done === null ? '—' : dec(p.done), p.done === null ? '' : '%',
     skew.length ? 'план по этому фильтру не разрезается'
                 : `закрыто позиций: ${num(p.positions_done)} из ${num(p.positions)}`,
     skew.length ? ' warn' : planClass(p.done)],
    ['Недобор по позициям', short ? num(short) : 'нет', short ? 'шт' : '',
     short ? `не закрыто позиций: ${num(missing)}` : 'все позиции закрыты',
     short ? (missing > p.positions / 4 ? ' bad' : ' warn') : ' ok'],
  ].map(([label, value, unit, note, cls]) =>
    `<div class="plan-tile${cls}"><div class="k-label">${esc(label)}</div>` +
    `<div class="k-value"><span class="k-num">${esc(value)}</span>` +
    (unit ? `<span class="k-unit">${esc(unit)}</span>` : '') + '</div>' +
    `<div class="k-note">${esc(note)}</div></div>`
  ).join('');
  fitValues(tiles);
  const periodDays = (lastData && lastData.daily ? lastData.daily.length : 0) || p.plan_days.length;
  const gap = periodDays > p.plan_days.length
    ? ` ⚠ в периоде ${periodDays} дн. — факт больше плана просто из-за разницы дней`
    : '';
  $('planDates').textContent = `план на ${p.plan_days.length} дн.: ${p.plan_days.join(', ')}${gap}`;

  const warn = $('planSkew');
  if (skew.length) {
    warn.hidden = false;
    warn.textContent = `⚠️ В файле плана нет разреза по ${skew.join(', ')}. ` +
      `Факт сужен фильтром, а план взят целиком — проценты ниже реальных. ` +
      `Для честного сравнения сними ${skew.length > 1 ? 'эти фильтры' : 'этот фильтр'}.`;
  } else {
    warn.hidden = true;
  }

  panel.hidden = false;
  renderTable(table,
    [{ title: 'Продукт' }, { title: 'Оборудование' }, { title: 'План', num: true },
     { title: 'Факт', num: true }, { title: 'Разница', num: true }, { title: 'Выполнение', num: true }],
    p.rows,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, esc(r.equipment), num(r.plan), num(r.fact),
            `<span class="delta${r.diff < 0 ? ' bad' : ' ok'}">${r.diff >= 0 ? '+' : ''}${num(r.diff)}</span>`,
            r.done === null ? '—' : `<span class="delta${planClass(r.done).trim() ? planClass(r.done) : ''}">${dec(r.done)}%</span>`],
    (r) => ({ key: 'product', value: r.product }));
}

/* ─── источник: ФК / БХМ ──────────────────────────────────────────────────── */

function setUnits(units) {
  UNIT = (units && units.qty) || SOURCES[SOURCE].qty;
  RATE = (units && units.rate) || UNIT + '/ч';
  document.querySelectorAll('[data-unit="qty"]').forEach((el) => { el.textContent = UNIT; });
}

// План и история загрузок есть только у ФК: у БХМ механизма загрузки плана нет.
function planEnabled() {
  return !meta || meta.plan_enabled !== false;
}

function markSourceButtons(available) {
  document.querySelectorAll('.src-btn').forEach((b) => {
    const on = b.dataset.source === SOURCE;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
    if (available) {
      const ok = available[b.dataset.source] !== false;
      b.disabled = !ok;
      b.title = ok ? '' : 'Источник не настроен на сервере';
    }
  });
}

function applySourceUi() {
  document.title = `${SOURCES[SOURCE].title} — аналитика производства`;
  markSourceButtons(meta && meta.sources);
  applyTabsVisibility();
  // Страница одного источника не должна мелькать на другом: прячем всё, что не его.
  document.querySelectorAll('.tab-page').forEach((p) => { p.hidden = p.dataset.page !== activeTab; });
}

async function fetchMeta(src) {
  const r = await fetch('/api/meta?source=' + encodeURIComponent(src), { cache: 'no-store' });
  const m = await r.json();
  if (!r.ok || !m.ok) throw new Error(m.error || 'сервис недоступен');
  return m;
}

function applyMeta(m) {
  meta = m;
  fillSelect($('equipment'), meta.equipment || [], 'Все');
  fillSelect($('product'), meta.products || [], 'Все');
  fillSelect($('employee'), meta.employees || [], 'Все');
  // Границы дат — у каждого источника свои; пустая строка снимает ограничение.
  $('dateFrom').min = $('dateTo').min = meta.date_min || '';
  $('dateFrom').max = $('dateTo').max = meta.date_max || '';
  setUnits(meta.units);
  // Расстойка: печь (код → название) и статус партии; у ФК и БХМ этих полей нет.
  const ovens = meta.ovens || [];
  $('oven').innerHTML = '<option value="all">Все</option>' +
    ovens.map((o) => `<option value="${esc(o.code)}">${esc(o.name)}</option>`).join('');
  $('status').innerHTML = '<option value="all">Все</option>' +
    (meta.statuses || []).map((x) => `<option value="${esc(x.code)}">${esc(x.name)}</option>`).join('');
  fillSelect($('operation'), meta.operations || [], 'Все');
  // Поле фильтра видно только у источников из data-only.
  document.querySelectorAll('[data-only]').forEach((el) => { el.hidden = !el.dataset.only.split(' ').includes(SOURCE); });
  $('productLabel').textContent = isProofing() ? 'Изделие' : (isKc() ? 'Продукция' : 'Продукт');
  if (isGantt()) fillGanttTools(meta);
}

function restoreFilters() {
  const saved = filtersBySource[SOURCE];
  if (!saved) { resetFilters(); return; }
  Object.assign(FILTERS, saved);
  // Значение, которого больше нет в справочнике источника, — сбрасываем, а не шлём вслепую.
  [['equipment', 'equipment'], ['product', 'products'], ['employee', 'employees']].forEach(([key, list]) => {
    if (FILTERS[key] !== 'all' && !(meta[list] || []).includes(FILTERS[key])) FILTERS[key] = 'all';
  });
  if (FILTERS.oven !== 'all' && !(meta.ovens || []).some((o) => o.code === FILTERS.oven)) FILTERS.oven = 'all';
  if (FILTERS.operation !== 'all' && !(meta.operations || []).includes(FILTERS.operation)) FILTERS.operation = 'all';
  if (FILTERS.status !== 'all' && !(meta.statuses || []).some((x) => x.code === FILTERS.status)) FILTERS.status = 'all';
}

let switching = '';

async function switchSource(next) {
  if (!SOURCES[next] || next === SOURCE || switching) return;
  switching = next;
  document.body.classList.add('loading');
  let m;
  let proofFail = '';
  try {
    m = await fetchMeta(next);
  } catch (e) {
    if (!hasOwnPage(next)) {
      // Источник недоступен — остаёмся на прежнем, ничего не ломая.
      switching = '';
      document.body.classList.remove('loading');
      showAlert(`Источник ${SOURCES[next].title} недоступен: ${e.message}`);
      return;
    }
    // Расстойка и КЦ открываются и без API: ошибка видна только на их странице.
    proofFail = e.message;
    m = { stub: true, equipment: [], products: [], employees: [], ovens: [], statuses: [], operations: [],
          refresh_seconds: 60, sources: meta && meta.sources, plan_enabled: false,
          units: { qty: 'шт', rate: next === 'kc' ? 'шт/чел·ч' : 'шт/ч' } };
  }
  // Переключаемся только с готовыми справочниками: до этого момента и автообновление,
  // и фильтры работают со старым источником.
  filtersBySource[SOURCE] = { ...FILTERS };
  SOURCE = next;
  switching = '';
  store.set('fk_source', next);
  applyMeta(m);
  restoreFilters();
  syncControls();
  applySourceUi();
  if (proofFail) {
    showAlert('');
    document.body.classList.remove('loading');
    showSectionError(proofFail);
    return;
  }
  await loadSummary(false);
}

/* ─── вкладки ─────────────────────────────────────────────────────────────── */

const TABS = ['dashboard', 'plan', 'history', 'proofing', 'batches', 'kc', 'kcops', 'kcnorms', 'gantt', 'timing', 'gavg', 'gmap'];
let activeTab = 'dashboard';

// У ФК и БХМ — дашборд (+ план у ФК), у расстойки — «Сводка» и «Партии».
function allowedTabs() {
  if (isProofing()) return ['proofing', 'batches'];
  if (isKc()) return ['kc', 'kcops', 'kcnorms'];
  if (isGantt()) return ['gantt', 'timing', 'gavg', 'gmap'];
  return planEnabled() ? ['dashboard', 'plan', 'history'] : ['dashboard'];
}

function showTab(name, push = true) {
  const allowed = allowedTabs();
  if (!TABS.includes(name) || !allowed.includes(name)) name = allowed[0];
  activeTab = name;
  document.querySelectorAll('.tab').forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.tab-page').forEach((p) => { p.hidden = p.dataset.page !== name; });
  // Фильтры не относятся к журналу загрузок — там они только путают. У Ганта — своя панель.
  $('filterPanel').hidden = name === 'history' || isGantt();
  $('chips').hidden = name === 'history' || isGantt() || !activeFilters().length;
  $('gTools').hidden = !(isGantt() && (name === 'gantt' || name === 'timing' || name === 'gavg'));
  // У среднего по продукту — период, у диаграммы и тайминга — сутки и окно.
  document.querySelectorAll('[data-gonly]').forEach((f) => { f.hidden = (f.dataset.gonly === 'avg') !== (name === 'gavg'); });
  if (name === 'gmap') loadGanttMap();
  if (push && location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name);
  store.set('fk_tab', name);
  if (name === 'history') loadHistory();
  if (name !== 'history' && lastData) { mountExportButtons(); fitValues(document); }
}

function applyTabsVisibility() {
  const allowed = allowedTabs();
  document.querySelectorAll('.tab').forEach((t) => { t.hidden = !allowed.includes(t.dataset.tab); });
  if (!allowed.includes(activeTab)) showTab(allowed[0]);
}

function initTabs() {
  document.querySelectorAll('.tab').forEach((t) =>
    t.addEventListener('click', () => showTab(t.dataset.tab)));
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1), false));
  showTab(location.hash.slice(1) || store.get('fk_tab') || 'dashboard');
}

/* ─── история загрузок ────────────────────────────────────────────────────── */

async function loadHistory() {
  const table = $('historyTable');
  try {
    const r = await fetch('/api/plan/history', { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'сервис недоступен');
    const rows = d.entries || [];
    $('historyNote').textContent = rows.length ? `записей: ${rows.length}` : 'пока пусто';
    renderTable(table,
      [{ title: 'Когда' }, { title: 'Действие' }, { title: 'Файл' }, { title: 'Даты плана' },
       { title: 'Позиций', num: true }, { title: 'План, шт', num: true }, { title: 'Результат' }],
      rows,
      (r2) => [
        esc(formatMoment(r2.at)),
        r2.action === 'delete' ? 'удаление' : 'загрузка',
        esc(r2.file || '—'),
        esc((r2.dates || []).join(', ') || '—'),
        num(r2.positions),
        r2.quantity ? num(r2.quantity) : '—',
        r2.ok
          ? `<span class="delta ok">успешно${(r2.replaced || []).length ? ' · перезаписано: ' + esc(r2.replaced.join(', ')) : ''}</span>`
          : `<span class="delta bad">${esc(r2.error || 'ошибка')}</span>`,
      ]);
  } catch (e) {
    $('historyNote').textContent = '';
    table.innerHTML = `<tbody><tr><td class="empty">Не удалось загрузить историю: ${esc(e.message)}</td></tr></tbody>`;
  }
}

/* ─── загрузка файла ──────────────────────────────────────────────────────── */

function initDropzone() {
  const zone = $('dropzone');
  const input = $('planFile');
  const open = () => input.click();
  zone.addEventListener('click', open);
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
  input.addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) uploadPlan(file);
    e.target.value = '';
  });

  ['dragenter', 'dragover'].forEach((type) =>
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'dragend'].forEach((type) =>
    zone.addEventListener(type, () => zone.classList.remove('over')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('over');
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadPlan(file);
  });
  // Файл, брошенный мимо зоны, не должен открываться браузером поверх дашборда.
  ['dragover', 'drop'].forEach((type) =>
    window.addEventListener(type, (e) => { if (!zone.contains(e.target)) e.preventDefault(); }));
}

function showUploadResult(kind, text) {
  const el = $('uploadResult');
  el.hidden = false;
  el.className = 'upload-result ' + kind;
  el.textContent = text;
}

async function refreshPlanState() {
  try {
    const r = await fetch('/api/plan', { cache: 'no-store' });
    const d = await r.json();
    const note = $('planNote');
    const zone = $('dropzone');
    if (!d.upload_enabled) {
      zone.classList.add('disabled');
      note.textContent = 'загрузка выключена: на сервере не задан PLAN_UPLOAD_TOKEN';
      return;
    }
    zone.classList.remove('disabled');
    note.textContent = d.dates.length
      ? `загружено дат: ${d.dates.length} (${d.dates.slice(-3).join(', ')}${d.dates.length > 3 ? ' …' : ''}), позиций: ${d.positions}`
      : 'планов пока нет';
  } catch (e) {
    $('planNote').textContent = 'не удалось получить состояние планов';
  }
}

async function uploadPlan(file) {
  let token = store.get('fk_plan_token');
  if (!token) {
    token = (window.prompt('Ключ загрузки плана (PLAN_UPLOAD_TOKEN из .env)') || '').trim();
    if (!token) return;
  }
  if ($('dropzone').classList.contains('disabled')) return;
  showUploadResult('pending', `Загружаю ${file.name}…`);
  const form = new FormData();
  form.append('file', file, file.name);
  // В шаблоне «Дата план» — формула =TODAY(); если Excel не сохранил значение,
  // сервер возьмёт эту дату.
  form.append('date', $('planDate').value || '');
  try {
    const r = await fetch('/api/plan/upload', { method: 'POST', body: form, headers: { 'X-Plan-Token': token } });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      if (r.status === 401) store.del('fk_plan_token');
      throw new Error(d.error || 'ошибка загрузки');
    }
    store.set('fk_plan_token', token);
    const replaced = (d.replaced_dates || []).length ? `, перезаписаны даты: ${d.replaced_dates.join(', ')}` : '';
    showUploadResult('ok', `Загружено ${d.loaded_positions} позиций на ${d.loaded_dates.join(', ')}` +
      ` — ${num(d.loaded_quantity || 0)} шт${replaced}.`);
    await refreshPlanState();
    await loadSummary(false);
    if (activeTab === 'history') loadHistory();
  } catch (e) {
    showUploadResult('bad', 'Не удалось загрузить: ' + e.message);
    if (activeTab === 'history') loadHistory();
  }
}

/* ─── выгрузки ────────────────────────────────────────────────────────────── */

function download(url) {
  // Content-Disposition: attachment — браузер скачивает файл и остаётся на странице.
  const a = document.createElement('a');
  a.href = url;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function exportWidget(widget) {
  download(`/api/export/xlsx?widget=${encodeURIComponent(widget)}&` + query(false));
}

function mountExportButtons() {
  document.querySelectorAll('.panel[data-widget]').forEach((panel) => {
    const head = panel.querySelector('.panel-head');
    if (!head || head.querySelector('.export-btn')) return;
    const btn = document.createElement('button');
    btn.className = 'export-btn';
    btn.type = 'button';
    btn.title = 'Выгрузить в Excel с текущими фильтрами';
    btn.textContent = '⤓ Excel';
    btn.addEventListener('click', () => exportWidget(panel.dataset.widget));
    head.appendChild(btn);
  });
}

function renderAnalysis(blocks, el = $('analysis')) {
  if (!blocks || !blocks.length) { el.innerHTML = '<div class="empty">Нет данных за выбранный период</div>'; return; }
  el.innerHTML = blocks.map(([heading, lines]) =>
    `<section class="an-block"><h3>${esc(heading)}</h3><ul>` +
    lines.map((l) => `<li>${esc(l)}</li>`).join('') + '</ul></section>').join('');
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
  if (isKc() && FILTERS.operation !== 'all') {
    out.push({ key: 'operation', value: FILTERS.operation, text: `Операция: ${FILTERS.operation}` });
  }
  if (hasOwnPage()) {
    if (isProofing() && FILTERS.oven !== 'all') {
      const o = ((meta && meta.ovens) || []).find((x) => x.code === FILTERS.oven);
      out.push({ key: 'oven', value: FILTERS.oven, text: `Печь: ${o ? o.name : FILTERS.oven}` });
    }
    if (FILTERS.status !== 'all') {
      const st = ((meta && meta.statuses) || []).find((x) => x.code === FILTERS.status);
      out.push({ key: 'status', value: FILTERS.status, text: `Статус: ${st ? st.name : FILTERS.status}` });
    }
  }
  return out;
}

function renderChips() {
  const list = activeFilters();
  const bar = $('chips');
  bar.hidden = list.length === 0 || activeTab === 'history';
  bar.innerHTML = list.map((f) =>
    `<button class="fchip" data-clear="${esc(f.key)}">${esc(f.text)}<i>×</i></button>`).join('')
    + (list.length ? '<button class="fchip clear-all" data-clear="__all">Сбросить всё</button>' : '');
  $('filterHint').textContent = list.length ? list.map((f) => f.text).join(' · ') : 'все данные';
}

function setFilter(key, value) {
  if (key === 'gproduct') {
    // Гант: строка тайминга сужает диаграмму до продукта (повторно — снимает).
    GANTT.product = GANTT.product === value ? 'all' : value;
    $('gProduct').value = GANTT.product;
    loadSummary(false);
    return;
  }
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
  FILTERS.oven = 'all';
  FILTERS.status = 'all';
  FILTERS.operation = 'all';
}

function syncControls() {
  $('dateFrom').value = FILTERS.dateFrom;
  $('dateTo').value = FILTERS.dateTo;
  $('equipment').value = FILTERS.equipment;
  $('product').value = FILTERS.product;
  $('employee').value = FILTERS.employee;
  $('shift').value = FILTERS.shift;
  $('hour').value = FILTERS.hour;
  $('oven').value = FILTERS.oven;
  $('status').value = FILTERS.status;
  $('operation').value = FILTERS.operation;
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
  FILTERS.oven = $('oven').value || 'all';
  FILTERS.status = $('status').value || 'all';
  FILTERS.operation = $('operation').value || 'all';
  renderChips();
}

/* ─── рендер ──────────────────────────────────────────────────────────────── */

function render(data) {
  if (data.kind === 'proofing') { renderProofing(data); return; }
  if (data.kind === 'kc') { renderKc(data); return; }
  if (data.kind === 'gantt') { renderGantt(data); return; }
  lastData = data;
  const lfl = data.lfl || {};
  const lflOk = lfl.available && !lfl.prev_empty;
  renderKpis(data.kpi, lfl);
  renderLflCaption(lfl);
  renderPlan(data.plan);   // читает lastData.daily для предупреждения о разнице дней

  $('chartDaily').innerHTML = barChart(
    data.daily.map((d) => ({ label: d.date.slice(5), key: d.date, value: d.qty })),
    { color: '#3b6ef5', filterKey: 'date', unit: UNIT,
      activeValue: FILTERS.dateFrom === FILTERS.dateTo ? FILTERS.dateFrom : undefined,
      compare: lfl.available ? data.daily.map((d) => d.lfl_qty) : [],
      compareTip: lfl.available ? (i) => {
        const d = data.daily[i];
        if (!d.lfl_date) return '';
        return ` · ${d.lfl_date.slice(5)} (неделей раньше): ${num(d.lfl_qty)} ${UNIT}, ${pctText(d.lfl_change)}`;
      } : null })
    + (data.daily.length ? chartLegend([['box', '#3b6ef5', 'выпуск за день']]
      .concat(lfl.available ? [['box', GHOST_COLOR, 'тот же день неделей раньше']] : [])) : '');
  renderDailyLfl(lfl);

  $('chartHourly').innerHTML = barChart(
    data.hourly.map((h, i) => ({ label: h.label, key: String(i), value: h.value })),
    { color: '#7c5cf5', rotate: true, filterKey: 'hour', unit: RATE,
      target: data.hourly_target, activeValue: FILTERS.hour,
      compareTip: lflOk ? (i) => {
        const h = data.hourly[i];
        return ` · прошлый период: ${dec(h.lfl_value)} ${RATE}, ${pctText(h.lfl_change)}`;
      } : null })
    + (data.hourly_target ? chartLegend([['box', '#7c5cf5', 'выше средней'], ['box', BELOW_COLOR, 'ниже средней'],
                                         ['dash', '#101828', 'средняя']]) : '');
  renderHourlyLfl(lfl, data);
  $('hourlyNote').textContent = `${RATE} · нажми на столбец`;

  const cap = isNarrow() ? 8 : 12;
  $('chartEquipment').innerHTML = barChart(data.by_equipment.slice(0, cap),
    { color: '#3fbf94', rotate: true, filterKey: 'equipment', unit: UNIT, activeValue: FILTERS.equipment });
  $('chartProducts').innerHTML = barChart(data.top_products.slice(0, cap),
    { color: '#f0a63c', rotate: true, filterKey: 'product', unit: UNIT, activeValue: FILTERS.product });
  $('chartEmployees').innerHTML = barChart((data.top_employees || []).slice(0, cap),
    { color: '#2a78d6', rotate: true, filterKey: 'employee', unit: UNIT, activeValue: FILTERS.employee });

  $('chartReasons').innerHTML = pieChart(data.pause_reasons);
  $('chartWork').innerHTML = pieChart(data.work_vs_stop, { donut: true, colors: ['#2a78d6', '#e34948'] });

  // Сводка по дням: крупный факт, полоска доли от лучшего дня, план отдельной строкой.
  // Процент считается из того же суженного факта и полного плана, что и на вкладке
  // «План / факт», поэтому при неподдерживаемом фильтре он тут тоже не «выполнение».
  const daySkew = ((data.plan || {}).unsupported_filters || []).length > 0;
  const best = Math.max(1, ...data.daily.map((d) => d.qty));
  $('dailyCards').innerHTML = data.daily.length
    ? data.daily.map((d) => {
        const active = FILTERS.dateFrom === d.date && FILTERS.dateTo === d.date;
        const planRow = d.plan
          ? `<div class="dc-plan${daySkew ? '' : planClass(d.done)}" ${daySkew ? 'title="План не разрезается по выбранному фильтру — это доля от полного плана"' : ''}><span>план ${num(d.plan)}</span><b>${daySkew ? 'доля ' : ''}${dec(d.done)}%</b></div>`
          : '';
        return `<button class="daycard${active ? ' active' : ''}" data-filter="date" data-value="${esc(d.date)}" role="button" tabindex="0">
          <div class="dc-top"><span class="dc-date">${esc(d.date.slice(5))}</span><span class="dc-wd">${esc(d.weekday || '')}</span></div>
          <div class="dc-qty">${num(d.qty)}<small>${esc(UNIT)}</small></div>
          <div class="dc-meter"><i style="width:${(d.qty / best * 100).toFixed(1)}%"></i></div>
          ${planRow}
          <div class="dc-foot"><span>${num(d.ops)} оп.</span><span>${num(d.avg_duration)} мин</span><span class="${d.stop ? 'dc-stop' : ''}">${d.stop ? hm(d.stop) : 'без простоев'}</span></div>
        </button>`;
      }).join('')
    : '<div class="empty">Нет данных за выбранный период</div>';

  renderTable($('employeeTable'),
    [{ title: 'Сотрудник' }, { title: 'Операций', num: true }, { title: `Выпуск (${UNIT})`, num: true },
     { title: 'Доля', num: true }, { title: 'Ср. длит. (мин)', num: true }, { title: `Ср. произв. (${RATE})`, num: true },
     { title: 'Простои', num: true }, { title: 'Загрузка', num: true }, { title: 'Дней', num: true }],
    data.employee_timings || [],
    (r) => [`<span class="tag">${esc(r.employee)}</span>`, num(r.ops), num(r.qty), dec(r.share) + '%',
            dec(r.avg_duration), dec(r.avg_rate), `${num(r.pauses)} · ${hm(r.stop)}`, dec(r.utilization) + '%', num(r.days)],
    (r) => ({ key: 'employee', value: r.employee }));

  renderTable($('equipmentTable'),
    [{ title: 'Оборудование' }, { title: 'Операций', num: true }, { title: `Выпуск (${UNIT})`, num: true },
     { title: 'Работа', num: true }, { title: 'Простои', num: true },
     { title: `Ср. произв. (${RATE})`, num: true }, { title: 'Загрузка', num: true }],
    data.equipment_timings,
    (r) => [`<span class="tag">${esc(r.equipment)}</span>`, num(r.ops), num(r.qty), hm(r.work), hm(r.stop), dec(r.avg_rate), dec(r.utilization) + '%'],
    (r) => ({ key: 'equipment', value: r.equipment }));

  renderTable($('productTable'),
    [{ title: 'Продукт' }, { title: 'Операций', num: true }, { title: `Выпуск (${UNIT})`, num: true },
     { title: 'Ср. длит. (мин)', num: true }, { title: `Ср. произв. (${RATE})`, num: true }, { title: 'Диапазон старта' }],
    data.product_timings,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, num(r.ops), num(r.qty), dec(r.avg_duration), dec(r.avg_rate), esc(r.start_range)],
    (r) => ({ key: 'product', value: r.product }));

  // У БХМ строка детализации — шаг партии (сборка, замес, отлежка…): показываем, какой.
  const withOp = data.detail.some((r) => r.operation);
  renderTable($('detailTable'),
    [{ title: 'Дата' }].concat(withOp ? [{ title: 'Операция' }] : []).concat(
     [{ title: 'Оборудование' }, { title: 'Продукт' }, { title: 'Старт' },
     { title: 'Финиш' }, { title: 'Кол-во', num: true }, { title: 'Статус' }, { title: 'Оператор' }]),
    data.detail,
    (r) => [esc(r.date || '—')].concat(withOp ? [esc(r.operation || '—')] : []).concat(
           [esc(r.equipment), `<span class="tag">${esc(r.product)}</span>`,
            esc(r.start_time || '—'), esc(r.end_time || '—'), num(r.qty),
            r.status === 'closed' ? 'завершена' : esc(r.status), esc(r.user_name)]));

  renderAnalysis(data.analysis);
  mountExportButtons();

  const src = data.source || {};
  const fetched = formatMoment(src.fetched_at);
  const modes = { db: 'PostgreSQL (только чтение)', api: 'API бота', demo: 'тестовые данные (demo)',
                  'bhm-api': 'API бота тестозаготовочного цеха (БХМ)', 'bhm-demo': 'тестовые данные тестозаготовочного цеха (demo)' };
  const skipped = (data.excluded_dates || []).length ? ` · скрыто дат: ${data.excluded_dates.join(', ')}` : '';
  $('sourceLine').textContent = `Источник: ${modes[src.mode] || src.mode || '—'} · снимок от ${fetched} · автообновление ${meta ? meta.refresh_seconds : 60} с${skipped}`;
  $('periodLine').textContent = `Период ${FILTERS.dateFrom || 'начало'} – ${FILTERS.dateTo || 'сегодня'} · ${SHIFT_NAMES[FILTERS.shift]} · операций: ${num(data.kpi.operations)} · выпуск: ${num(data.kpi.quantity)} ${UNIT} · обновлено ${fetched}`;
}

function showAlert(message) {
  const el = $('alert');
  if (!message) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = '⚠️ ' + message;
}

function query(force) {
  if (isGantt()) return ganttQuery(force);
  const p = new URLSearchParams({
    source: SOURCE,
    date_from: FILTERS.dateFrom, date_to: FILTERS.dateTo,
    equipment: FILTERS.equipment, product: FILTERS.product,
    employee: FILTERS.employee, shift: FILTERS.shift,
  });
  if (FILTERS.hour !== '') p.set('hour', FILTERS.hour);
  if (isProofing()) { p.set('oven', FILTERS.oven); p.set('status', FILTERS.status); }
  if (isKc()) { p.set('operation', FILTERS.operation); p.set('status', FILTERS.status); }
  if (force) p.set('refresh', '1');
  return p.toString();
}

async function loadSummary(force) {
  const seq = ++loadSeq;
  document.body.classList.add('loading');
  try {
    const r = await fetch('/api/summary?' + query(force), { cache: 'no-store' });
    const data = await r.json();
    if (seq !== loadSeq) return;
    if (!r.ok || !data.ok) throw new Error(data.error || 'сервис недоступен');
    showAlert(data.source && data.source.error ? 'Данные показаны из кеша: ' + data.source.error : '');
    if (data.kind === 'proofing') showProofError('');
    if (data.kind === 'kc') showKcError('');
    if (data.kind === 'gantt') showGanttError('');
    render(data);
  } catch (e) {
    if (seq !== loadSeq) return;
    // Ошибка расстойки и КЦ показывается только на их странице.
    if (hasOwnPage()) { showAlert(''); showSectionError(e.message); }
    else showAlert('Не удалось загрузить данные: ' + e.message);
  } finally {
    if (seq === loadSeq) document.body.classList.remove('loading');
  }
}

/* ─── Расстойка и отпекание ───────────────────────────────────────────────── */

// Своя страница: у партии два этапа, выпуск — один раз по концу отпекания.
// Графики, таблицы и KPI — те же компоненты, что у ФК и БХМ, чтобы раздел выглядел частью продукта.
let proofData = null;
const NO_DATA = 'Нет данных';
const UNIT_SHORT = { racks: 'шп.', pods: 'под.' };
const UNIT_FORMS = { racks: ['шпилька', 'шпильки', 'шпилек'], pods: ['под', 'пода', 'подов'] };

function plural(n, forms) {
  const a = Math.abs(Math.round(n)) % 100;
  const b = a % 10;
  if (b === 1 && a !== 11) return forms[0];
  if (b >= 2 && b <= 4 && (a < 12 || a > 14)) return forms[1];
  return forms[2];
}

function dur(minutes) {
  return minutes === null || minutes === undefined ? '—' : hm(minutes);
}

function showProofError(message) {
  const box = $('proofError');
  if (!message) { box.hidden = true; $('proofBody').hidden = false; return; }
  box.hidden = false;
  $('proofErrorText').textContent = message;
  // Строки шапки и подвала не должны остаться от ФК/БХМ.
  $('periodLine').textContent = proofData
    ? 'Печной цех: данные не обновились — показаны последние полученные'
    : 'Печной цех: данные недоступны';
  $('sourceLine').textContent = 'Источник: API бота печного цеха (расстойка) · ошибка подключения';
  // Старые цифры при ошибке не прячем, если они уже были: подписываем, что данные не обновились.
  $('proofBody').hidden = !proofData;
  if (proofData) $('proofErrorText').textContent = message + ' Ниже — последние полученные данные.';
}

async function refreshProofMeta() {
  try {
    const m = await fetchMeta('proofing');
    if (!isProofing()) return;
    applyMeta(m);
    restoreFilters();
    syncControls();
    markSourceButtons(meta.sources);
  } catch (e) { /* остаёмся с тем, что есть */ }
}

function stageText(st, unitFallback) {
  if (!st) return '<span class="muted">—</span>';
  const unit = UNIT_SHORT[st.rack_unit || unitFallback] || 'шп.';
  const load = st.racks_start === null || st.racks_start === undefined ? ''
    : ` · ${num(st.racks_start)}${st.racks_end !== null && st.racks_end !== undefined && st.racks_end !== st.racks_start ? '→' + num(st.racks_end) : ''} ${unit}`;
  const end = st.end_time ? st.end_time : (st.status === 'cancelled' ? 'отменён' : '…');
  const minutes = st.duration_min === null || st.duration_min === undefined ? ''
    : ` · ${st.valid ? hm(st.duration_min) : '<span class="delta bad" title="Некорректное время — не входит в средние">' + esc(hm(st.duration_min)) + '</span>'}`;
  const who = st.closed_by && st.closed_by !== st.started_by ? `${st.started_by} → ${st.closed_by}` : st.started_by;
  return `<span class="cellbox"><span class="stage">${esc(st.start_time)}–${esc(end)}${minutes}${esc(load)}</span>` +
    `<small class="stage-who">${esc(who || '')}</small></span>`;
}

function proofKpis(k, lfl) {
  const ok = lfl && lfl.available && !lfl.prev_empty;
  const none = (v) => v === null || v === undefined;
  // [подпись, значение, единица, пояснение, ключ LFL, «лучше» (1 — рост хорошо, -1 — плохо, 0 — без оценки), формат прошлого]
  const cards = [
    ['Начато партий', num(k.started), '', k.cancelled ? `из них отменено: ${num(k.cancelled)}` : 'по началу расстойки', 'started', 1, num],
    ['Завершено партий', num(k.completed), '', 'по концу отпекания', 'completed', 1, num],
    ['В работе', num(k.in_work), '', 'на конец периода', null, 0, num],
    ['Выпуск', k.completed ? num(k.output) : NO_DATA, k.completed ? 'шт' : '',
     k.completed_without_output ? `без выхода: ${num(k.completed_without_output)} парт.` : 'только закрытое отпекание', 'output', 1, (v) => num(v) + ' шт'],
    ['Ср. расстойка', none(k.avg_proofing) ? NO_DATA : num(Math.round(k.avg_proofing)), none(k.avg_proofing) ? '' : 'мин',
     `этапов: ${num(k.proofing_count)}`, 'avg_proofing', 0, (v) => none(v) ? NO_DATA : dec(v) + ' мин'],
    ['Ср. отпекание', none(k.avg_baking) ? NO_DATA : num(Math.round(k.avg_baking)), none(k.avg_baking) ? '' : 'мин',
     `этапов: ${num(k.baking_count)}`, 'avg_baking', 0, (v) => none(v) ? NO_DATA : dec(v) + ' мин'],
    ['Производительность', none(k.rate) ? NO_DATA : num(Math.round(k.rate)), none(k.rate) ? '' : 'шт/ч',
     'выход / часы отпекания', 'rate', 1, (v) => none(v) ? NO_DATA : num(Math.round(v)) + ' шт/ч'],
    ['Крит. остановки', num(k.pauses), '', k.pauses ? `суммарно ${hm(k.stop_minutes)}` : 'не было', 'pauses', -1, num],
  ];
  $('pKpis').innerHTML = cards.map(([label, value, unit, note, key, better, fmt]) => {
    let delta = '';
    if (ok && key && lfl.kpi[key]) {
      const c = lfl.kpi[key];
      let tip = `${lfl.prev_label}: ${fmt(c.prev)}`;
      if (key === 'pauses' && lfl.kpi.stop_minutes) {
        tip += ` · минут остановок: ${num(k.stop_minutes)} против ${num(lfl.kpi.stop_minutes.prev)} (${pctText(lfl.kpi.stop_minutes.change)})`;
      }
      if (c.change === null) tip += ' · сравнить не с чем';
      delta = `<div class="k-delta" data-tip="${esc(tip)}">${trend(c.change, better)}<span class="k-vs">${esc(lflVs(lfl))}</span></div>`;
    }
    return `<div class="kpi"><div class="k-label">${esc(label)}</div>` +
      `<div class="k-value${value === NO_DATA ? ' k-none' : ''}"><span class="k-num">${esc(value)}</span>` +
      (unit ? `<span class="k-unit">${esc(unit)}</span>` : '') + '</div>' +
      `<div class="k-note">${esc(note)}</div>${delta}</div>`;
  }).join('');
  fitValues($('pKpis'));
}

function proofLflCaption(lfl) {
  const el = $('pLflCaption');
  el.hidden = false;
  if (!lfl || !lfl.available) { el.textContent = 'Сравнение с прошлым периодом: ' + ((lfl && lfl.reason) || 'недоступно'); return; }
  const cut = lfl.partial ? `, последний день до ${lfl.cutoff} — как сегодня` : '';
  el.textContent = lfl.prev_empty
    ? `За прошлый период ${lfl.prev_label} с этими фильтрами данных нет — сравнивать не с чем.`
    : `▲▼ — изменение к прошлому периоду ${lfl.prev_label} (${lfl.days} дн.${cut}) с теми же фильтрами`;
}

function ovenCards(ovens) {
  const top = Math.max(1, ...ovens.map((o) => o.rate || 0));
  return ovens.map((o) => {
    const active = FILTERS.oven === o.code;
    const rate = o.rate === null || o.rate === undefined ? NO_DATA : num(Math.round(o.rate));
    return `<button class="ovencard${active ? ' active' : ''}" type="button" data-filter="oven" data-value="${esc(o.code)}">
      <div class="oc-top"><span class="oc-name">${esc(o.name)}</span><span class="oc-unit">${esc(o.unit_name)}</span></div>
      <div class="oc-rate">${esc(rate)}${o.rate === null || o.rate === undefined ? '' : '<small>шт/ч</small>'}</div>
      <div class="dc-meter"><i style="width:${((o.rate || 0) / top * 100).toFixed(1)}%"></i></div>
      <dl class="oc-facts">
        <div><dt>Выпуск</dt><dd>${o.batches ? num(o.output) + ' шт' : NO_DATA}</dd></div>
        <div><dt>Партий</dt><dd>${num(o.batches)}</dd></div>
        <div><dt>Загружено</dt><dd>${esc(o.loaded_text)}</dd></div>
        <div><dt>Ср. отпекание</dt><dd>${esc(dur(o.avg_baking))}</dd></div>
        <div><dt>Занято</dt><dd>${o.utilization === null || o.utilization === undefined ? '—' : dec(o.utilization) + '%'} · до ${num(o.max_parallel)} парт.</dd></div>
        <div><dt>Остановки</dt><dd class="${o.pauses ? 'dc-stop' : ''}">${o.pauses ? num(o.pauses) + ' · ' + hm(o.stop) : 'нет'}</dd></div>
      </dl>
    </button>`;
  }).join('') || '<div class="empty">Нет данных</div>';
}

function loadTone(pct) {
  if (!(pct > 0)) return 'background:#f5f6f8';
  const a = Math.min(1, 0.12 + pct / 100 * 0.88);
  return `background:rgba(59,110,245,${a.toFixed(2)});color:${a > 0.55 ? '#fff' : '#1d2b52'}`;
}

function loadHeat(load) {
  const rows = load.rows || [];
  if (!rows.some((r) => r.stages)) return '<div class="empty">Нет данных за выбранный период</div>';
  const hour = FILTERS.hour === '' ? null : Number(FILTERS.hour);
  const inShift = (h) => FILTERS.shift === 'all' || (FILTERS.shift === 'day' ? h >= 8 && h < 20 : h < 8 || h >= 20);
  const head = '<div class="lh-name"></div>' + Array.from({ length: 24 }, (_, h) =>
    `<div class="lh-hour${hour === h ? ' on' : ''}">${String(h).padStart(2, '0')}</div>`).join('');
  const body = rows.map((r) => `<div class="lh-name" title="${esc(r.name)}">${esc(r.name)}</div>` + r.hours.map((x) => {
    const dim = (hour !== null && hour !== x.hour) || !inShift(x.hour);
    const tip = `${r.name}, ${String(x.hour).padStart(2, '0')}:00 — занято в среднем ${dec(x.busy_min)} мин из 60 (${dec(x.pct)}%) · партий одновременно в среднем ${dec(x.parallel)}`;
    return `<div class="lh-cell${dim ? ' dim' : ''}" style="${loadTone(x.pct)}" data-tip="${esc(tip)}">${x.pct >= 1 ? Math.round(x.pct) : ''}</div>`;
  }).join('')).join('');
  return `<div class="loadheat">${head}${body}</div>` +
    '<div class="heat-key"><span><i class="lk" style="background:#f5f6f8"></i>свободно</span>' +
    `<span><i class="lk" style="${loadTone(25)}"></i>25%</span><span><i class="lk" style="${loadTone(60)}"></i>60%</span>` +
    `<span><i class="lk" style="${loadTone(100)}"></i>занято весь час</span>` +
    '<span class="hk-note">пересечения партий не суммируются</span></div>';
}

function batchRows(list) {
  const q = ($('pBatchSearch').value || '').trim().toLowerCase();
  if (!q) return list;
  return list.filter((b) => (b.product + ' ' + b.employees.join(' ') + ' ' + (b.oven_name || '')).toLowerCase().includes(q));
}

function renderBatchTable() {
  const data = proofData;
  if (!data) return;
  const rows = batchRows(data.batches || []);
  const shown = data.batches.length;
  $('pBatchNote').textContent = data.batches_total > shown
    ? `показаны последние ${num(shown)} из ${num(data.batches_total)} — сузь период`
    : `партий: ${num(rows.length)}${rows.length !== shown ? ' из ' + num(shown) : ''}`;
  renderTable($('pBatchTable'),
    [{ title: 'Старт' }, { title: 'Изделие' }, { title: 'Статус' }, { title: 'Печь' },
     { title: 'Расстойка' }, { title: 'Отпекание' }, { title: 'Вход → выход, шт', num: true },
     { title: 'шт/ч', num: true }, { title: 'Цикл', num: true }, { title: 'Фото' }],
    rows,
    (b) => [
      `<span class="cellbox">${esc(b.date.slice(5))} <b>${esc(b.start_time)}</b></span>`,
      `<span class="tag" title="${esc(b.product)}">${esc(b.product)}</span>`,
      `<span class="cellbox"><span class="badge ${esc(b.status)}">${esc(b.status_name)}</span>` +
        (b.next_mark ? `<small class="stage-who">дальше: ${esc(b.next_mark)}${b.open_for_min !== null && b.open_for_min !== undefined ? ' · ' + esc(hm(b.open_for_min)) : ''}</small>` : '') + '</span>',
      esc(b.oven_name || '—'),
      stageText(b.proofing, 'racks'),
      stageText(b.baking, b.oven_unit || 'racks'),
      `${b.pieces_in === null || b.pieces_in === undefined ? '—' : num(b.pieces_in)} → ${b.pieces_out === null || b.pieces_out === undefined ? '—' : '<b>' + num(b.pieces_out) + '</b>'}`,
      b.rate === null || b.rate === undefined ? '—' : num(Math.round(b.rate)),
      b.cycle_min === null || b.cycle_min === undefined ? '—' : hm(b.cycle_min),
      b.photo_count ? `<button class="photo-btn" type="button" data-gallery="${esc(b.id)}">📷 ${num(b.photo_count)}</button>` : '<span class="muted">нет</span>',
    ]);
}

function renderProofing(data) {
  proofData = data;
  lastData = data;
  if (meta && meta.stub) refreshProofMeta();
  const k = data.kpi;
  const lfl = data.lfl || {};
  proofKpis(k, lfl);
  proofLflCaption(lfl);

  $('pChartDaily').innerHTML = barChart(
    data.daily.map((d) => ({ label: d.date.slice(5), key: d.date, value: d.output })),
    { color: '#3b6ef5', filterKey: 'date', unit: 'шт',
      activeValue: FILTERS.dateFrom === FILTERS.dateTo ? FILTERS.dateFrom : undefined,
      compare: lfl.available ? data.daily.map((d) => d.lfl_output) : [],
      compareTip: (i) => {
        const d = data.daily[i];
        return ` · партий ${num(d.completed)}` + (d.lfl_date ? ` · ${d.lfl_date.slice(5)} (неделей раньше): ${num(d.lfl_output)} шт, ${pctText(d.lfl_change)}` : '');
      } })
    + (data.daily.length ? chartLegend([['box', '#3b6ef5', 'выпуск за день']]
      .concat(lfl.available ? [['box', GHOST_COLOR, 'тот же день неделей раньше']] : [])) : '');

  $('pChartHourly').innerHTML = barChart(
    data.hourly.map((h) => ({ label: h.label, key: String(h.hour), value: h.value })),
    { color: '#7c5cf5', rotate: true, filterKey: 'hour', unit: 'шт', activeValue: FILTERS.hour,
      compareTip: (i) => ` · партий ${num(data.hourly[i].batches)} · в среднем ${dec(data.hourly[i].per_day)} шт за день` });

  $('pOvens').innerHTML = ovenCards(data.ovens || []);

  const load = data.load || {};
  $('pLoadNote').textContent = `% времени часа · в среднем за ${num(load.days || 0)} дн.`;
  $('pLoad').innerHTML = loadHeat(load);
  renderTable($('pLoadTable'),
    [{ title: 'Оборудование' }, { title: 'Занято (без пересечений)', num: true }, { title: 'Сумма по партиям', num: true },
     { title: 'Занято, % периода', num: true }, { title: 'Одновременно до', num: true }, { title: 'Этапов', num: true }],
    (load.rows || []).filter((r) => r.stages),
    (r) => [`<span class="tag">${esc(r.name)}</span>`, dec(r.busy_hours) + ' ч', dec(r.sum_hours) + ' ч',
            r.utilization === null || r.utilization === undefined ? '—' : dec(r.utilization) + '%',
            num(r.max_parallel) + ' парт.', num(r.stages)]);

  const cap = isNarrow() ? 8 : 12;
  $('pChartProducts').innerHTML = barChart((data.top_products || []).slice(0, cap),
    { color: '#f0a63c', rotate: true, filterKey: 'product', unit: 'шт', activeValue: FILTERS.product });
  $('pChartEmployees').innerHTML = barChart((data.top_employees || []).slice(0, cap),
    { color: '#2a78d6', rotate: true, filterKey: 'employee', unit: 'партий', activeValue: FILTERS.employee });

  renderTable($('pProductTable'),
    [{ title: 'Изделие' }, { title: 'Начато', num: true }, { title: 'Завершено', num: true }, { title: 'Выпуск, шт', num: true },
     { title: 'Ср. расстойка', num: true }, { title: 'Ср. отпекание', num: true }, { title: 'Ср. цикл', num: true },
     { title: 'шт/ч', num: true }],
    data.products || [],
    (r) => [`<span class="tag">${esc(r.product)}</span>`, num(r.started), num(r.completed), r.completed ? num(r.output) : '—',
            esc(dur(r.avg_proofing)), esc(dur(r.avg_baking)), esc(dur(r.avg_cycle)),
            r.rate === null || r.rate === undefined ? '—' : num(Math.round(r.rate))],
    (r) => ({ key: 'product', value: r.product }));

  $('pChartReasons').innerHTML = pieChart(data.pause_reasons || []);
  renderTable($('pPauseEquipment'),
    [{ title: 'Оборудование' }, { title: 'Остановок', num: true }, { title: 'Длительность', num: true }, { title: 'Причины' }],
    data.pause_equipment || [],
    (r) => [`<span class="tag">${esc(r.equipment)}</span>`, num(r.count), esc(hm(r.minutes)), esc(r.reasons)],
    (r) => ({ key: 'equipment', value: r.equipment }));

  renderAnalysis(data.analysis, $('pAnalysis'));

  renderTable($('pDailyTable'),
    [{ title: 'Дата' }, { title: 'Начато', num: true }, { title: 'Завершено', num: true }, { title: 'Выпуск, шт', num: true },
     { title: 'Ср. расстойка', num: true }, { title: 'Ср. отпекание', num: true }, { title: 'шт/ч', num: true },
     { title: 'Остановки', num: true }, { title: 'Неделей раньше', num: true }],
    data.daily || [],
    (d) => [`<span class="cellbox">${esc(d.date)} <span class="muted">${esc(d.weekday)}</span></span>`, num(d.started), num(d.completed),
            d.completed ? num(d.output) : '—', esc(dur(d.avg_proofing)), esc(dur(d.avg_baking)),
            d.rate === null || d.rate === undefined ? '—' : num(Math.round(d.rate)),
            d.pauses ? `${num(d.pauses)} · ${hm(d.stop)}` : '—',
            d.lfl_date ? `<span class="cellbox">${num(d.lfl_output)} ${trend(d.lfl_change, 1)}</span>` : '—'],
    (d) => ({ key: 'date', value: d.date }));

  renderTable($('pEmployeeTable'),
    [{ title: 'Сотрудник' }, { title: 'Партий', num: true }, { title: 'Отметок', num: true },
     { title: 'Начал расстойку', num: true }, { title: 'Закрыл расстойку', num: true },
     { title: 'Начал отпекание', num: true }, { title: 'Закрыл отпекание', num: true },
     { title: 'Выход закрытых им отпеканий, шт', num: true }, { title: 'Остановки', num: true }],
    data.employees || [],
    (e) => [`<span class="tag">${esc(e.employee)}</span>`, num(e.batches), num(e.marks), num(e.start_proofing),
            num(e.end_proofing), num(e.start_baking), num(e.end_baking), num(e.output_closed),
            e.pauses ? `${num(e.pauses)} · ${hm(e.stop)}` : '—'],
    (e) => ({ key: 'employee', value: e.employee }));

  const opened = data.open_stages || [];
  $('pOpenNote').textContent = opened.length ? `сейчас открыто: ${num(opened.length)}` : 'все этапы закрыты';
  renderTable($('pOpenTable'),
    [{ title: 'Изделие' }, { title: 'Этап' }, { title: 'Оборудование' }, { title: 'Начало' },
     { title: 'Открыт', num: true }, { title: 'Начал' }, { title: 'Следующая отметка' }],
    opened,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, esc(r.stage), esc(r.equipment),
            `${esc(r.start_date.slice(5))} ${esc(r.start_time)}`,
            `<span class="${r.stale ? 'delta bad' : ''}" ${r.stale ? 'title="Дольше лимита этапа — вероятно, забыли закрыть"' : ''}>${esc(hm(r.open_min))}${r.stale ? ' ⚠' : ''}</span>`,
            esc(r.started_by), esc(r.next_mark)]);

  renderTable($('pPauseTable'),
    [{ title: 'Дата' }, { title: 'Оборудование' }, { title: 'Причина' }, { title: 'Время' },
     { title: 'Длительность', num: true }, { title: 'Сотрудник' }],
    data.pause_rows || [],
    (r) => [esc(r.date), esc(r.equipment), esc(r.reason), `${esc(r.start_time)}–${esc(r.end_time)}`, esc(hm(r.minutes)), esc(r.user_name)]);

  renderBatchTable();
  mountExportButtons();

  const src = data.source || {};
  const fetched = formatMoment(src.fetched_at);
  const modes = { 'proofing-api': 'API бота расстойки', 'proofing-demo': 'тестовые данные расстойки (demo)' };
  const skipped = (data.excluded_dates || []).length ? ` · скрыто дат: ${data.excluded_dates.join(', ')}` : '';
  $('sourceLine').textContent = `Источник: ${modes[src.mode] || src.mode || '—'} · снимок от ${fetched} · автообновление ${meta ? meta.refresh_seconds : 60} с${skipped}`;
  $('periodLine').textContent = `Период ${FILTERS.dateFrom || 'начало'} – ${FILTERS.dateTo || 'сегодня'} · ${SHIFT_NAMES[FILTERS.shift]} · ` +
    `партий завершено: ${num(k.completed)} · выпуск: ${k.completed ? num(k.output) + ' шт' : 'нет данных'} · обновлено ${fetched}`;
}

/* ─── галерея фото партии: грузится только по нажатию ─────────────────────── */

let gallery = { photos: [], index: -1, opener: null };

function photoUrl(id) {
  // Только адрес дашборда: ключ API бота остаётся на сервере.
  return '/api/proofing/photo/' + encodeURIComponent(id);
}

function openGallery(batchId, opener) {
  const b = ((proofData && proofData.batches) || []).find((x) => x.id === batchId);
  if (!b || !b.photos.length) return;
  gallery = { photos: b.photos, index: -1, opener };
  $('galleryTitle').textContent = `${b.product} · ${b.date.slice(5)} ${b.start_time} · фото: ${b.photos.length}`;
  $('galleryGrid').innerHTML = b.photos.map((p, i) =>
    `<button class="g-thumb" type="button" data-photo="${i}">
      <img loading="lazy" src="${esc(photoUrl(p.id))}" alt="${esc(p.mark)}">
      <span>${esc(p.mark)}<small>${esc(p.user_name)} · ${esc(formatMoment(p.created_at))}</small></span>
    </button>`).join('');
  $('galleryGrid').querySelectorAll('img').forEach((img) =>
    img.addEventListener('error', () => { img.replaceWith(Object.assign(document.createElement('div'), { className: 'g-fail', textContent: 'не удалось загрузить' })); }));
  $('galleryView').hidden = true;
  $('galleryGrid').hidden = false;
  $('gallery').hidden = false;
  document.body.classList.add('modal-open');
  $('galleryClose').focus();
}

function showPhoto(i) {
  const n = gallery.photos.length;
  if (!n) return;
  gallery.index = (i + n) % n;
  const p = gallery.photos[gallery.index];
  $('galleryImg').src = photoUrl(p.id);
  $('galleryImg').alt = p.mark;
  $('galleryCaption').textContent = `${gallery.index + 1} / ${n} · ${p.mark} · ${p.user_name} · ${formatMoment(p.created_at)}`;
  $('galleryView').hidden = false;
  $('galleryGrid').hidden = true;
  $('galleryPrev').hidden = $('galleryNext').hidden = n < 2;
}

function closeGallery() {
  if ($('gallery').hidden) return;
  $('gallery').hidden = true;
  $('galleryImg').removeAttribute('src');
  document.body.classList.remove('modal-open');
  if (gallery.opener && gallery.opener.focus) gallery.opener.focus();
}

function galleryBack() {
  // Из просмотра одного фото — назад к сетке; из сетки — закрыть.
  if (!$('galleryView').hidden && gallery.photos.length > 1) {
    $('galleryView').hidden = true;
    $('galleryGrid').hidden = false;
  } else {
    closeGallery();
  }
}

function bindProofing() {
  document.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('[data-gallery]') : null;
    if (btn) { openGallery(btn.dataset.gallery, btn); return; }
    const thumb = e.target.closest ? e.target.closest('[data-photo]') : null;
    if (thumb) showPhoto(Number(thumb.dataset.photo));
  });
  $('galleryClose').addEventListener('click', closeGallery);
  $('galleryPrev').addEventListener('click', () => showPhoto(gallery.index - 1));
  $('galleryNext').addEventListener('click', () => showPhoto(gallery.index + 1));
  $('gallery').addEventListener('click', (e) => { if (e.target === $('gallery')) closeGallery(); });
  document.addEventListener('keydown', (e) => {
    if ($('gallery').hidden) return;
    if (e.key === 'Escape') galleryBack();
    else if (e.key === 'ArrowLeft' && !$('galleryView').hidden) showPhoto(gallery.index - 1);
    else if (e.key === 'ArrowRight' && !$('galleryView').hidden) showPhoto(gallery.index + 1);
  });
  $('pBatchSearch').addEventListener('input', renderBatchTable);
  $('proofRetry').addEventListener('click', async () => {
    await refreshProofMeta();
    loadSummary(true);
  });
}

/* ─── КЦ: кондитерский цех ────────────────────────────────────────────────── */

// Каждая запись — самостоятельная операция: штуки разных операций одной продукции — не готовый выпуск.
let kcData = null;
const KC_SORTS = {
  date: (a, b) => String(b.end_at || b.start_at).localeCompare(String(a.end_at || a.start_at)),
  pcs: (a, b) => (b.pcs || 0) - (a.pcs || 0),
  ph: (a, b) => (b.person_hours || 0) - (a.person_hours || 0),
  rate: (a, b) => (b.pcs_per_ph || 0) - (a.pcs_per_ph || 0),
  duration: (a, b) => (b.duration_min || 0) - (a.duration_min || 0),
  product: (a, b) => (a.product + a.operation).localeCompare(b.product + b.operation, 'ru'),
};
const none = (v) => v === null || v === undefined;
const decOr = (v, empty = '—') => (none(v) ? empty : dec(v));

function showKcError(message) {
  const box = $('kcError');
  if (!message) { box.hidden = true; $('kcBody').hidden = false; return; }
  box.hidden = false;
  $('kcErrorText').textContent = message + (kcData ? ' Ниже — последние полученные данные.' : '');
  $('kcBody').hidden = !kcData;
  $('periodLine').textContent = kcData ? 'Кондитерский цех: данные не обновились — показаны последние полученные' : 'Кондитерский цех: данные недоступны';
  $('sourceLine').textContent = 'Источник: API бота кондитерского цеха · ошибка подключения';
}

async function refreshKcMeta() {
  try {
    const m = await fetchMeta('kc');
    if (!isKc()) return;
    applyMeta(m);
    restoreFilters();
    syncControls();
    markSourceButtons(meta.sources);
  } catch (e) { /* остаёмся с тем, что есть */ }
}

function kcKpis(k, lfl) {
  const ok = lfl && lfl.available && !lfl.prev_empty;
  // [подпись, значение, единица, пояснение, ключ LFL, «лучше», формат прошлого]
  const cards = [
    ['Завершено операций', num(k.completed), '', k.cancelled ? `отменено: ${num(k.cancelled)} из ${num(k.started)} начатых` : `начато: ${num(k.started)}`,
     'completed', 1, num],
    ['В работе', num(k.in_work), '', 'операций на конец периода', null, 0, num],
    ['Обработано изделий', k.completed ? num(k.pcs) : NO_DATA, k.completed ? 'шт' : '', 'по операциям, с повторами ⓘ', 'pcs', 1,
     (v) => num(v) + ' шт'],
    ['Человеко-часы', k.completed ? dec(k.person_hours) : NO_DATA, k.completed ? 'чел·ч' : '', 'длительность × люди', 'person_hours', 0,
     (v) => dec(v) + ' чел·ч'],
    ['Ср. длительность', none(k.avg_duration) ? NO_DATA : num(Math.round(k.avg_duration)), none(k.avg_duration) ? '' : 'мин',
     none(k.avg_people) ? 'на операцию' : `людей в среднем: ${dec(k.avg_people)}`, 'avg_duration', 0,
     (v) => (none(v) ? NO_DATA : dec(v) + ' мин')],
    ['Ср. выработка', none(k.pcs_per_ph) ? NO_DATA : dec(k.pcs_per_ph), none(k.pcs_per_ph) ? '' : 'шт/чел·ч',
     'смесь операций ⓘ', 'pcs_per_ph', 1, (v) => (none(v) ? NO_DATA : dec(v) + ' шт/чел·ч')],
    ['Крит. остановки', num(k.pauses), '', k.pauses ? 'событий за период' : 'не было', 'pauses', -1, num],
    ['Простои', k.pauses ? hm(k.stop_minutes) : '0 мин', '', 'суммарная длительность', 'stop_minutes', -1, (v) => hm(v)],
  ];
  const tips = { 'Обработано изделий': kcData.notes.pcs, 'Ср. выработка': kcData.notes.mix };
  $('kKpis').innerHTML = cards.map(([label, value, unit, note, key, better, fmt]) => {
    let delta = '';
    if (ok && key && lfl.kpi[key]) {
      const c = lfl.kpi[key];
      let tip = `${lfl.prev_label}: ${fmt(c.prev)}`;
      if (c.change === null) tip += ' · сравнить не с чем';
      delta = `<div class="k-delta" data-tip="${esc(tip)}">${trend(c.change, better)}<span class="k-vs">${esc(lflVs(lfl))}</span></div>`;
    }
    const tip = tips[label] ? ` data-tip="${esc(tips[label])}" title="${esc(tips[label])}"` : '';
    return `<div class="kpi"${tip}><div class="k-label">${esc(label)}</div>` +
      `<div class="k-value${value === NO_DATA ? ' k-none' : ''}"><span class="k-num">${esc(value)}</span>` +
      (unit ? `<span class="k-unit">${esc(unit)}</span>` : '') + '</div>' +
      `<div class="k-note">${esc(note)}</div>${delta}</div>`;
  }).join('');
  fitValues($('kKpis'));
}

function kcLflCaption(lfl) {
  const el = $('kLflCaption');
  el.hidden = false;
  if (!lfl || !lfl.available) { el.textContent = 'Сравнение с прошлым периодом: ' + ((lfl && lfl.reason) || 'недоступно'); return; }
  const cut = lfl.partial ? `, последний день до ${lfl.cutoff} — как сегодня` : '';
  el.textContent = lfl.prev_empty
    ? `За прошлый период ${lfl.prev_label} с этими фильтрами данных нет — сравнивать не с чем.`
    : `▲▼ — изменение к прошлому периоду ${lfl.prev_label} (${lfl.days} дн.${cut}) с теми же фильтрами`;
}

function kcActivity(employees) {
  const rows = (employees || []).filter((e) => e.marks).slice(0, 14);
  if (!rows.length) return '<div class="empty">Нет данных за выбранный период</div>';
  const top = Math.max(1, ...rows.flatMap((e) => e.hours));
  const head = '<div class="lh-name"></div>' + Array.from({ length: 24 }, (_, h) =>
    `<div class="lh-hour${FILTERS.hour !== '' && Number(FILTERS.hour) === h ? ' on' : ''}">${String(h).padStart(2, '0')}</div>`).join('');
  const body = rows.map((e) => `<div class="lh-name" title="${esc(e.employee)}">${esc(e.employee)}</div>` + e.hours.map((v, h) =>
    `<div class="lh-cell" style="${loadTone(v / top * 100)}" data-tip="${esc(`${e.employee}, ${String(h).padStart(2, '0')}:00 — отметок: ${v}`)}">${v || ''}</div>`
  ).join('')).join('');
  return `<div class="loadheat">${head}${body}</div>`;
}

function kcOpCells(r) {
  const end = r.end_time ? `${r.end_date !== r.start_date ? esc(r.end_date.slice(5)) + ' ' : ''}${esc(r.end_time)}` : (r.status === 'open' ? '…' : '—');
  const dur = none(r.duration_min) ? (r.status === 'open' && !none(r.open_min) ? `идёт ${hm(r.open_min)}` : '—')
    : (r.valid ? hm(r.duration_min) : `<span class="delta bad" title="Некорректное время — не входит в средние и нормы">${esc(hm(r.duration_min))}</span>`);
  return [
    esc(r.date.slice(5)),
    `<span class="tag" title="${esc(r.product)}">${esc(r.product)}</span>`,
    esc(r.operation),
    `<span class="cellbox">${esc(r.start_time)}–${end}${r.midnight ? ' <small class="muted" title="Через полночь">🌙</small>' : ''}</span>`,
    dur,
    none(r.pcs) ? '—' : num(r.pcs),
    none(r.people) ? '—' : num(r.people),
    decOr(r.person_hours),
    decOr(r.pcs_per_hour),
    decOr(r.pcs_per_ph),
    esc(r.started_by),
    esc(r.closed_by || '—'),
    `<span class="badge ${r.status === 'closed' ? 'done' : r.status === 'open' ? 'active' : 'cancelled'}">${esc(r.status_name)}</span>`,
  ];
}

const KC_OP_COLUMNS = [{ title: 'Дата' }, { title: 'Продукция' }, { title: 'Операция' }, { title: 'Время' },
  { title: 'Длительность', num: true, sort: 'duration' }, { title: 'Шт', num: true, sort: 'pcs' }, { title: 'Людей', num: true },
  { title: 'Чел·ч', num: true, sort: 'ph' }, { title: 'Шт/ч', num: true }, { title: 'Шт/чел·ч', num: true, sort: 'rate' },
  { title: 'Начал' }, { title: 'Завершил' }, { title: 'Статус' }];

function renderKcOps() {
  if (!kcData) return;
  const q = ($('kOpsSearch').value || '').trim().toLowerCase();
  const sort = KC_SORTS[$('kOpsSort').value] || KC_SORTS.date;
  const rows = (kcData.ops || []).filter((r) => !q ||
    `${r.product} ${r.operation} ${r.started_by} ${r.closed_by}`.toLowerCase().includes(q)).slice().sort(sort);
  const shown = (kcData.ops || []).length;
  $('kOpsNote').textContent = kcData.ops_total > shown
    ? `показаны последние ${num(shown)} из ${num(kcData.ops_total)} — сузь период`
    : `операций: ${num(rows.length)}${rows.length !== shown ? ' из ' + num(shown) : ''}`;
  renderTable($('kOpsTable'), KC_OP_COLUMNS, rows, kcOpCells);
  // Заголовок с сортировкой — клик меняет порядок (на телефоне — список «Сортировка»).
  $('kOpsTable').querySelectorAll('thead th').forEach((th, i) => {
    const key = KC_OP_COLUMNS[i].sort;
    if (!key) return;
    th.dataset.sort = key;
    th.classList.add('sortable');
    if ($('kOpsSort').value === key) th.classList.add('sorted');
  });
}

function renderKcNorms() {
  if (!kcData) return;
  const q = ($('kNormsSearch').value || '').trim().toLowerCase();
  const rows = (kcData.norms || []).filter((r) => !q || `${r.product} ${r.operation}`.toLowerCase().includes(q));
  const lfl = kcData.lfl || {};
  $('kNormsNote').textContent = `пар: ${num(rows.length)} · только завершённые операции`;
  renderTable($('kNormsTable'),
    [{ title: 'Продукция' }, { title: 'Операция' }, { title: 'Выполнений', num: true }, { title: 'Шт', num: true },
     { title: 'Ср. время', num: true }, { title: 'Мин – макс', num: true }, { title: 'Ср. людей', num: true },
     { title: 'Чел·ч', num: true }, { title: 'Шт/ч', num: true }, { title: 'Шт/чел·ч', num: true },
     { title: lfl.available ? `К ${lfl.prev_label}` : 'К прошлому периоду', num: true }],
    rows,
    (r) => [`<span class="tag" title="${esc(r.product)}">${esc(r.product)}</span>`, esc(r.operation), num(r.completed), num(r.pcs),
            esc(dur(r.avg_duration)),
            none(r.min_duration) ? '—' : `${esc(hm(r.min_duration))} – ${esc(hm(r.max_duration))}`,
            decOr(r.avg_people), dec(r.person_hours), decOr(r.pcs_per_hour), `<b>${decOr(r.pcs_per_ph)}</b>`,
            none(r.prev_pcs_per_ph) ? '<span class="muted">не было</span>'
              : `<span class="cellbox">${trend(r.pcs_per_ph_change, 1)} <small class="muted">было ${dec(r.prev_pcs_per_ph)}</small></span>`],
    (r) => ({ key: 'operation', value: r.operation }));
}

function kcGroupTable(el, rows, key, title) {
  renderTable(el,
    [{ title }, { title: 'Операций', num: true }, { title: 'Шт', num: true }, { title: 'Чел·ч', num: true },
     { title: 'Ср. время', num: true }, { title: 'Ср. людей', num: true }, { title: 'Шт/ч', num: true }, { title: 'Шт/чел·ч', num: true }],
    rows,
    (r) => [`<span class="tag">${esc(r[key])}</span>`, num(r.completed), num(r.pcs), dec(r.person_hours), esc(dur(r.avg_duration)),
            decOr(r.avg_people), decOr(r.pcs_per_hour), decOr(r.pcs_per_ph)],
    (r) => ({ key, value: r[key] }));
}

function renderKc(data) {
  kcData = data;
  lastData = data;
  if (meta && meta.stub) refreshKcMeta();
  const k = data.kpi;
  const lfl = data.lfl || {};
  $('kNote').textContent = `⚠ «Обработано изделий» — ${data.notes.pcs}; это не уникальный готовый выпуск цеха. ${data.notes.mix}`;
  kcKpis(k, lfl);
  kcLflCaption(lfl);
  const daily = data.daily || [];
  const activeDate = FILTERS.dateFrom === FILTERS.dateTo ? FILTERS.dateFrom : undefined;
  $('kChartOps').innerHTML = barChart(daily.map((d) => ({ label: d.date.slice(5), key: d.date, value: d.completed })),
    { color: '#3b6ef5', filterKey: 'date', unit: 'операций', activeValue: activeDate,
      compare: lfl.available ? daily.map((d) => d.lfl_completed) : [],
      compareTip: (i) => (daily[i].lfl_date ? ` · ${daily[i].lfl_date.slice(5)} (неделей раньше): ${num(daily[i].lfl_completed)}` : '') })
    + (daily.length && lfl.available ? chartLegend([['box', '#3b6ef5', 'за день'], ['box', GHOST_COLOR, 'неделей раньше']]) : '');
  $('kChartPcs').innerHTML = barChart(daily.map((d) => ({ label: d.date.slice(5), key: d.date, value: d.pcs })),
    { color: '#7c5cf5', filterKey: 'date', unit: 'шт', activeValue: activeDate,
      compare: lfl.available ? daily.map((d) => d.lfl_pcs) : [],
      compareTip: (i) => ` · чел·ч ${dec(daily[i].person_hours)}` +
        (daily[i].lfl_date ? ` · неделей раньше: ${num(daily[i].lfl_pcs)} шт, ${pctText(daily[i].lfl_change)}` : '') })
    + (daily.length && lfl.available ? chartLegend([['box', '#7c5cf5', 'за день'], ['box', GHOST_COLOR, 'неделей раньше']]) : '');
  $('kChartHourly').innerHTML = barChart((data.hourly || []).map((h) => ({ label: h.label, key: String(h.hour), value: h.value })),
    { color: '#3fbf94', rotate: true, filterKey: 'hour', unit: 'шт', activeValue: FILTERS.hour,
      compareTip: (i) => ` · операций ${num(data.hourly[i].completed)}` });
  $('kChartStops').innerHTML = barChart((data.pause_daily || []).map((d) => ({ label: d.date.slice(5), key: d.date, value: d.minutes })),
    { color: '#e34948', filterKey: 'date', unit: 'мин', activeValue: activeDate,
      compareTip: (i) => ` · событий ${num(data.pause_daily[i].count)}` });
  const cap = isNarrow() ? 8 : 12;
  $('kChartProducts').innerHTML = barChart((data.top_products || []).slice(0, cap),
    { color: '#f0a63c', rotate: true, filterKey: 'product', unit: 'шт', activeValue: FILTERS.product });
  $('kChartOperations').innerHTML = barChart((data.top_operations || []).slice(0, cap),
    { color: '#2a78d6', rotate: true, filterKey: 'operation', unit: 'шт', activeValue: FILTERS.operation });
  $('kChartLaborP').innerHTML = barChart((data.labor_products || []).slice(0, cap),
    { color: '#eb6834', rotate: true, filterKey: 'product', unit: 'чел·ч', activeValue: FILTERS.product });
  $('kChartLaborO').innerHTML = barChart((data.labor_operations || []).slice(0, cap),
    { color: '#0f766e', rotate: true, filterKey: 'operation', unit: 'чел·ч', activeValue: FILTERS.operation });

  renderTable($('kHeavyTable'),
    [{ title: 'Продукция' }, { title: 'Операция' }, { title: 'Чел·ч', num: true }, { title: 'Выполнений', num: true },
     { title: 'Ср. время', num: true }, { title: 'Ср. людей', num: true }, { title: 'Шт/чел·ч', num: true }],
    data.heavy || [],
    (r) => [`<span class="tag">${esc(r.product)}</span>`, esc(r.operation), `<b>${dec(r.person_hours)}</b>`, num(r.completed),
            esc(dur(r.avg_duration)), decOr(r.avg_people), decOr(r.pcs_per_ph)],
    (r) => ({ key: 'operation', value: r.operation }));

  $('kChartReasons').innerHTML = pieChart(data.pause_reasons || []);
  renderTable($('kShiftTable'),
    [{ title: 'Смена' }, { title: 'Операций', num: true }, { title: 'Шт', num: true }, { title: 'Чел·ч', num: true },
     { title: 'Шт/чел·ч', num: true }],
    data.shifts || [],
    (r) => [`<span class="cellbox">${esc(r.shift_date.slice(5))} · ${esc(r.shift_name)}${r.midnight ? ` <small class="muted" title="Операций через полночь">🌙 ${num(r.midnight)}</small>` : ''}</span>`,
            num(r.completed), num(r.pcs), dec(r.person_hours), decOr(r.pcs_per_ph)],
    (r) => ({ key: 'shift', value: r.shift }));

  renderAnalysis(data.analysis, $('kAnalysis'));

  renderTable($('kEmployeeTable'),
    [{ title: 'Сотрудник' }, { title: 'Начал', num: true }, { title: 'Завершил', num: true }, { title: 'Отменил', num: true },
     { title: 'Операций с отметкой', num: true }, { title: 'Шт в завершённых им (группой)', num: true }, { title: 'Остановки', num: true }],
    data.employees || [],
    (e) => [`<span class="tag">${esc(e.employee)}</span>`, num(e.started), num(e.closed), num(e.cancelled), num(e.ops),
            num(e.pcs_closed), e.pauses ? `${num(e.pauses)} · ${hm(e.stop)}` : '—'],
    (e) => ({ key: 'employee', value: e.employee }));
  $('kActivity').innerHTML = kcActivity(data.employees);

  const opened = data.open_ops || [];
  $('kOpenNote').textContent = opened.length ? `сейчас в работе: ${num(opened.length)}` : 'нет операций в работе';
  renderTable($('kOpenTable'),
    [{ title: 'Продукция' }, { title: 'Операция' }, { title: 'Начало' }, { title: 'Идёт', num: true },
     { title: 'Людей', num: true }, { title: 'Начал' }],
    opened,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, esc(r.operation), `${esc(r.start_date.slice(5))} ${esc(r.start_time)}`,
            none(r.open_min) ? '—' : esc(hm(r.open_min)), none(r.people) ? '—' : num(r.people), esc(r.started_by)]);

  renderTable($('kDailyTable'),
    [{ title: 'Дата' }, { title: 'Начато', num: true }, { title: 'Завершено', num: true }, { title: 'Шт', num: true },
     { title: 'Чел·ч', num: true }, { title: 'Ср. время', num: true }, { title: 'Шт/чел·ч', num: true },
     { title: 'Простои', num: true }, { title: 'Шт неделей раньше', num: true }],
    daily,
    (d) => [`<span class="cellbox">${esc(d.date)} <span class="muted">${esc(d.weekday)}</span></span>`, num(d.started),
            num(d.completed), num(d.pcs), dec(d.person_hours), esc(dur(d.avg_duration)), decOr(d.pcs_per_ph),
            d.pauses ? `${num(d.pauses)} · ${hm(d.stop)}` : '—',
            d.lfl_date ? `<span class="cellbox">${num(d.lfl_pcs)} ${trend(d.lfl_change, 1)}</span>` : '—'],
    (d) => ({ key: 'date', value: d.date }));

  renderTable($('kPauseTable'),
    [{ title: 'Дата' }, { title: 'Причина' }, { title: 'Время' }, { title: 'Длительность', num: true }, { title: 'Сотрудник' }],
    data.pause_rows || [],
    (r) => [esc(r.date), esc(r.reason), `${esc(r.start_time)}–${esc(r.end_time)}`, esc(hm(r.minutes)), esc(r.user_name)]);

  $('kNormsWarn').textContent = `ⓘ ${data.notes.norms} Шт/чел·ч в паре = Σ шт / Σ чел·ч, а не среднее по записям; ` +
    'мин – макс — по корректно закрытым операциям.';
  renderKcNorms();
  kcGroupTable($('kProductTable'), data.products || [], 'product', 'Продукция');
  kcGroupTable($('kOperationTable'), data.operations || [], 'operation', 'Операция');
  renderKcOps();
  mountExportButtons();

  const src = data.source || {};
  const fetched = formatMoment(src.fetched_at);
  const modes = { 'kc-api': 'API бота кондитерского цеха', 'kc-demo': 'тестовые данные кондитерского цеха (demo)' };
  const skipped = (data.excluded_dates || []).length ? ` · скрыто дат: ${data.excluded_dates.join(', ')}` : '';
  $('sourceLine').textContent = `Источник: ${modes[src.mode] || src.mode || '—'} · снимок от ${fetched} · автообновление ${meta ? meta.refresh_seconds : 60} с${skipped}`;
  $('periodLine').textContent = `Период ${FILTERS.dateFrom || 'начало'} – ${FILTERS.dateTo || 'сегодня'} · ${SHIFT_NAMES[FILTERS.shift]} · ` +
    `операций завершено: ${num(k.completed)} · обработано изделий по операциям: ${k.completed ? num(k.pcs) + ' шт' : 'нет данных'} · обновлено ${fetched}`;
}

function bindKc() {
  $('kOpsSearch').addEventListener('input', renderKcOps);
  $('kOpsSort').addEventListener('change', renderKcOps);
  $('kNormsSearch').addEventListener('input', renderKcNorms);
  $('kOpsTable').addEventListener('click', (e) => {
    const th = e.target.closest ? e.target.closest('th[data-sort]') : null;
    if (!th) return;
    $('kOpsSort').value = th.dataset.sort;
    renderKcOps();
  });
  $('kcRetry').addEventListener('click', async () => {
    await refreshKcMeta();
    loadSummary(true);
  });
}

/* ─── Гант: все цеха на одной шкале + общий тайминг ───────────────────────── */

const GANTT = { date: '', window: 'full', sections: ['bhm', 'fk', 'proofing', 'kc'], product: 'all', equipment: 'all', search: '',
  // fold: явный выбор «развёрнут/свёрнут» по продукту; по умолчанию продукты свёрнуты
  // при поиске — своя карта: найденные операции видны сразу, свернуть можно и в поиске
  avgFrom: '', avgTo: '', days: [], fold: new Map(), searchFold: new Map(), avgOpen: new Set() };
const G_COLORS = { bhm: '#eb6834', fk: '#2a78d6', proofing: '#1baf7a', kc: '#7c5cf5' };
const G_STAGE_COLORS = { bhm: '#eb6834', fk: '#2a78d6', proofing: '#1baf7a', baking: '#0f766e', kc: '#7c5cf5', pack: '#9ca3af' };
const G_NAMES = { bhm: 'Тестозаготовочный цех', fk: 'Формовочный цех', proofing: 'Печной цех', kc: 'Кондитерский цех' };
let ganttData = null;

function showGanttError(message) {
  $('gError').hidden = !message;
  $('gErrorText').textContent = message || '';
}

function ganttQuery(force) {
  const p = new URLSearchParams({ source: 'gantt', window: GANTT.window, sections: GANTT.sections.join(','),
    product: GANTT.product, equipment: GANTT.equipment });
  if (GANTT.date) p.set('date', GANTT.date);
  if (GANTT.avgFrom && GANTT.avgTo) { p.set('avg_from', GANTT.avgFrom); p.set('avg_to', GANTT.avgTo); }
  if (force) p.set('refresh', '1');
  return p.toString();
}

// Даты — календарём: выбрать можно любой день в пределах данных (min–max), ‹ › листают дни с данными.
function fillGanttTools(m) {
  GANTT.days = (m.days || []).slice();
  const first = GANTT.days[0] || '';
  const last = GANTT.days[GANTT.days.length - 1] || '';
  const inRange = (d) => d && (!first || d >= first) && (!last || d <= last);
  if (!inRange(GANTT.date)) GANTT.date = last;
  if (!inRange(GANTT.avgFrom)) GANTT.avgFrom = m.avg_from || '';
  if (!inRange(GANTT.avgTo)) GANTT.avgTo = m.avg_to || '';
  ['gDate', 'gAvgFrom', 'gAvgTo'].forEach((id) => {
    $(id).min = first;
    $(id).max = last;
    $(id).title = first ? `данные с ${first.split('-').reverse().join('.')} по ${last.split('-').reverse().join('.')}` : 'нет данных';
  });
  $('gDate').value = GANTT.date;
  $('gAvgFrom').value = GANTT.avgFrom;
  $('gAvgTo').value = GANTT.avgTo;
  $('gWindow').innerHTML = Object.entries(m.windows || {}).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('');
  $('gWindow').value = GANTT.window;
  fillSelect($('gProduct'), m.products || [], 'Все');
  if (GANTT.product !== 'all' && !(m.products || []).includes(GANTT.product)) GANTT.product = 'all';
  $('gProduct').value = GANTT.product;
  fillSelect($('gEquipment'), m.equipment || [], 'Все');
  if (GANTT.equipment !== 'all' && !(m.equipment || []).includes(GANTT.equipment)) GANTT.equipment = 'all';
  $('gEquipment').value = GANTT.equipment;
  $('gSections').innerHTML = (m.sections_list || []).map((x) => {
    const on = GANTT.sections.includes(x.key);
    const err = (m.section_errors || {})[x.key];
    return `<button class="g-sec${on ? ' on' : ''}" type="button" data-gsec="${esc(x.key)}" aria-pressed="${on}" ` +
      `${err ? `title="${esc(err)}"` : ''}><i style="background:${G_COLORS[x.key]}"></i>${esc(x.title)}${err ? ' ⚠' : ''}</button>`;
  }).join('');
}

function gMinutes(iso, start) { return (new Date(iso) - start) / 60000; }

function gHours(start, total, relative) {
  const out = [];
  for (let m = 0; m <= total; m += 60) {
    if (relative) { out.push({ m, h: m / 60, rel: true }); continue; }
    const d = new Date(start.getTime() + m * 60000);
    const h = Number(new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', hourCycle: 'h23', timeZone: (meta && meta.timezone) || undefined }).format(d));
    out.push({ m, h });
  }
  return out;
}

function gBarText(b) {
  return [b.people ? `${b.people} чел` : '', b.qty ? `${num(b.qty)} ${b.unit}` : '', b.equipment].filter(Boolean).join(' · ');
}

function gBarTip(b) {
  const end = b.end_time || (b.running ? 'идёт' : 'конец не отмечен');
  return [`${b.section_title} · ${b.unified || b.product} — ${b.operation}`,
    `${b.start_time}–${end}${none(b.duration_min) ? '' : ' (' + hm(b.duration_min) + ')'}`,
    b.people ? `людей: ${b.people}${none(b.person_hours) ? '' : ', чел·ч ' + dec(b.person_hours)}` : 'людей: нет данных',
    b.qty ? `выработка: ${num(b.qty)} ${b.unit}${none(b.rate) ? '' : ` · ${dec(b.rate)} ${b.unit}/ч`}${none(b.rate_per_person) ? '' : ` · ${dec(b.rate_per_person)} ${b.unit}/чел·ч`}` : 'выработка: нет данных',
    b.equipment ? `оборудование: ${b.equipment}` : '',
    b.operation_raw && b.operation_raw !== b.operation ? `в источнике: «${b.operation_raw}»` : '',
    b.unified && b.unified !== b.product ? `название в цехе: «${b.product}»` : '',
    b.source === 'chat' ? 'источник: переписка кондитерского цеха (неточно)' : '',
    b.flag === 'check' ? '⚠ время по времени сообщения' : (b.flag === 'invalid' ? '⚠ некорректное время — не в нормах' : ''),
    b.note || ''].filter(Boolean).join(' · ');
}

// Выработка в час на человека; людей нет в боте — в час на всех (бледно), разные операции — не складываем.
function gRateCell(x, several) {
  if (several) return '<span class="muted" title="Штуки разных операций не складываем — смотри по операциям">по операциям</span>';
  if (!none(x.rate_per_person)) return `<b>${dec(x.rate_per_person)}</b> <small>${esc(x.rate_unit)}/чел·ч</small>`;
  if (!none(x.rate)) return `<span class="muted" title="Людей в этом цехе бот не знает — выработка в час на всех">${dec(x.rate)} ${esc(x.rate_unit)}/ч</span>`;
  return '<span class="muted">—</span>';
}

function gPeopleCell(x, product) {
  if (none(x.people)) return '<span class="muted" title="Людей знает только кондитерский цех (бот и чат)">—</span>';
  const tip = product ? `Максимум одновременно занятых продуктом${x.people_ops ? `; сумма по операциям ${x.people_ops}` : ''}` : 'Людей на операции (максимум за запуск)';
  return `<span title="${esc(tip)}">${num(x.people)}</span>`;
}

const G_COLS = ['Оборудование', 'Людей', 'Выработка в час на чел.'];

// rows: [{label, sub, kind?: 'product'|'op', key?, open?, cols?:[html×3], mob?, mini?, lanes,
//          bars:[{start, draw_end, lane, color, cls, text, tip, idx}]}] и {group, color} — заголовки цехов.
function ganttGrid(rows, wStart, wEnd, nowIso, opts = {}) {
  const start = new Date(wStart);
  const total = (new Date(wEnd) - start) / 60000;
  const pct = (m) => Math.max(0, Math.min(100, m / total * 100));
  const hours = gHours(start, total, opts.relative);
  const ticks = hours.map((x) => `<span class="g-tick${!x.rel && (x.h === 8 || x.h === 20) ? ' shift' : ''}" style="left:${pct(x.m)}%">` +
    `${x.rel ? (x.h ? '+' + x.h + 'ч' : '0') : String(x.h).padStart(2, '0')}</span>`).join('');
  const nowM = nowIso ? gMinutes(nowIso, start) : -1;
  const nowLine = nowM > 0 && nowM < total ? `<i class="g-now" style="left:${pct(nowM)}%" title="сейчас"></i>` : '';
  const shiftLines = opts.relative ? '' : hours.filter((x) => (x.h === 8 || x.h === 20) && x.m > 0 && x.m < total)
    .map((x) => `<i class="g-shift" style="left:${pct(x.m)}%"></i>`).join('');
  const cols = !!opts.cols;
  const headCols = cols ? (opts.colTitles || G_COLS).map((c, i) => `<span class="g-c${i ? '' : ' g-c1'}">${esc(c)}</span>`).join('') : '';
  let html = `<div class="gantt${cols ? ' has-cols' : ''}" style="--hours:${Math.round(total / 60)}"><div class="g-row g-head">` +
    `<div class="g-label"><span class="g-name">${esc(opts.title || 'Продукт · операция')}</span>${headCols}</div>` +
    `<div class="g-track g-ticks">${ticks}</div></div>`;
  rows.forEach((r) => {
    if (r.group) {
      html += `<div class="g-group" style="--c:${r.color}"><span>${esc(r.group)}</span></div>`;
      return;
    }
    const bars = r.bars.map((b) => {
      const s = pct(gMinutes(b.start, start));
      const top = r.mini ? 10 : b.lane * 26 + 3;
      if (!b.draw_end) {
        return `<span class="g-mark${r.mini ? ' mini' : ''}" style="left:${s}%;top:${top - (r.mini ? 3 : 0)}px;--c:${b.color}" data-tip="${esc(b.tip)}" ` +
          `${none(b.idx) ? '' : `data-gbar="${b.idx}" role="button" tabindex="0"`}></span>`;
      }
      const e = pct(gMinutes(b.draw_end, start));
      return `<span class="g-bar ${b.cls || ''}${r.mini ? ' mini' : ''}" style="left:${s}%;width:${Math.max(e - s, 0.35)}%;top:${top}px;--c:${b.color}" ` +
        `data-tip="${esc(b.tip)}" ${none(b.idx) ? '' : `data-gbar="${b.idx}" role="button" tabindex="0"`}><b>${esc(r.mini ? '' : (b.text || ''))}</b></span>`;
    }).join('');
    const name = r.kind === 'product'
      ? `<button class="g-name g-toggle" type="button" data-gprod="${esc(r.key)}" aria-expanded="${r.open}">` +
        `<span><i class="g-caret">${r.open ? '▾' : '▸'}</i>${esc(r.label)}</span><small>${esc(r.sub)}</small>${r.mob ? `<small class="g-mob">${r.mob}</small>` : ''}</button>`
      : `<span class="g-name"><span>${esc(r.label)}</span>${r.sub ? `<small>${esc(r.sub)}</small>` : ''}${r.mob ? `<small class="g-mob">${r.mob}</small>` : ''}</span>`;
    const cells = cols ? (r.cols || ['', '', '']).map((c, i) => `<span class="g-c${i ? '' : ' g-c1'}">${c}</span>`).join('') : '';
    html += `<div class="g-row${r.kind ? ' g-' + r.kind : ''}"><div class="g-label" title="${esc(r.label + (r.sub ? ' · ' + r.sub : ''))}">${name}${cells}</div>` +
      `<div class="g-track" style="height:${(r.mini ? 1 : r.lanes) * 26 + 4}px">${shiftLines}${nowLine}${bars}</div></div>`;
  });
  return html + '</div>';
}

let gBarsFlat = [];

function renderGantt(data) {
  ganttData = data;
  lastData = data;
  if (data.day && data.day !== GANTT.date) { GANTT.date = data.day; if ($('gDate').value !== data.day) $('gDate').value = data.day; }
  const av = data.averages || {};
  if (av.from) { GANTT.avgFrom = av.from; GANTT.avgTo = av.to; $('gAvgFrom').value = av.from; $('gAvgTo').value = av.to; }
  const t = data.totals;
  const errors = data.section_errors || {};
  $('gWarn').hidden = !Object.keys(errors).length;
  $('gWarn').textContent = Object.keys(errors).length
    ? '⚠ Не все цеха доступны: ' + Object.entries(errors).map(([k, v]) => `${G_NAMES[k] || k} — ${v}`).join('; ') + '. Остальные показаны.' : '';
  const cards = [
    ['Операций в окне', num(t.ops), '', `закрыто ${num(t.closed)} · без конца/идут ${num(t.open)}`],
    ['Человеко-часы', t.people_known ? dec(t.person_hours) : NO_DATA, t.people_known ? 'чел·ч' : '', `людей известно у ${num(t.people_known)} операций`],
    ['Продуктов', num(t.products), '', `операций-строк: ${num(data.rows.length)}`],
    ['Из переписки', num(t.from_chat), '', `кондитерский цех, неточно${t.check ? ` · время по сообщению: ${num(t.check)}` : ''}`],
  ];
  $('gKpis').innerHTML = cards.map(([label, value, unit, note]) => `<div class="kpi"><div class="k-label">${esc(label)}</div>` +
    `<div class="k-value${value === NO_DATA ? ' k-none' : ''}"><span class="k-num">${esc(value)}</span>${unit ? `<span class="k-unit">${esc(unit)}</span>` : ''}</div>` +
    `<div class="k-note">${esc(note)}</div></div>`).join('');
  fitValues($('gKpis'));
  $('gLegend').innerHTML = Object.entries(G_NAMES).map(([k, v]) => `<span><i style="background:${G_COLORS[k]}"></i>${v}${k === 'kc' && t.from_chat ? ' (часть — из переписки, неточно)' : ''}</span>`).join('') +
    '<span><i class="lg-hatch"></i>время по сообщению / некорректно</span><span><i class="lg-run"></i>идёт сейчас</span>' +
    '<span><i class="lg-mark"></i>начало без отметки конца</span>';
  $('gChatWarn').hidden = !data.chat_note;
  $('gChatWarn').textContent = data.chat_note ? '⚠ ' + data.chat_note : '';
  drawGantt();
  renderGanttTable();
  renderTiming(data);
  renderAverages(data);
  const fetched = formatMoment(data.now);
  $('sourceLine').textContent = `Тайминги: тестозаготовочный, формовочный, печной и кондитерский цеха (бот${data.history_source ? ' + разобранная переписка' : ''}) · расчёт ${fetched}`;
  $('periodLine').textContent = `${data.day.split('-').reverse().join('.')} · ${data.window_title} · операций: ${num(t.ops)} · продуктов: ${num(t.products)}`;
}

function gProductKey(section, product) { return section + '|' + product; }

function gFoldMap() { return GANTT.search.trim() ? GANTT.searchFold : GANTT.fold; }

// Пометка строк из разобранной переписки КЦ: эти данные неточные.
function gChatText(fromChat, total) {
  if (!fromChat) return '';
  return fromChat === total ? 'из переписки, неточно' : `из переписки ${fromChat} из ${total}, неточно`;
}

function ganttRowsFiltered() {
  const q = GANTT.search.trim().toLowerCase();
  return (ganttData.rows || []).filter((r) => !q || `${r.product} ${r.operation} ${r.equipment}`.toLowerCase().includes(q));
}

function drawGantt() {
  if (!ganttData) return;
  const rows = [];
  gBarsFlat = [];
  const visible = ganttRowsFiltered();
  let section = '';
  // Цех → продукт (сворачивается) → его операции. В строке продукта — все его операции тонкой полосой.
  (ganttData.products || []).forEach((p) => {
    const ops = visible.filter((r) => r.section === p.section && r.product === p.product);
    if (!ops.length) return;
    if (p.section !== section) { section = p.section; rows.push({ group: p.section_title, color: G_COLORS[p.section] }); }
    const key = gProductKey(p.section, p.product);
    // Свёрнут по умолчанию; развёрнут сам, если выбран один продукт или идёт поиск — иначе найденное не видно.
    const fold = gFoldMap();
    const open = fold.has(key) ? fold.get(key) : (GANTT.product !== 'all' || !!GANTT.search.trim());
    const several = p.ops > 1;
    const color = G_COLORS[p.section];
    rows.push({ kind: 'product', key, open, label: p.product, mini: true, lanes: 1,
      sub: [p.ops > 1 ? `операций ${p.ops}` : ops[0].operation, none(p.busy_min) ? '' : 'занято ' + hm(p.busy_min),
        gChatText(ops.reduce((n, r) => n + r.bars.filter((b) => b.source === 'chat').length, 0), ops.reduce((n, r) => n + r.bars.length, 0))].filter(Boolean).join(' · '),
      cols: [p.equipment ? `<span class="g-clamp" title="${esc(p.equipment)}">${esc(p.equipment)}</span>` : '—', gPeopleCell(p, true), gRateCell(p, several)],
      mob: [p.equipment, none(p.people) ? '' : p.people + ' чел', several ? '' : gRateCell(p, false).replace(/<[^>]+>/g, '')].filter((x) => x && x !== '—').map(esc).join(' · '),
      bars: ops.flatMap((r) => r.bars.map((b) => ({ start: b.start, draw_end: b.draw_end, lane: 0, color,
        cls: [b.flag !== 'ok' ? 'hatch' : ''].join(' '), tip: `${r.operation}: ${gBarTip(b)}` }))) });
    if (!open) return;
    ops.forEach((r) => rows.push({ kind: 'op', label: r.operation, lanes: r.lanes,
      sub: [r.runs > 1 ? `запусков ${r.runs}` : '', gChatText(r.bars.filter((b) => b.source === 'chat').length, r.bars.length)].filter(Boolean).join(' · '),
      cols: [esc(r.equipment || '—'), gPeopleCell(r, false), gRateCell(r, false)],
      mob: [r.equipment, none(r.people) ? '' : r.people + ' чел', gRateCell(r, false).replace(/<[^>]+>/g, '')].filter((x) => x && x !== '—').map(esc).join(' · '),
      bars: r.bars.map((b) => {
        gBarsFlat.push(b);
        return { start: b.start, draw_end: b.draw_end, lane: b.lane, color, idx: gBarsFlat.length - 1,
          cls: [b.flag !== 'ok' ? 'hatch' : '', b.running ? 'run' : ''].join(' '), text: gBarText(b), tip: gBarTip(b) };
      }) }));
  });
  $('gChart').innerHTML = rows.length ? ganttGrid(rows, ganttData.start, ganttData.end, ganttData.now, { cols: true })
    : '<div class="empty">Нет операций в этом окне — выбери другие сутки или окно</div>';
  $('gDetail').hidden = true;
  const products = rows.filter((r) => r.kind === 'product').length;
  $('gNote').textContent = `${ganttData.window_title} · продуктов ${num(products)} · нажми на продукт, чтобы развернуть операции, на полосу — подробности`;
}

function renderGanttTable() {
  const bars = [];
  ganttRowsFiltered().forEach((r) => r.bars.forEach((b) => bars.push(b)));
  bars.sort((a, b) => a.start.localeCompare(b.start));
  $('gTableNote').textContent = `операций: ${num(bars.length)}`;
  renderTable($('gTable'),
    [{ title: 'Цех' }, { title: 'Продукт' }, { title: 'Операция' }, { title: 'Время' }, { title: 'Длит.', num: true },
     { title: 'Людей', num: true }, { title: 'Выработка', num: true }, { title: 'В час', num: true }, { title: 'На чел·ч', num: true },
     { title: 'Оборудование' }, { title: 'Источник' }],
    bars,
    (b) => [`<span class="g-dot" style="--c:${G_COLORS[b.section]}"></span>${esc(b.section_title)}`,
      `<span class="tag">${esc(b.unified || b.product)}</span>`, esc(b.operation),
      `<span class="cellbox">${esc(b.start_time)}–${esc(b.end_time || (b.running ? 'идёт' : '…'))}${b.flag !== 'ok' ? ` <span class="delta warn" title="${esc(gBarTip(b))}">⚠</span>` : ''}</span>`,
      none(b.duration_min) ? '—' : esc(hm(b.duration_min)), none(b.people) ? '—' : num(b.people),
      b.qty ? `${num(b.qty)} ${esc(b.unit)}` : '—', decOr(b.rate), decOr(b.rate_per_person),
      esc(b.equipment || '—'), b.source === 'chat' ? 'переписка (неточно)' : 'бот']);
}

function renderTiming(data) {
  const rows = data.timing || [];
  const chains = rows.filter((r) => r.stages_count > 1).length;
  $('tNote').textContent = `ⓘ Этапы связаны по единому продукту (справочник продуктов), а не по номеру партии — общего номера у ботов нет. ` +
    `Фасовка — отдельный бот, пока не подключён: показываем только «передано на фасовку» из переписки кондитерского цеха (время по сообщению, неточно). ` +
    `Продуктов, прошедших больше одного цеха в этом окне: ${chains}.`;
  $('tLegend').innerHTML = (data.stages || []).map((s) => `<span><i style="background:${G_STAGE_COLORS[s.key]}"></i>${esc(s.title)}</span>`).join('');
  // Строка — продукт, дорожка — этап, полосы — реальные операции этапа (не сплошной интервал).
  const chartRows = rows.map((r) => {
    const present = r.stages.filter((s) => s.present);
    return { label: r.product, sub: `этапов ${r.stages_count}${none(r.lead_min) ? '' : ' · ' + hm(r.lead_min)}`,
      lanes: Math.max(1, present.length),
      bars: present.flatMap((s, lane) => (s.bars || []).map((op) => ({ start: op.start, draw_end: op.end || null, lane,
        color: G_STAGE_COLORS[s.key], text: s.title,
        tip: `${r.product} · ${s.title}: этап ${s.start_time}–${s.end_time || '…'} · операций ${s.ops} · ${s.qty}` +
          (s.people_max ? ` · до ${s.people_max} чел.` : '') + (none(s.gap_min) ? '' : ` · ожидание после прошлого этапа ${hm(s.gap_min)}`) }))) };
  });
  $('tChart').innerHTML = chartRows.length ? ganttGrid(chartRows, data.start, data.end, data.now) : '<div class="empty">Нет данных</div>';
  $('tTableNote').textContent = `продуктов: ${num(rows.length)}`;
  const stageCols = (data.stages || []).map((s) => ({ title: s.title }));
  renderTable($('tTable'), [{ title: 'Продукт' }, { title: 'Этапов', num: true }, { title: 'Начало → конец', num: true }].concat(stageCols),
    rows,
    (r) => [`<span class="tag">${esc(r.product)}</span>`, num(r.stages_count), none(r.lead_min) ? '—' : esc(hm(r.lead_min))].concat(
      r.stages.map((s) => {
        if (s.key === 'pack') {
          const hs = s.handoffs || [];
          return hs.length ? hs.map((h) => `передано ${num(h.qty)} ${esc(h.unit)} в ${esc(h.time)}`).join('<br>') : '<span class="muted">нет источника</span>';
        }
        if (!s.present) return '<span class="muted">—</span>';
        return `<span class="cellbox"><b>${esc(s.start_time.slice(6))}–${esc(s.end_time ? s.end_time.slice(6) : '…')}</b>` +
          `<small class="stage-who">${none(s.span_min) ? '' : esc(hm(s.span_min)) + ' · '}${esc(s.qty)}${s.people_max ? ` · до ${s.people_max} чел.` : ''}` +
          `${none(s.gap_min) ? '' : ` · ожидание ${esc(hm(s.gap_min))}`}</small></span>`;
      })),
    (r) => ({ key: 'gproduct', value: r.product }));
}

/* Среднее по продукту: средний день продукта, разворачивается до этапов и операций. */

function aDay(d) { return d ? d.split('-').reverse().join('.') : ''; }

function renderAverages(data) {
  const av = data.averages || { products: [] };
  const q = GANTT.search.trim().toLowerCase();
  const all = av.products || [];
  const list = all.filter((p) => !q || `${p.product} ${p.ops.map((o) => o.operation).join(' ')}`.toLowerCase().includes(q));
  $('aNote').textContent = `ⓘ Период ${aDay(av.from)} — ${aDay(av.to)}, дней с данными: ${num(av.days || 0)}. ` +
    (list.some((p) => p.from_chat) ? 'Среднее по кондитерскому цеху посчитано по переписке — оно неточное, для норм не годится. ' : '') +
    'Время операции — от начала работы с продуктом в эти рабочие сутки; «длится» — от первого начала до последнего конца, ' +
    '«занято» — без пауз между запусками. Операции из переписки без отметки конца в длительность не входят.';
  $('aCount').textContent = `продуктов: ${num(list.length)}${list.length !== all.length ? ` из ${num(all.length)}` : ''}`;
  if (!list.length) { $('aList').innerHTML = '<div class="empty">Нет операций за этот период</div>'; return; }
  $('aList').innerHTML = list.map((p) => {
    const i = all.indexOf(p);
    const open = GANTT.avgOpen.has(p.product);
    const chips = p.stages.map((s) => `<span class="a-chip" style="--c:${G_STAGE_COLORS[s.key]}" title="${esc(s.title)}: начало +${esc(hm(s.offset_min))}, ` +
      `длится ${none(s.span_min) ? 'нет конца' : esc(hm(s.span_min))}, дней ${s.days}"><i></i>${esc(s.title)} ${none(s.span_min) ? '…' : esc(hm(s.span_min))}</span>`).join('');
    return `<details class="avg-item" data-aidx="${i}"${open ? ' open' : ''}><summary>` +
      `<span class="a-name"><b>${esc(p.product)}</b><small>${esc(p.sections.join(', '))} · дней ${num(p.days)} · операций ${num(p.ops.length)}` +
      `${p.from_chat ? ' · <span class="chat-flag">из переписки, неточно</span>' : ''}</small></span>` +
      `<span class="a-lead"><b>${none(p.lead_min) ? '—' : esc(hm(p.lead_min))}</b><small>${p.days > 1 && !none(p.lead_min_min) ? `от ${esc(hm(p.lead_min_min))} до ${esc(hm(p.lead_min_max))}` : 'весь путь за день'}</small></span>` +
      `${aOutputCell(p)}` +
      `<span class="a-ph"><b>${none(p.person_hours_day) ? '—' : dec(p.person_hours_day)}</b><small>чел·ч в день</small></span>` +
      `<span class="a-chips">${chips}</span></summary><div class="avg-body">${open ? averageBody(p) : ''}</div></details>`;
  }).join('');
}

// Выработка продукта: последний этап с количеством, максимум по операциям (не сумма операций).
function aOutputText(p) {
  const o = p.output;
  if (!o) return 'нет количества в закрытых операциях';
  return `${num(o.qty_day)} ${o.unit} в день — ${o.stage_title}, «${o.operation}»` +
    `${o.days < p.days ? `, по ${o.days} дн. из ${p.days}` : ''}` +
    (none(o.rate_per_person) ? '; людей нет — на человека не считаем' : `; ${dec(o.rate_per_person)} ${o.unit} на чел·ч всех операций продукта`);
}

function aOutputCell(p) {
  const o = p.output;
  const tip = `Выработка продукта: ${aOutputText(p)}. Берём последний этап с количеством и самую массовую операцию — ` +
    'операции не складываем, одни и те же изделия проходят несколько операций.';
  if (!o) return `<span class="a-out" title="${esc(tip)}"><b>—</b><small>выработка в день</small></span>`;
  return `<span class="a-out" title="${esc(tip)}"><b>${num(o.qty_day)} ${esc(o.unit)}</b>` +
    `<small>в день${none(o.rate_per_person) ? '' : ` · ${dec(o.rate_per_person)} ${esc(o.unit)}/чел·ч`}</small></span>`;
}

function averageBody(p) {
  const rows = [];
  let end = 60;
  p.stages.forEach((s) => {
    const ops = p.ops.filter((o) => o.stage === s.key);
    const color = G_STAGE_COLORS[s.key];
    const sEnd = (s.offset_min || 0) + (s.span_min || 0);
    end = Math.max(end, sEnd);
    const rel = (m) => new Date((m || 0) * 60000).toISOString();
    rows.push({ kind: 'stage', label: s.title, sub: `этап целиком · дней ${s.days}${none(s.busy_min) ? '' : ' · занято ' + hm(s.busy_min)}`, lanes: 1,
      cols: [none(s.span_min) ? '—' : esc(hm(s.span_min)), '', ''],
      bars: [{ start: rel(s.offset_min), draw_end: none(s.span_min) ? null : rel(s.offset_min + s.span_min), lane: 0, color, cls: 'stage',
        text: none(s.span_min) ? '' : hm(s.span_min), tip: `${s.title}: начало +${hm(s.offset_min)} от старта продукта, длится ${none(s.span_min) ? '—' : hm(s.span_min)}` }] });
    ops.forEach((o) => {
      end = Math.max(end, (o.offset_min || 0) + (o.span_min || 0));
      rows.push({ kind: 'op', label: o.operation, lanes: 1,
        sub: `дней ${o.days}${o.no_end ? ` · без конца ${o.no_end}` : ''}${o.from_chat ? ' · из переписки' : ''}`,
        cols: [none(o.span_min) ? '—' : esc(hm(o.span_min)), none(o.people_avg) ? '<span class="muted">—</span>' : dec(o.people_avg), gRateCell(o, false)],
        bars: [{ start: rel(o.offset_min), draw_end: none(o.span_min) ? null : rel(o.offset_min + o.span_min), lane: 0, color,
          cls: o.no_end ? 'hatch' : '', text: [none(o.people_avg) ? '' : dec(o.people_avg) + ' чел', o.qty_day !== '—' ? o.qty_day : ''].filter(Boolean).join(' · '),
          tip: `${o.operation}: начало +${hm(o.offset_min)}, длится ${none(o.span_min) ? '—' : hm(o.span_min)}, занято ${none(o.busy_min) ? '—' : hm(o.busy_min)}` +
            `${none(o.people_avg) ? '' : `, людей ${dec(o.people_avg)}`}, выработка в день ${o.qty_day}${o.equipment ? ', ' + o.equipment : ''}` }] });
    });
  });
  const total = Math.ceil(end / 60) * 60;
  const chart = ganttGrid(rows, new Date(0).toISOString(), new Date(total * 60000).toISOString(), null,
    { relative: true, cols: true, title: 'Этап · операция', colTitles: ['Длится', 'Людей', 'Выработка в час на чел.'] });
  const table = '<div class="table-wrap"><table class="table"><thead><tr><th>Этап</th><th>Операция</th><th class="num">Дней</th>' +
    '<th class="num">Начало</th><th class="num">Длится</th><th class="num">Занято</th><th class="num">Людей</th>' +
    '<th class="num">В день</th><th class="num">На чел·ч</th><th>Оборудование</th></tr></thead><tbody>' +
    p.ops.map((o) => `<tr><td data-label="Этап"><span class="g-dot" style="--c:${G_STAGE_COLORS[o.stage]}"></span>${esc(o.stage_title)}</td>` +
      `<td data-label="Операция">${esc(o.operation)}</td><td class="num" data-label="Дней">${num(o.days)}</td>` +
      `<td class="num" data-label="Начало">+${esc(hm(o.offset_min))}</td><td class="num" data-label="Длится">${none(o.span_min) ? '—' : esc(hm(o.span_min))}</td>` +
      `<td class="num" data-label="Занято">${none(o.busy_min) ? '—' : esc(hm(o.busy_min))}</td><td class="num" data-label="Людей">${none(o.people_avg) ? '—' : dec(o.people_avg)}</td>` +
      `<td class="num" data-label="В день">${esc(o.qty_day)}</td><td class="num" data-label="На чел·ч">${gRateCell(o, false)}</td>` +
      `<td data-label="Оборудование">${esc(o.equipment || '—')}</td></tr>`).join('') + '</tbody></table></div>';
  const output = `<p class="a-output"><b>Выработка продукта:</b> ${esc(aOutputText(p))}.</p>`;
  return `${output}<div class="gantt-scroll">${chart}</div>${table}`;
}

async function loadGanttMap() {
  try {
    const r = await fetch('/api/gantt/map', { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'сервис недоступен');
    const sections = d.sections || {};
    const hint = (list) => (list && list.length ? `<small class="stage-who">похоже на: ${list.map(esc).join('; ')}</small>` : '');
    const rows = [];
    Object.entries(d.products || {}).forEach(([sec, names]) => names.forEach((n) => rows.push({ sec, name: n, unified: ((d.map || {})[sec] || {})[n] || '' })));
    const mapped = rows.filter((x) => x.unified).length;
    $('mNote').textContent = d.upload_enabled ? (d.updated_at ? `загружен ${formatMoment(d.updated_at)}${d.file ? ' · ' + d.file : ''}` : 'ещё не загружали')
      : 'загрузка выключена: на сервере не задан PLAN_UPLOAD_TOKEN';
    $('mUpload').disabled = !d.upload_enabled;
    const hints = rows.filter((x) => (d.similar || {})[x.name]).length;
    $('mCount').textContent = `сведено ${num(mapped)} из ${num(rows.length)} названий${hints ? ` · с подсказкой «похоже» ${num(hints)}` : ''}`;
    rows.sort((a, b) => (b.unified ? 1 : 0) - (a.unified ? 1 : 0) || a.sec.localeCompare(b.sec) || a.name.localeCompare(b.name, 'ru'));
    renderTable($('mTable'), [{ title: 'Цех' }, { title: 'Продукт в цехе' }, { title: 'Единый продукт' }], rows,
      (x) => [esc(sections[x.sec] || x.sec), `<span class="cellbox">${esc(x.name)}${hint((d.similar || {})[x.name])}</span>`,
        x.unified ? `<span class="tag">${esc(x.unified)}</span>` : '<span class="muted">как есть</span>']);
    const ops = [];
    Object.entries(d.operations || {}).forEach(([sec, names]) => names.forEach((n) => ops.push({ sec, name: n, unified: ((d.ops || {})[sec] || {})[n] || '' })));
    ops.sort((a, b) => (b.unified ? 1 : 0) - (a.unified ? 1 : 0) || a.sec.localeCompare(b.sec) || a.name.localeCompare(b.name, 'ru'));
    $('mOpsCount').textContent = `сведено ${num(ops.filter((x) => x.unified).length)} из ${num(ops.length)} названий`;
    renderTable($('mOpsTable'), [{ title: 'Цех' }, { title: 'Операция в цехе' }, { title: 'Единая операция' }], ops,
      (x) => [esc(sections[x.sec] || x.sec), `<span class="cellbox">${esc(x.name)}${hint(((d.similar_ops || {})[x.sec] || {})[x.name])}</span>`,
        x.unified ? `<span class="tag">${esc(x.unified)}</span>` : '<span class="muted">как есть</span>']);
  } catch (e) {
    $('mNote').textContent = 'не удалось получить справочник: ' + e.message;
  }
}

async function uploadGanttMap(file) {
  let token = store.get('fk_plan_token');
  if (!token) {
    token = (window.prompt('Ключ загрузки (PLAN_UPLOAD_TOKEN)') || '').trim();
    if (!token) return;
  }
  const el = $('mResult');
  el.hidden = false; el.className = 'upload-result pending'; el.textContent = `Загружаю ${file.name}…`;
  const form = new FormData();
  form.append('file', file, file.name);
  try {
    const r = await fetch('/api/gantt/map/upload', { method: 'POST', body: form, headers: { 'X-Plan-Token': token } });
    const d = await r.json();
    if (!r.ok || !d.ok) { if (r.status === 401) store.del('fk_plan_token'); throw new Error(d.error || 'ошибка загрузки'); }
    store.set('fk_plan_token', token);
    el.className = 'upload-result ok'; el.textContent = `Загружено соответствий: продуктов ${d.pairs}, операций ${d.op_pairs || 0}.`;
    await loadGanttMap();
    try { applyMeta(await fetchMeta('gantt')); } catch (e) { /* справочники обновятся при следующей загрузке */ }
    loadSummary(false);
  } catch (e) {
    el.className = 'upload-result bad'; el.textContent = 'Не удалось загрузить: ' + e.message;
  }
}

function showGanttDetail(idx) {
  const b = gBarsFlat[idx];
  if (!b) return;
  const el = $('gDetail');
  el.hidden = false;
  el.innerHTML = `<b>${esc(b.section_title)} · ${esc(b.unified || b.product)} — ${esc(b.operation)}</b><br>` + esc(gBarTip(b)).split(' · ').slice(1).join('<br>');
}

function bindGantt() {
  const reload = () => { loadSummary(false); };
  // Пустое значение (стёрли дату в календаре) не применяем — возвращаем прежнее.
  const pickDate = (id, key) => $(id).addEventListener('change', () => {
    if (!$(id).value) { $(id).value = GANTT[key]; return; }
    GANTT[key] = $(id).value;
    reload();
  });
  pickDate('gDate', 'date');
  $('gWindow').addEventListener('change', () => { GANTT.window = $('gWindow').value; reload(); });
  $('gProduct').addEventListener('change', () => { GANTT.product = $('gProduct').value; reload(); });
  $('gEquipment').addEventListener('change', () => { GANTT.equipment = $('gEquipment').value; reload(); });
  pickDate('gAvgFrom', 'avgFrom');
  pickDate('gAvgTo', 'avgTo');
  $('gSearch').addEventListener('input', () => {
    GANTT.search = $('gSearch').value;
    GANTT.searchFold.clear();
    drawGantt(); renderGanttTable();
    if (ganttData) renderAverages(ganttData);
  });
  const step = (dir) => {
    // ближайший день с данными до/после выбранного (выбранный в календаре мог быть и без данных)
    const days = GANTT.days || [];
    const next = dir > 0 ? days.find((d) => d > GANTT.date) : days.slice().reverse().find((d) => d < GANTT.date);
    if (next) { GANTT.date = next; $('gDate').value = next; reload(); }
  };
  $('gPrev').addEventListener('click', () => step(-1));
  $('gNext').addEventListener('click', () => step(1));
  const foldAll = (open) => {
    (ganttData && ganttData.products || []).forEach((p) => gFoldMap().set(gProductKey(p.section, p.product), open));
    drawGantt();
  };
  $('gExpand').addEventListener('click', () => foldAll(true));
  $('gCollapse').addEventListener('click', () => foldAll(false));
  $('aList').addEventListener('toggle', (e) => {
    const item = e.target;
    if (!item.matches || !item.matches('details.avg-item') || !ganttData) return;
    const p = ((ganttData.averages || {}).products || [])[Number(item.dataset.aidx)];
    if (!p) return;
    if (item.open) {
      GANTT.avgOpen.add(p.product);
      const body = item.querySelector('.avg-body');
      if (!body.innerHTML) body.innerHTML = averageBody(p);
    } else {
      GANTT.avgOpen.delete(p.product);
    }
  }, true);
  document.addEventListener('click', (e) => {
    const sec = e.target.closest ? e.target.closest('[data-gsec]') : null;
    if (sec) {
      const k = sec.dataset.gsec;
      GANTT.sections = GANTT.sections.includes(k) ? GANTT.sections.filter((x) => x !== k) : GANTT.sections.concat(k);
      if (!GANTT.sections.length) GANTT.sections = [k];
      fillGanttTools(meta);
      reload();
      return;
    }
    const prod = e.target.closest ? e.target.closest('[data-gprod]') : null;
    if (prod && prod.closest('#gChart')) {
      const k = prod.dataset.gprod;
      gFoldMap().set(k, prod.getAttribute('aria-expanded') !== 'true');
      drawGantt();
      const again = Array.from($('gChart').querySelectorAll('[data-gprod]')).find((x) => x.dataset.gprod === k);
      if (again) again.focus({ preventScroll: true });
      return;
    }
    const bar = e.target.closest ? e.target.closest('[data-gbar]') : null;
    if (bar && bar.closest('#gChart')) showGanttDetail(Number(bar.dataset.gbar));
  });
  $('mTemplate').addEventListener('click', () => download('/api/gantt/map/template'));
  $('mUpload').addEventListener('click', () => $('mFile').click());
  $('mFile').addEventListener('change', (e) => { const f = e.target.files && e.target.files[0]; if (f) uploadGanttMap(f); e.target.value = ''; });
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

/* ─── нажатия ─────────────────────────────────────────────────────────────── */

function actionTarget(node) {
  // closest() на SVG-элементах есть не везде — поднимаемся вручную по parentNode.
  let el = node;
  while (el && el !== document) {
    if (el.dataset && (el.dataset.filter || el.dataset.clear)) return el;
    el = el.parentNode || (el.correspondingUseElement && el.correspondingUseElement.parentNode);
  }
  return null;
}

function runAction(el) {
  hideTip();
  if (el.dataset.clear) clearFilter(el.dataset.clear);
  else if (el.dataset.filter) setFilter(el.dataset.filter, el.dataset.value);
}

function bindTaps() {
  // pointerup вместо click: в мобильных браузерах click с не-интерактивных
  // элементов (SVG, tr) до document доходит не всегда, pointerup — всегда.
  let start = null;
  document.addEventListener('pointerdown', (e) => {
    start = { x: e.clientX, y: e.clientY, el: actionTarget(e.target) };
  }, true);
  document.addEventListener('pointerup', (e) => {
    const el = actionTarget(e.target);
    if (!el || !start || start.el !== el) { start = null; return; }
    const moved = Math.hypot(e.clientX - start.x, e.clientY - start.y);
    start = null;
    if (moved > 12) return;             // это был скролл, а не нажатие
    e.preventDefault();
    runAction(el);
  }, true);
  document.addEventListener('pointercancel', () => { start = null; }, true);
  // Клавиатура и мышь там, где pointer-события не поддерживаются.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = actionTarget(e.target);
    if (el) { e.preventDefault(); runAction(el); }
  });
  if (!window.PointerEvent) {
    document.addEventListener('click', (e) => {
      const el = actionTarget(e.target);
      if (el) runAction(el);
    });
  }
}

/* ─── старт ───────────────────────────────────────────────────────────────── */

function fillSelect(el, values, all) {
  el.innerHTML = `<option value="all">${all}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}

function bindEvents() {
  bindTaps();

  document.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return;      // на тапе тултип только мешает
    const mark = e.target.closest ? e.target.closest('[data-tip]') : null;
    if (mark) moveTip(e, mark.dataset.tip); else hideTip();
  });
  window.addEventListener('blur', hideTip);
  window.addEventListener('scroll', hideTip, { passive: true });

  $('apply').addEventListener('click', () => { readControls(); loadSummary(false); });
  $('reset').addEventListener('click', () => { resetFilters(); syncControls(); loadSummary(false); });
  $('refresh').addEventListener('click', () => loadSummary(true));
  ['dateFrom', 'dateTo', 'equipment', 'product', 'employee', 'shift', 'hour', 'oven', 'status', 'operation'].forEach((id) =>
    $(id).addEventListener('change', () => { readControls(); loadSummary(false); }));

  $('exportPdf').addEventListener('click', () => download('/api/export/pdf?' + query(false)));
  document.querySelectorAll('.src-btn').forEach((b) =>
    b.addEventListener('click', () => switchSource(b.dataset.source)));
  $('exportAll').addEventListener('click', () => exportWidget('all'));
  bindProofing();
  bindKc();
  bindGantt();
  initDropzone();

  let width = window.innerWidth;
  window.addEventListener('resize', () => {
    // Перерисовываем только при смене режима — иначе лишние ререндеры на скролле мобилки.
    const narrowNow = window.innerWidth < 640;
    if (narrowNow !== (width < 640) && lastData) render(lastData);
    else fitValues(document);
    width = window.innerWidth;
  });
}

async function init() {
  // По умолчанию — ФК; БХМ, только если его выбирали раньше.
  const saved = store.get('fk_source');
  SOURCE = SOURCES[saved] ? saved : 'fk';
  let notice = '';
  let m = null;
  let proofFail = '';
  try {
    m = await fetchMeta(SOURCE);
  } catch (e) {
    if (hasOwnPage()) {
      // Расстойка или КЦ недоступны — ошибка на их странице, остальные разделы доступны кнопками.
      proofFail = e.message;
      try { const fk = await fetchMeta('fk'); m = { ...fk, stub: true, equipment: [], products: [], employees: [],
        ovens: [], statuses: [], operations: [], date_min: '', date_max: '', plan_enabled: false,
        units: { qty: 'шт', rate: isKc() ? 'шт/чел·ч' : 'шт/ч' } }; } catch (e2) { /* ничего */ }
    } else if (SOURCE !== 'fk') {
      // Сохранённый БХМ сейчас недоступен — открываем ФК, а не пустую страницу.
      notice = `Источник ${SOURCES[SOURCE].title} недоступен (${e.message}) — показан формовочный цех.`;
      SOURCE = 'fk';
      try { m = await fetchMeta('fk'); } catch (e2) { showAlert('Не удалось получить справочники: ' + e2.message); }
    } else {
      showAlert('Не удалось получить справочники: ' + e.message);
    }
  }
  applyMeta(m || { equipment: [], products: [], employees: [], refresh_seconds: 60 });
  markSourceButtons(meta.sources);
  $('hour').innerHTML = '<option value="">Все</option>' +
    Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, '0')}:00</option>`).join('');
  if (!isNarrow()) $('filterPanel').open = true;
  $('planDate').value = (SOURCE === 'fk' && meta.date_max) || new Date().toISOString().slice(0, 10);

  resetFilters();
  syncControls();
  bindEvents();
  initTabs();
  applySourceUi();
  await refreshPlanState();
  if (proofFail) showSectionError(proofFail);
  else await loadSummary(false);
  if (notice) showAlert(notice);

  const every = Math.max(15, Number(meta.refresh_seconds) || 60) * 1000;
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) loadSummary(false); }, every);
}

init();
