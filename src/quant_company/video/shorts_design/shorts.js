/* Shorts frame: every frame is drawn from EP by a deterministic seek(t), like the episode template. */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const eo = p => 1 - Math.pow(1 - p, 4);
const back = p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
const prog = (lt, s, d) => clamp((lt - s) / d);
const ARROW = { up: '▲', dn: '▼' };
// Numbers in a line are drawn gold, like the episode captions.
const gold = s => String(s).replace(/(\d[\d,.]*\s?(?:%|%p|bp|조원|억원|억달러|달러|원|포인트|배)?)/g, '<em>$1</em>');

const CARDS = {
  hook(d) {
    const dir = ARROW[d.stat_dir] ? `<span class="${d.stat_dir}">${ARROW[d.stat_dir]} </span>` : '';
    // On screen from the first frame: the feed decides within two seconds.
    return `<div class="q">${gold(d.question)}</div>`
      + (d.stat ? `<div class="stat ${d.stat_dir || ''}" data-fit data-in="0.35,0.4,40">${dir}${d.stat}</div>` : '');
  },
  point(d, sc) {
    const dir = ARROW[d.dir] ? `${ARROW[d.dir]} ` : '';
    return `<div class="lab" data-in="0,0.3,30"><span class="badge">${sc.n}</span>${d.label}</div>
      <div class="big ${d.dir || ''}" data-fit data-pop="0.15,0.4">${dir}${d.big}</div>
      <div class="t" data-in="0.45,0.4,30">${gold(d.text)}</div>`;
  },
  outro(d) {
    return `<div class="out1" data-in="0,0.35,30">${d.lead}</div><div class="out2" data-pop="0.25,0.4">${d.main}</div>
      <div class="play" data-pop="0.5,0.4"><svg width="80" height="80" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg></div>`;
  },
};

function fit() {
  for (const scn of $$('.scene')) {
    const shown = scn.style.display; scn.style.display = 'flex';
    for (const e of $$('[data-fit]', scn)) {
      let size = parseFloat(getComputedStyle(e).fontSize); const floor = size * 0.4;
      while (e.scrollWidth > e.clientWidth + 1 && size > floor) { size *= 0.94; e.style.fontSize = size.toFixed(1) + 'px'; }
    }
    scn.style.display = shown;
  }
}

function boot() {
  for (const w of [600, 700, 800]) document.fonts.load(`${w} 40px Pretendard`);
  $('#label').textContent = EP.label;
  $('#segs').innerHTML = EP.scenes.map(() => '<div><i></i></div>').join('');
  let n = 0;
  for (const sc of EP.scenes) {
    if (sc.type === 'point') sc.n = ++n;
    const d = document.createElement('div');
    d.className = 'scene'; d.dataset.t0 = sc.t0; d.dataset.t1 = sc.t1; d.dataset.id = sc.id;
    d.innerHTML = CARDS[sc.type](sc.data, sc);
    $('#stage').appendChild(d);
  }
}

let fitted = false;
function seek(t) {
  if (!fitted) { fitted = true; fit(); }
  const bars = $$('#segs i');
  $$('.scene').forEach((scn, i) => {
    const t0 = +scn.dataset.t0, t1 = +scn.dataset.t1, vis = t >= t0 && t < t1;
    bars[i].style.width = (clamp((t - t0) / (t1 - t0)) * 100) + '%';
    scn.style.display = vis ? 'flex' : 'none';
    if (!vis) return;
    const lt = t - t0;
    for (const e of $$('[data-in]', scn)) {
      const [s, d = 0.4, dy = 30] = e.dataset.in.split(',').map(Number); const p = eo(prog(lt, s, d));
      e.style.opacity = p; e.style.transform = `translateY(${(1 - p) * dy}px)`;
    }
    for (const e of $$('[data-pop]', scn)) {
      const [s, d = 0.4] = e.dataset.pop.split(',').map(Number); const p = prog(lt, s, d);
      e.style.opacity = p > 0 ? Math.min(1, p * 3) : 0; e.style.transform = `scale(${p > 0 ? back(p) : 0.001})`;
    }
  });
  const cb = $('#capbox'), c = EP.captions.find(c => t >= c[0] && t < c[1]);
  if (c) { if (cb.dataset.k !== String(c[0])) { cb.innerHTML = c[2]; cb.dataset.k = String(c[0]); } cb.style.opacity = Math.min(1, (t - c[0]) / 0.12, (c[1] - t) / 0.12); }
  else cb.style.opacity = 0;
}
window.seek = seek;
