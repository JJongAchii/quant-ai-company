/* Shorts frame: every frame is drawn from EP by a deterministic seek(t), like the episode template. */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const eo = p => 1 - Math.pow(1 - p, 4);
const back = p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
const prog = (lt, s, d) => clamp((lt - s) / d);
const ARROW = { up: '▲', dn: '▼' };
const KINDS = ['다르게 볼 점', '의미', '확인할 점'];
// Each picture zooms toward its own point, so consecutive scenes do not move alike.
const ORIGINS = ['50% 30%', '32% 50%', '68% 45%', '45% 68%', '60% 35%', '38% 40%'];
const FADE = 0.35;
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// Numbers in a line are drawn gold, like the episode captions.
const gold = s => esc(s).replace(/(\d(?:[\d,.]*\d)?\s?(?:%p|%|bp|조원|억원|억달러|달러|원|포인트|배)?)/g, '<em>$1</em>');
// The arrow comes from the direction field only: an arrow the script wrote is never drawn, and a +/- sign is dropped
// only where the arrow replaces it (a level of -0.2% keeps its minus).
const bare = (s, dir) => String(s).replace(dir ? /^\s*[▲▼+\-]\s*/ : /^\s*[▲▼]\s*/, '');
const PLAY = '<svg viewBox="0 0 24 24" fill="#fff"><path d="M6 3.5v17l14.5-8.5z"/></svg>';

const CARDS = {
  opener(d) {
    // On screen from the first frame: the feed decides within two seconds.
    return `<div class="inner"><div class="kicker">${esc(d.kicker)}</div><div class="h1">${gold(d.headline)}</div>
      <div class="ask" data-in="0.35,0.45,24">${esc(d.question)}</div></div>`;
  },
  agenda(d, sc) {
    // The rows come in one by one over the first ~60% of the scene.
    const n = d.rows.length, span = Math.max(0.6 * (sc.t1 - sc.t0) - 0.15, 0.3);
    return `<div class="inner"><div class="kicker" data-in="0,0.3,20">${esc(d.kicker)}</div><div class="list">`
      + d.rows.map((r, i) => `<div class="row" data-in="${(0.15 + i * span / n).toFixed(2)},0.4,0,70"><b>${i + 1}</b>`
        + `<div><strong data-fit>${esc(r.label)}</strong><small>${esc(r.agenda)}</small></div></div>`).join('')
      + '</div></div>';
  },
  issue(d) {
    const dir = ARROW[d.dir] || '';
    return `<div class="inner"><div class="pill" data-in="0,0.3,0,-50" data-fit><b>${d.k}</b>${esc(d.label)}<i>${d.k}/${d.n}</i></div>
      <div class="num"><span class="v ${d.dir || ''}" data-pop="0.15,0.45" data-fit>${dir}${esc(bare(d.big, ARROW[d.dir]))}</span>`
      + `<span class="l" data-in="0.4,0.4,0,-24">${esc(d.big_label)}</span></div>
      <div class="card" data-in="0.5,0.45,40"><p>${gold(d.body)}</p>`
      + `<span class="tag k${Math.max(0, KINDS.indexOf(d.note_kind))}" data-in="0.95,0.4,14"><strong>${esc(d.note_kind)}</strong> · ${esc(d.note)}</span></div></div>`;
  },
  outro(d, sc) {
    // The button comes in as the closing line is spoken (its caption), else halfway through.
    const said = EP.captions.find(c => c[0] >= sc.t0 && c[0] < sc.t1 && c[2].includes('본편'));
    const pop = said ? Math.min(said[0] - sc.t0, sc.t1 - sc.t0 - 1.2) : 0.5 * (sc.t1 - sc.t0);
    return `<div class="inner"><div class="kicker" data-in="0,0.3,20">${esc(d.kicker)}</div><div class="sigs" data-in="0.1,0.4,30">`
      + d.signals.map((s, i) => `<div class="sig" data-in="${(0.3 + i * 0.35).toFixed(2)},0.4,0,50"><b>${'①②③④'[i]}</b><span>${gold(s)}</span></div>`).join('')
      + `</div><div class="play" data-pop="${Math.max(0.4, pop).toFixed(2)},0.45">${PLAY}${esc(d.button)}</div></div>`;
  },
};

function fit() {
  for (const scn of $$('.scene')) {
    const shown = scn.style.display; scn.style.display = 'block';
    const box = $('.box', scn), inner = $('.inner', scn);
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

function fitCaption(cb) {
  let size = 44; cb.style.fontSize = size + 'px';
  while (cb.offsetHeight > 160 && size > 30) { size -= 2; cb.style.fontSize = size + 'px'; }
}

function boot() {
  for (const w of [500, 600, 700, 800]) document.fonts.load(`${w} 40px Pretendard`);
  document.title = EP.label;
  $('#badge').textContent = EP.date_badge;
  $('#segs').innerHTML = EP.scenes.map(() => '<div><i></i></div>').join('');
  EP.scenes.forEach((sc, i) => {
    const bg = document.createElement('div');
    // No picture anywhere: a gradient, never a bare frame (the renderer reports the scene).
    bg.className = 'bg' + (sc.bg ? (sc.blur ? ' blur' : '') : ' nophoto');
    if (sc.bg) bg.innerHTML = `<img src="${esc(sc.bg)}" alt="" style="transform-origin:${ORIGINS[i % ORIGINS.length]}"><div class="shade"></div>`;
    $('#bgs').appendChild(bg);
    const d = document.createElement('div');
    d.className = `scene ${sc.type}` + (sc.bg ? '' : ' nophoto');
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
  const cb = $('#capbox'), c = EP.captions.find(c => t >= c[0] && t < c[1]);
  if (c) {
    if (cb.dataset.k !== String(c[0])) { cb.innerHTML = c[2]; cb.dataset.k = String(c[0]); fitCaption(cb); }
    cb.style.opacity = Math.min(1, (t - c[0]) / 0.12, (c[1] - t) / 0.12);
  } else cb.style.opacity = 0;
}
window.seek = seek;
window.pictures = pictures;
