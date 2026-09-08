#!/usr/bin/env python3
"""Проверка вёрстки: не едет ли текст на разных ширинах экрана.

Ловит четыре вида поломок:
  1. страница скроллится вбок;
  2. текст обрезан — содержимое шире своей рамки при overflow:hidden/nowrap;
  3. значение перенеслось на вторую строку там, где должно быть в одну
     (KPI-плитки: «1 870,4 шт/ч» не должно ломаться на «шт/» и «ч»);
  4. нажимаемый элемент мельче 44px — пальцем не попасть.

Запуск (дашборд должен быть уже поднят):
    python3 dashboard/tests/layout_check.py --url http://127.0.0.1:8090
Код возврата 1, если хоть одна проверка провалилась.
"""
import argparse
import asyncio
import sys

WIDTHS = [(360, "малый телефон"), (390, "iPhone"), (414, "большой телефон"),
          (768, "планшет"), (1024, "ноутбук"), (1440, "десктоп"), (1920, "широкий")]
TABS = ["dashboard", "plan", "history"]

# Поломка вёрстки часто живёт МЕЖДУ типовыми разрешениями: на 390px шрифт уже
# ужат медиазапросом, на 1440px места вдоволь, а на 820px значение не влезает.
# Поэтому вдобавок к точкам выше прочёсываем весь диапазон с мелким шагом.
SWEEP_FROM, SWEEP_TO, SWEEP_STEP = 320, 1920, 40

# Один и тот же текст в узкой колонке переносится законно; здесь только то,
# что обязано жить в одну строку.
# Подписи и заголовки переноситься могут — это нормальная вёрстка.
# В одну строку обязаны укладываться только числовые значения и ярлыки вкладок.
SINGLE_LINE = ".k-value, .tab, .dc-date, .dc-qty"

PROBE = """
() => {
  const out = {overflow: [], clipped: [], wrapped: [], small: []};
  const doc = document.documentElement;
  if (doc.scrollWidth > window.innerWidth + 1)
    out.overflow.push({what: 'page', scroll: doc.scrollWidth, view: window.innerWidth});

  // Намеренно скрытый элемент (clip-техника для заголовков таблиц в карточном
  // режиме) не является поломкой вёрстки — его никто не видит.
  const hiddenOnPurpose = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const st = getComputedStyle(n);
      if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return true;
      const clip = st.clip || '';
      if (clip && clip !== 'auto' && /rect\(\s*0/.test(clip.replace(/px/g, ''))) return true;
      if (st.clipPath && st.clipPath.includes('inset(50%)')) return true;
      const r = n.getBoundingClientRect();
      if (r.width <= 1 || r.height <= 1) return true;
    }
    return false;
  };
  const seen = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && !hiddenOnPurpose(el);
  };
  const name = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = (el.className && el.className.baseVal !== undefined)
      ? '' : (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '');
    return el.tagName.toLowerCase() + id + cls;
  };

  document.querySelectorAll('.wrap *').forEach((el) => {
    if (!seen(el)) return;
    const st = getComputedStyle(el);
    const hidesOverflow = st.overflowX === 'hidden' || st.overflowX === 'clip';
    const nowrap = st.whiteSpace === 'nowrap' || st.whiteSpace === 'pre';
    if ((hidesOverflow || nowrap) && el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      out.clipped.push({el: name(el), scroll: el.scrollWidth, client: el.clientWidth,
                        text: (el.textContent || '').trim().slice(0, 48)});
    }
    // Элемент вылезает за правый край своей панели.
    // Внутри контейнера с горизонтальным скроллом широкий элемент — норма:
    // его обрезает и прокручивает сам контейнер (широкие таблицы так и живут).
    let scroller = null;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') { scroller = n; break; }
    }
    const panel = el.closest('.panel, .kpi, .plan-tile, .daycard');
    if (panel && panel !== el && !el.closest('svg') && !scroller) {
      const a = el.getBoundingClientRect(), b = panel.getBoundingClientRect();
      if (a.right > b.right + 2 || a.left < b.left - 2) {
        out.overflow.push({what: name(el), inside: name(panel),
                           dx: Math.round(Math.max(a.right - b.right, b.left - a.left))});
      }
    }
  });

  // Число строк меряем по содержимому через Range: у блочного элемента
  // getClientRects() всегда один прямоугольник, сколько бы строк внутри ни было.
  const lineCount = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const boxes = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
    range.detach && range.detach();
    if (!boxes.length) return 0;
    // Число и единица выровнены по базовой линии, поэтому top у них разный при
    // одной визуальной строке. Считаем строкой группу пересекающихся по вертикали
    // прямоугольников, а не одинаковый top.
    boxes.sort((a, b) => a.top - b.top);
    let lines = 1, bottom = boxes[0].bottom;
    for (const r of boxes.slice(1)) {
      if (r.top >= bottom - 1) { lines += 1; bottom = r.bottom; }
      else bottom = Math.max(bottom, r.bottom);
    }
    return lines;
  };
  document.querySelectorAll(SINGLE_LINE_SELECTOR).forEach((el) => {
    if (!seen(el)) return;
    const lines = lineCount(el);
    if (lines > 1) {
      out.wrapped.push({el: name(el), lines: lines,
                        text: (el.textContent || '').trim().slice(0, 48)});
    }
  });

  // 44px — норматив для пальца; на десктопе целятся мышью, там хватает 32px.
  const minTap = window.innerWidth < 768 ? 44 : 32;
  document.querySelectorAll('button, .daycard, .chip, .fchip, .tab, .export-btn, input, select')
    .forEach((el) => {
      if (!seen(el)) return;
      const r = el.getBoundingClientRect();
      if (r.height < minTap - 0.5)
        out.small.push({el: name(el), h: Math.round(r.height), w: Math.round(r.width), need: minTap});
    });
  return out;
}
"""


async def run(url, chrome):
    from playwright.async_api import async_playwright

    problems = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chrome or None, args=["--no-sandbox"])
        for width, label in WIDTHS:
            page = await browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_timeout(1400)

            for tab in TABS:
                await page.evaluate("(t) => document.querySelector(`.tab[data-tab='${t}']`).click()", tab)
                await page.wait_for_timeout(700)
                probe = PROBE.replace("SINGLE_LINE_SELECTOR", repr(SINGLE_LINE))
                found = await page.evaluate(probe)
                head = f"{width:>5}px {label:<16} вкладка {tab:<10}"
                issues = sum(len(v) for v in found.values())
                if not issues:
                    print(f"  ✅ {head} чисто")
                    continue
                problems += issues
                print(f"  ❌ {head} проблем: {issues}")
                for kind, title in (("overflow", "вылезает"), ("clipped", "текст обрезан"),
                                    ("wrapped", "перенос строки"), ("small", "мелкая цель")):
                    for item in found[kind][:6]:
                        print(f"       [{title}] {item}")
            if errors:
                problems += len(errors)
                print(f"  ❌ {width}px JS-ошибки: {errors[:3]}")
            await page.close()

        # Сплошной прогон по ширинам: ищем только переносы значений и боковой скролл.
        print("\n  Сплошной прогон по ширинам "
              f"{SWEEP_FROM}–{SWEEP_TO}px с шагом {SWEEP_STEP}px:")
        page = await browser.new_page(viewport={"width": SWEEP_TO, "height": 900})
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        bad_widths = []
        for width in range(SWEEP_FROM, SWEEP_TO + 1, SWEEP_STEP):
            await page.set_viewport_size({"width": width, "height": 900})
            await page.wait_for_timeout(180)
            probe = PROBE.replace("SINGLE_LINE_SELECTOR", repr(SINGLE_LINE))
            found = await page.evaluate(probe)
            hits = found["wrapped"] + [o for o in found["overflow"] if o.get("what") == "page"]
            if hits:
                bad_widths.append(width)
                problems += len(hits)
                for item in hits[:3]:
                    print(f"       ❌ {width}px: {item}")
        if not bad_widths:
            print(f"       ✅ все {len(range(SWEEP_FROM, SWEEP_TO + 1, SWEEP_STEP))} ширин чисты")
        await page.close()
        await browser.close()
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8090")
    ap.add_argument("--chrome", default="", help="путь к бинарю Chromium, если Playwright его не находит")
    args = ap.parse_args()
    print(f"Проверка вёрстки: {args.url}\n")
    problems = asyncio.run(run(args.url, args.chrome))
    print()
    if problems:
        print(f"ИТОГ: найдено проблем — {problems}")
        sys.exit(1)
    print("ИТОГ: вёрстка чистая на всех ширинах и вкладках")


if __name__ == "__main__":
    main()
