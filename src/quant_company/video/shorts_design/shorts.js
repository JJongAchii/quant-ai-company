/* Shorts frame: every frame is drawn from EP by a deterministic seek(t), like the episode template. */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const eo = p => 1 - Math.pow(1 - p, 4);
const back = p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
const prog = (lt, s, d) => clamp((lt - s) / d);
const ARROW = { up: '▲', dn: '▼' };
// Each picture zooms toward its own point, so consecutive scenes do not move alike.
const ORIGINS = ['50% 30%', '32% 50%', '68% 45%', '45% 68%', '60% 35%', '38% 40%'];
const FADE = 0.35;
const QBAR = ['agenda', 'issue'];
// A name joined by a middle dot ('엔비디아·AMD', '운송·보험') never breaks at the dot (10/28: '…엔비디아 / ·AMD').
const nobreak = s => String(s ?? '').replace(/·/g, '\u2060·\u2060');
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// A short run of Latin words stays on one line ('LIG D&A', 'S&P 500'); a longer one keeps its breaks to fit the width.
const tie = s => String(s ?? '').replace(/[A-Za-z][\w&.]*(?: [A-Za-z\d][\w&.]*)+/g, r => r.length > 16 ? r : r.replace(/ /g, '\u00a0'));
// Numbers in a line are drawn gold, like the episode captions.
const gold = s => esc(nobreak(tie(s))).replace(/(\d(?:[\d,.]*\d)?\s?(?:%p|%|bp|조원|억원|억달러|달러|원|포인트|배)?)/g, '<em>$1</em>');
// The arrow comes from the direction field only: an arrow the script wrote is never drawn, and a +/- sign is dropped
// only where the arrow replaces it (a level of -0.2% keeps its minus).
const bare = (s, dir) => String(s).replace(dir ? /^\s*[▲▼+\-]\s*/ : /^\s*[▲▼]\s*/, '');
const PLAY = '<svg viewBox="0 0 24 24" fill="#fff"><path d="M6 3.5v17l14.5-8.5z"/></svg>';
const MAIL = '<svg viewBox="0 0 24 24" fill="none" stroke="#FDC749" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.2"/><path d="M3.6 6.6 12 13l8.4-6.4"/></svg>';
const DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v15M5.5 12.5 12 19l6.5-6.5"/></svg>';
// A headline with a comma breaks after its first one, where its first phrase ends ('나스닥 1.11% 반등, / AI 칩 살 돈엔
// 금리 10% 이상', not '… 반등, AI 칩 / 살 돈엔 …'); fit() keeps the break only where both parts fit a line each.
const headline = s => {
  const m = /, |，/.exec(s), rest = m ? s.slice(m.index + m[0].length).trim() : '';
  return rest ? `<span class="part">${gold(s.slice(0, m.index + 1))}</span><br class="comma"><span class="part">${gold(rest)}</span>` : gold(s);
};

const CARDS = {
  opener(d) {
    // On screen from the first frame: the feed decides within two seconds.
    return `<div class="inner"><div class="kicker">${esc(d.kicker)}</div><div class="h1" data-text="${esc(d.headline)}">${headline(d.headline)}</div>
      <div class="ask" data-in="0.35,0.45,24">${esc(d.question)}</div></div>`;
  },
  agenda(d) {
    // The list says only its count (2026-10-27): the rows come in one by one, every 0.3 s, and stay to be glanced at.
    return `<div class="inner"><div class="kicker" data-in="0,0.3,20">${esc(d.kicker)}</div><div class="list">`
      + d.rows.map((r, i) => `<div class="row" data-in="${(0.15 + 0.3 * i).toFixed(2)},0.4,0,70"><b>${i + 1}</b>`
        + `<div><strong data-fit>${esc(r.label)}</strong><small>${esc(r.agenda)}</small></div></div>`).join('')
      + '</div></div>';
  },
  issue(d) {
    // The issue's one-line takeaway (its line in the list) leads; the number backs it up (owner, 10/21). A core issue's
    // card ends with what it means (그래서, 2026-10-27); a brief one is the number and one line.
    const dir = ARROW[d.dir] || '', brief = d.tier !== 'core';
    const num = d.big ? `<div class="num"><span class="v ${d.dir || ''}" data-pop="0.35,0.45" data-fit>${dir}${esc(bare(d.big, ARROW[d.dir]))}</span>`
      + `<span class="l" data-in="0.55,0.4,0,-24">${esc(d.big_label)}</span></div>` : '';
    const tag = !brief && d.meaning ? `<span class="tag k1" data-in="1.1,0.4,14"><strong>그래서</strong> · ${gold(d.meaning)}</span>` : '';
    return `<div class="inner"><div class="pill" data-in="0,0.3,0,-50" data-fit><b>${d.k}</b>${esc(d.label)}<i>${d.k}/${d.n}</i></div>
      <div class="ih" data-in="0.1,0.4,24" data-text="${esc(d.agenda)}">${headline(d.agenda)}</div>${num}
      <div class="card${brief ? ' brief' : ''}" data-in="0.7,0.45,40"><p>${gold(d.body)}</p>${tag}</div></div>`;
  },
  outro(d, sc) {
    // One end screen: the answer to the opener first (2026-10-27), the signals to watch, then a button pointing down to
    // the related video and the report mail line. The button comes in as '본편' is said, else halfway through.
    const said = EP.captions.find(c => c[0] >= sc.t0 && c[0] < sc.t1 && c[2].includes('본편'));
    const pop = said ? Math.min(said[0] - sc.t0, sc.t1 - sc.t0 - 1.2) : 0.5 * (sc.t1 - sc.t0);
    return `<div class="inner"><div class="kicker" data-in="0,0.3,20">${esc(d.kicker)}</div>`
      + `<div class="oq" data-in="0.15,0.45,20">${gold(d.conclusion)}</div><div class="sigs" data-in="0.6,0.4,30">`
      + d.signals.map((s, i) => `<div class="sig" data-in="${(0.8 + i * 0.35).toFixed(2)},0.4,0,50"><b>${'①②③④'[i]}</b><span>${gold(s)}</span></div>`).join('')
      + `</div>`
      + `<div class="play" data-pop="${Math.max(0.4, pop).toFixed(2)},0.45">${PLAY}${esc(d.button)}<i class="down">${DOWN}</i></div>`
      + (d.mail ? `<div class="mailrow" data-in="${(Math.max(0.4, pop) + 0.8).toFixed(2)},0.4,14">${MAIL}<span>${esc(d.mail)}</span></div>` : '')
      + '</div>';
  },
};

function fit() {
  for (const scn of $$('.scene')) {
    const shown = scn.style.display; scn.style.display = 'block';
    const box = $('.box', scn), inner = $('.inner', scn);
    // A headline (the opener's, an issue's) broken at its comma fits each part on one line at the headline size, or at most 15% smaller (the
    // owner's 10/20 headline needs 13%). A longer second part wraps below the break when the first part fills half a
    // line ('다우 1.02% 올랐지만, / 반대매매에 / LIG D&A 제재까지', 10/21); after a short one it wraps as written.
    for (const h of $$('.h1, .ih', scn)) {
      const parts = $$('.part', h);
      if (!parts.length) continue;
      parts.forEach(p => { p.style.whiteSpace = 'nowrap'; });
      const widths = parts.map(p => p.getBoundingClientRect().width), line = inner.clientWidth;
      parts.forEach(p => { p.style.whiteSpace = ''; });
      const scale = line / Math.max(...widths), em = parseFloat(getComputedStyle(h).fontSize) / parseFloat(getComputedStyle(box).fontSize);
      const size = s => { h.style.fontSize = (em * s * 0.99).toFixed(3) + 'em'; };
      if (scale >= 0.85) { if (scale < 1) size(scale); }
      else if (widths[0] < line / 2) h.innerHTML = gold(h.dataset.text);
      else if (widths[0] > line) size(Math.max(line / widths[0], 0.85));
    }
    // The whole text shrinks until it fits between the header and the captions.
    let base = 10;
    while (inner.offsetHeight > box.clientHeight && base > 6) { base -= 0.25; box.style.fontSize = base + 'px'; }
    // A one-line number or title shrinks on its own until it fits the width.
    for (const e of $$('[data-fit]', scn)) {
      let size = parseFloat(getComputedStyle(e).fontSize); const floor = size * 0.55;
      while (e.scrollWidth > e.clientWidth + 1 && size > floor) { size *= 0.95; e.style.fontSize = size.toFixed(1) + 'px'; }
    }
    scn.style.display = shown;
  }
}

// The bar always shows the whole question (a clipped one would still measure full width in the layout check): smaller
// type first, then without its label, beside a credit (width 520) or alone (870).
function fitBar(q, width) {
  if (q.dataset.width === String(width)) return;
  q.dataset.width = String(width); q.style.maxWidth = width + 'px';
  const label = $('b', q); if (label) label.style.display = '';
  for (let size = 30; size >= 20; size--) {
    q.style.fontSize = size + 'px';
    if (q.scrollWidth <= q.clientWidth) return;
    if (size === 24 && label && label.style.display !== 'none') { label.style.display = 'none'; size = 31; }
  }
}

function fitCaption(cb) {
  let size = 44; cb.style.fontSize = size + 'px';
  while (cb.offsetHeight > 160 && size > 30) { size -= 2; cb.style.fontSize = size + 'px'; }
}

function boot() {
  for (const w of [500, 600, 700, 800]) document.fonts.load(`${w} 40px Pretendard`);
  document.title = EP.label;
  $('#badge').textContent = EP.date_badge;
  // The cut's question stays up over the list and the issues (owner, 10/22), so the reason to keep watching shows.
  if (EP.question) $('#qbar').innerHTML = `<b>오늘의 질문</b>${esc(EP.question)}`;
  $('#segs').innerHTML = EP.scenes.map(() => '<div><i></i></div>').join('');
  EP.scenes.forEach((sc, i) => {
    const bg = document.createElement('div');
    // No picture anywhere: a gradient, never a bare frame (the renderer reports the scene).
    bg.className = 'bg' + (sc.bg ? (sc.blur ? ' blur' : '') : ' nophoto');
    if (sc.bg) bg.innerHTML = `<img src="${esc(sc.bg)}" alt="" style="transform-origin:${ORIGINS[i % ORIGINS.length]}"><div class="shade"></div>`;
    $('#bgs').appendChild(bg);
    const d = document.createElement('div');
    d.className = `scene ${sc.type}` + (sc.bg ? '' : ' nophoto') + (EP.question && QBAR.includes(sc.type) ? ' withq' : '');
    d.dataset.id = sc.id; d.dataset.t0 = sc.t0; d.dataset.t1 = sc.t1;
    d.innerHTML = `<div class="box">${CARDS[sc.type](sc.data, sc)}</div>` + (sc.credit ? `<div class="credit">${esc(sc.credit)}</div>` : '');
    $('#stage').appendChild(d);
  });
}

// Waits for every picture; one that cannot be shown falls back to the gradient and is reported.
async function pictures() {
  const broken = [];
  await Promise.all($$('.bg img').map(img => img.decode().catch(() => {
    const bg = img.closest('.bg'); broken.push(img.getAttribute('src')); bg.className = 'bg nophoto'; bg.innerHTML = '';
  })));
  return broken;
}

let fitted = false;
function seek(t) {
  if (!fitted) { fitted = true; fit(); }
  const bars = $$('#segs i'), bgs = $$('.bg'), scenes = $$('.scene'), last = EP.scenes.length - 1;
  let cur = EP.scenes.findIndex(s => t >= s.t0 && t < s.t1);
  if (cur < 0) cur = t < EP.scenes[0].t0 ? 0 : last;
  const fading = cur > 0 && t - EP.scenes[cur].t0 < FADE;
  EP.scenes.forEach((sc, i) => {
    const lt = t - sc.t0, len = sc.t1 - sc.t0;
    bars[i].style.width = (clamp(lt / len) * 100) + '%';
    // The picture: the current one fades in over the previous one, which holds still beneath it; slow zoom across the scene.
    const bg = bgs[i];
    if (i === cur) { bg.style.opacity = cur === 0 ? 1 : eo(clamp(lt / FADE)); bg.style.zIndex = 2; }
    else if (i === cur - 1 && fading) { bg.style.opacity = 1; bg.style.zIndex = 1; }
    else bg.style.opacity = 0;
    const img = $('img', bg);
    if (img && (i === cur || (i === cur - 1 && fading))) img.style.transform = `scale(${(1 + 0.08 * clamp(lt / len)).toFixed(4)})`;
    scenes[i].style.display = i === cur ? 'block' : 'none';
    if (i !== cur) return;
    for (const e of $$('[data-in]', scenes[i])) {
      const [s, d = 0.4, dy = 30, dx = 0] = e.dataset.in.split(',').map(Number); const p = eo(prog(lt, s, d));
      e.style.opacity = p; e.style.transform = `translate(${((1 - p) * dx).toFixed(1)}px,${((1 - p) * dy).toFixed(1)}px)`;
    }
    for (const e of $$('[data-pop]', scenes[i])) {
      const [s, d = 0.4] = e.dataset.pop.split(',').map(Number); const p = prog(lt, s, d);
      e.style.opacity = p > 0 ? Math.min(1, p * 3) : 0; e.style.transform = `scale(${p > 0 ? back(p) : 0.001})`;
    }
  });
  const q = $('#qbar'), shown = EP.question && QBAR.includes(EP.scenes[cur].type);
  // Hidden, it is out of the way of the opener's and the outro's credit; beside a credit it leaves it the right third.
  q.style.visibility = shown ? 'visible' : 'hidden'; fitBar(q, EP.scenes[cur].credit ? 520 : 870);
  q.style.opacity = shown ? Math.min(1, (t - EP.scenes[cur].t0) / FADE + (cur > 0 && QBAR.includes(EP.scenes[cur - 1].type) ? 1 : 0)) : 0;
  const cb = $('#capbox'), c = EP.captions.find(c => t >= c[0] && t < c[1]);
  if (c) {
    if (cb.dataset.k !== String(c[0])) { cb.innerHTML = nobreak(c[2]); cb.dataset.k = String(c[0]); fitCaption(cb); }
    cb.style.opacity = Math.min(1, (t - c[0]) / 0.12, (c[1] - t) / 0.12);
  } else cb.style.opacity = 0;
}
window.seek = seek;
window.pictures = pictures;
