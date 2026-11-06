/* Card catalog (spec §5, template v5 of 2026-11-04). Each function returns the scene's inner HTML from episode data only;
   the data fields are the ones the script writes (episode.py CARD_GUIDE) and did not change with the redesign.
   Stage coordinates: 1728 x 740. The header (kicker + claim headline) takes 0-108, content sits in 140-720.
   Look: one flat navy ground, flat panels, hairline rules, yellow as the only accent, red/blue only for a rise or a fall.
   Pictograms are not drawn: the script's icon fields stay valid and are ignored. */
const POST = [];
const DIR = { up: ['upc', 'upn', '▲'], dn: ['dnc', 'dnn', '▼'], flat: ['', '', ''] };
const arrow = d => (DIR[d] || DIR.flat)[2];
const C = (d, navy) => (DIR[d] || DIR.flat)[navy ? 1 : 0];
const dirArrow = d => d && arrow(d) ? `<span class="${C(d, 1)}">${arrow(d)}</span> ` : '';
const src = s => s ? `<span class="src">${s}</span>` : '';
const DOT = '<i class="kd"></i>';
const TOP = 140, H = 580;
const two = n => String(n).padStart(2, '0');
// A bar's direction is the one its label shows (11/04 PM, 11/05 AM: '▼8.82%' written with a positive n was drawn as a
// red rise); with no arrow or sign in the label, the sign of n decides.
const signed = r => { const l = String(r.label ?? '').trim();
  return (/▼|^[-−]/.test(l) ? -1 : /▲|^\+/.test(l) ? 1 : Math.sign(+r.n || 0) || 1) * Math.abs(+r.n || 0); };

function header(d) {
  const chips = (d.chips || []).map((c, i) => `<span class="${i === 0 && d.gold !== false ? 'k0' : ''}">${c}</span>`);
  if (d.tag) chips.push(`<span class="tg">${d.tag}</span>`);
  const kick = chips.length ? `<div class="kick" data-in="0">${chips.join(DOT)}</div>` : '';
  return `<div class="hdr">${kick}<div class="h1 one" data-fit data-in="0.1" style="margin-top:${kick ? 4 : 0}px">${d.title}</div></div>`;
}

const CARDS = {
  // The day's question, set large; three numbers under one rule.
  cold_open(d) {
    const q = d.question.map((line, i) => `<div class="one" data-fit data-in="${(0.2 + i * 0.2).toFixed(2)}">${line.map(([w, g]) => g ? `<span class="ylw">${w}</span>` : w).join(' ')}</div>`).join('');
    const n = d.stats.length, w = Math.floor(1728 / n);
    const stats = d.stats.map((s, i) => `<div class="cstat" style="left:${i * w}px;width:${w}px;${i ? '' : 'padding-left:0;border-left:0'}" data-in="${(0.9 + i * 0.15).toFixed(2)}">
        <div class="lab">${s.label}</div>
        <div class="num one ${s.big_dir ? C(s.big_dir, 1) : ''}" style="font-size:80px;line-height:1.1;margin-top:14px" data-fit data-fit-group="cold">${s.big_dir ? arrow(s.big_dir) + ' ' : ''}${s.value}</div>
        <div class="uline"></div>
        <div class="${s.sub_dir ? C(s.sub_dir, 1) : 'muted'}" style="font-size:28px;font-weight:700;margin-top:12px;text-wrap:balance">${s.sub}</div></div>`).join('');
    return `<div class="kick" style="position:absolute;left:0;top:0"><span class="k0" data-in="0">${d.kicker}</span></div>
      <div class="display" style="position:absolute;left:0;top:56px;width:1728px">${q}</div>
      <div class="hline" style="top:436px" data-bar="0.7"></div>${stats}`;
  },

  // Three editorial columns: yellow number, two captioned points, the claim title, two stats. The columns share one grid
  // (subgrid rows), so titles and stat rows line up across them (2026-10-16 AM: a two-line label made one card's tiles
  // taller than the others').
  summary3(d) {
    const cards = d.cards.map((c, i) => {
      const b = (0.3 + i * 0.15).toFixed(2);
      return `<div class="paper scard" style="position:relative" data-in="${b}">
       <div class="spot"><div class="num">${two(c.n)}</div>
        ${c.items.map(it => `<div class="spot-item">${it[4] || it[5] ? `<span class="badge ${it[4] || ''}">${it[5] || '·'}</span>` : ''}<span class="cap">${it[3]}</span></div>`).join('')}</div>
       <div class="stitle"><div class="h2">${c.title}</div></div>
       <div class="sstats">${c.stats.map(s => `<div class="stat"><div class="k">${s[0]}</div><div class="v tnum ${s[2] ? C(s[2]) : ''}" data-fit data-fit-group="s${i}">${s[2] && arrow(s[2]) ? arrow(s[2]) + ' ' : ''}${s[1]}</div></div>`).join('')}</div></div>`;
    }).join('');
    return `<div class="h1 one" style="position:absolute;top:0;left:0;right:0" data-fit data-in="0.1">${d.title}</div>
      <div style="position:absolute;left:0;top:110px;width:1728px;height:620px;display:flex;align-items:safe center"><div class="sgrid">${cards}</div></div>`;
  },

  // Index levels and flows as a ruled table; a starred row carries the yellow marker.
  market_board(d) {
    const n = d.tiles.length, rh = Math.min(112, Math.floor((H - 20) / n));
    const rows = d.tiles.map((b, i) => {
      const star = !!(b.badge && b.badge.startsWith('★'));
      return `<div class="trow${star ? ' star' : ''}" style="top:${i * rh}px;height:${rh}px" data-in="${(0.3 + i * 0.08).toFixed(2)}">
        <div class="tn one" data-fit>${b.name}${b.badge ? `<span class="tb${star ? ' on' : ''}">${b.badge}</span>` : ''}</div>
        <div class="tv num one" data-fit>${b.value}</div>
        <div class="tc num ${C(b.dir)}">${arrow(b.dir)} ${b.change}</div>
        <div class="ts one" data-fit>${b.sub || ''}</div></div>`;
    }).join('');
    return header(d) + `<div class="tbl" style="top:${TOP}px;height:${n * rh}px">${rows}</div>`;
  },

  // A news photo with its credit under it; the numbers in a ruled column on the right.
  headline(d) {
    const k = d.stats.length;
    const labels = (d.labels || []).map((l, i) => `<div class="ilabel" style="left:${Math.round(l.x * 1000 / 1060)}px;top:${Math.round(l.y * 560 / 590)}px" data-in="${(0.8 + i * 0.15).toFixed(2)}">${l.text}${l.dir ? ` <span class="${C(l.dir, 1)}">${arrow(l.dir)}</span>` : ''}</div>`).join('');
    const stats = d.stats.map((s, i) => `<div class="hstat" data-in="${(0.5 + i * 0.15).toFixed(2)}">
        <div class="lab">${s.label}</div>
        <div class="row" style="align-items:baseline;gap:18px;margin-top:6px"><span class="num" style="font-size:${k > 2 ? 50 : 60}px">${s.value}</span>${s.change ? `<span class="num ${C(s.dir, 1)}" style="font-size:30px">${arrow(s.dir)} ${s.change}</span>` : ''}</div>
        ${s.sub ? `<div class="note" style="margin-top:6px">${s.sub}</div>` : ''}</div>`).join('');
    return header(d) + `<div class="paper imgbox" style="left:0;top:${TOP}px;width:1000px;height:560px" data-in="0.2"><img src="${d.image.file}" alt="">${labels}${d.image.credit ? `<span class="credit">${d.image.credit}</span>` : ''}</div>
      <div class="glass col" style="left:1048px;top:${TOP}px;width:680px;height:560px" data-in="0.3">
        <div class="row ptitle"><span class="lab strong">${d.panel_title}</span>${src(d.source)}</div>
        <div style="flex:1;display:flex;flex-direction:column;justify-content:${k > 2 ? 'flex-start' : 'space-around'}">${stats}</div></div>`;
  },

  // Three steps with thin arrows, then the takeaway bar.
  flow(d) {
    const nodes = d.nodes.map((n, i) => {
      const b = (0.3 + i * 0.35).toFixed(2);
      const inner = n.big ? `<div class="num" style="font-size:72px;letter-spacing:-.02em;margin-top:18px;line-height:1">${n.big}</div><div class="${C(n.dir)}" style="font-size:26px;font-weight:700;margin-top:14px;line-height:1.4">${n.sub || ''}</div>`
        : n.rows ? n.rows.map((r, k) => `<div class="num" style="display:flex;justify-content:space-between;align-items:baseline;margin-top:${k ? 12 : 22}px;font-size:32px"><span>${r[0]}</span><span class="${C(r[2])}">${arrow(r[2])} ${r[1]}</span></div>`).join('')
        : `<div class="h2" style="margin-top:20px;font-size:36px">${n.text}</div>`;
      const chips = (n.chips || []).map(c => `<span class="${c === '보도 해석' ? 'k0' : ''}">${c}</span>`);
      if (n.source) chips.push(src(n.source));
      return `<div class="paper fnode" style="min-height:${d.h || 330}px" data-in="${b}">
        <div class="kick small">${chips.join(DOT)}</div>
        <div class="row" style="gap:14px;margin-top:16px"><span style="font-size:30px;font-weight:700;line-height:1.3">${n.head}</span>${n.badge ? `<span class="tag">${n.badge}</span>` : ''}</div><div style="margin-top:auto">${inner}</div></div>`;
    }).join('');
    // The three cards share one row and its tallest card's height (10/16 PM: three-line notes ran out of fixed 330px
    // cards). The arrows sit at the row's middle and the foot follows the row.
    const arrows = [534, 1138].map((x, k) => `<svg style="position:absolute;left:${x}px;top:calc(50% - 1px);overflow:visible" width="56" height="2"><defs><marker id="ah_${d.id}_${k}" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11" fill="none" stroke="#FFD43B" stroke-width="2"/></marker></defs>
      <path d="M0,1 L50,1" stroke="#FFD43B" stroke-width="3" fill="none" marker-end="url(#ah_${d.id}_${k})" data-draw="${(0.55 + k * 0.35).toFixed(2)},0.4"/></svg>`).join('');
    const foot = d.foot ? `<div class="glass fill takeaway" style="position:relative;flex:none;min-height:120px" data-in="1.4"><span class="tk">${d.foot.chip}</span><div class="body" style="font-weight:600">${d.foot.text}</div></div>` : '';
    return header(d) + `<div style="position:absolute;left:0;top:${TOP}px;width:1728px;display:flex;flex-direction:column;gap:36px">
      <div style="position:relative;display:flex;gap:84px;align-items:stretch">${nodes}${arrows}</div>${foot}</div>`;
  },

  // The brief's counter-view or evidence: one panel, topic on the left, two or three numbers, up to two notes.
  counter(d) {
    // Blocks share the area right of the divider in equal columns. A script's x is ignored: on 2026-10-13 PM
    // x=590/1150 (screen guesses) pushed '19.6%' and '14%' off screen.
    const span = Math.floor(1170 / d.blocks.length);
    const blocks = d.blocks.map((b, i) => {
      const t = (0.5 + i * 0.2).toFixed(2);
      return `<div style="position:absolute;left:${i * span}px;top:0;width:${span - 40}px" data-block data-in="${t}">
        <div class="lab" style="text-wrap:balance">${b.label}</div>
        <div class="num one" style="font-size:76px;line-height:1;margin-top:16px;${b.gold ? 'color:var(--y)' : ''}" data-fit data-fit-group="blocks">${dirArrow(b.dir)}${b.value}</div>
        <div class="uline${b.gold ? ' y' : ''}"></div>
        ${b.sub ? `<div class="${b.dir ? C(b.dir, 1) : 'muted'}" style="font-size:25px;font-weight:700;margin-top:12px;line-height:1.35;text-wrap:balance">${b.sub}</div>` : ''}</div>`;
    }).join('');
    const rows = (d.rows || []).map((r, i) => `<div class="crow" data-in="${(1.1 + i * 0.15).toFixed(2)}"><i class="pt"></i><div class="body" style="font-weight:600;font-size:28px">${r.text}</div>${src(r.source)}</div>`).join('');
    const ch = 360 + (d.rows || []).length * 82;
    return header(d) + `<div class="glass fill" style="left:0;top:${TOP + Math.max(0, Math.round((H - ch) / 2))}px;width:1728px;height:${ch}px" data-in="0.2">
      <div style="position:absolute;left:40px;top:36px;width:400px"><div class="lab strong ylw" data-kicker>◆ ${d.kicker || '다르게 볼 점'}</div>
       <div style="font-size:30px;font-weight:700;margin-top:20px;line-height:1.35;text-wrap:balance">${d.topic}</div>
       ${d.source ? `<div style="margin-top:16px">${src(d.source)}</div>` : ''}</div>
      <div class="vrule" style="left:470px;top:36px;height:236px"></div>
      <div style="position:absolute;left:510px;top:36px;width:1170px;height:250px">${blocks}</div>
      <div class="hline" style="left:40px;right:40px;width:auto;top:300px"></div>
      <div style="position:absolute;left:40px;right:40px;top:326px">${rows}</div></div>`;
  },

  // What to watch: numbered rows, the conditions joined by '+', then the reading they would change.
  signals(d) {
    // Rows share the stage height below the header; up to three rows keep a 150px pitch, more rows shrink (2026-10-13 PM:
    // the fourth row and the disclaimer ran under the captions).
    const extras = (d.note ? 124 : 0) + (d.disclaimer ? 48 : 0), room = H - extras, n = d.rows.length;
    const pitch = Math.min(150, Math.floor(room / n)), height = pitch - 14;
    const top = TOP + Math.max(0, Math.floor((room - n * pitch) / 2));
    const rows = d.rows.map((r, i) => {
      const b = (0.3 + i * 0.15).toFixed(2);
      return `<div class="glass fill sig" style="top:${top + i * pitch}px;height:${height}px" data-in="${b}">
       <span class="tile">${two(i + 1)}</span>
       <div class="stopic one" data-topic data-fit>${r.topic}</div>
       <div class="sconds">${r.conds.map((c, j) => `${j ? '<span class="plus">+</span>' : ''}<span>${c}</span>`).join('')}</div>
       <svg width="48" height="24" style="flex:none"><path d="M2 12H44M34 3L45 12L34 21" fill="none" stroke="#FFD43B" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>
       <div class="sthen">${r.then}</div></div>`;
    }).join('');
    const after = top + n * pitch;
    const note = d.note ? `<div class="panel takeaway" style="left:0;width:1728px;top:${after + 4}px;height:110px" data-in="${(0.4 + n * 0.15).toFixed(2)}"><div class="body" style="font-weight:600;font-size:28px;line-height:1.4">${d.note}</div></div>` : '';
    const disc = d.disclaimer ? `<div class="note" style="position:absolute;left:0;top:${after + (d.note ? 124 : 0) + 10}px" data-disclaimer data-in="${(0.5 + n * 0.15).toFixed(2)}">${d.disclaimer}</div>` : '';
    return header(d) + rows + note + disc;
  },

  calendar(d) {
    if (d.mode === 'day') {
      const rows = d.rows.map(([, t, s], i) => `<div class="drow" data-in="${(0.5 + i * 0.12).toFixed(2)}">
        <div style="font-size:34px;font-weight:700;letter-spacing:-.01em;line-height:1.3">${t}</div><div class="note" style="margin-top:6px">${s}</div></div>`).join('');
      const side = d.side.map((s, i) => `<div class="hstat" data-in="${(0.8 + i * 0.15).toFixed(2)}">
        <div class="row" style="gap:12px"><span class="lab">${s.label}</span>${src(s.source)}</div>
        <div class="num ${s.dir ? C(s.dir, 1) : 'ylw'}" style="font-size:50px;margin-top:8px">${s.dir ? arrow(s.dir) + ' ' : ''}${s.value}</div>
        <div class="note" style="margin-top:6px">${s.sub}</div></div>`).join('');
      return header(d) + `<div class="paper" style="left:0;top:${TOP}px;width:860px;height:${H}px;overflow:hidden" data-in="0.2">
        <div class="dhead"><div class="num ylw" style="font-size:60px;line-height:1">${d.date}</div><div style="font-size:34px;font-weight:700">${d.dow}</div>${d.badge ? `<span class="tag" style="margin-left:auto">${d.badge}</span>` : ''}</div>
        <div style="padding:8px 40px 0">${rows}</div></div>
       <div class="glass col" style="left:900px;top:${TOP}px;width:828px;height:${H}px" data-in="0.3"><div class="row ptitle"><span class="lab strong">${d.side_title}</span></div><div style="flex:1;display:flex;flex-direction:column;justify-content:space-around">${side}</div></div>`;
    }
    const n = d.rows.length, rh = Math.min(112, Math.floor(H / n)), top = TOP + Math.max(0, Math.floor((H - n * rh) / 2));
    const rows = d.rows.map(([dt, w, bd, , e, s], i) => `<div class="crow2" style="top:${top + i * rh}px;height:${rh}px" data-in="${(0.3 + i * 0.1).toFixed(2)}">
       <div class="num one ylw" data-date data-fit style="font-size:38px">${dt}${w ? `<span class="cw">${w}</span>` : ''}</div>
       <div>${bd ? `<span class="tag">${bd}</span>` : ''}</div>
       <div class="ce">${e}</div>
       <div class="cs">${s}</div></div>`).join('');
    return header(d) + rows + `<div class="hline" style="top:${top + n * rh}px"></div>`;
  },

  compare(d) {
    // The arrow is red or blue only when the script says the measure rose or fell; other comparisons draw it yellow
    // (2026-10-16 AM: a fixed blue arrow made the KOFIX '0.25%p 상승' read as a fall). Bars stop at 162px so the arrow
    // label stays clear of the chart title and its source; the arrow ends above the second value's label, never on it
    // (11/06 AM: its head covered the '6' of '614억6000만').
    const [a, b] = d.bars, BH = 162;
    const ac = d.arrow_dir === 'up' ? '#FF5C66' : d.arrow_dir === 'dn' ? '#5E9BFF' : '#FFD43B';
    const tiles = d.tiles.map((t, i) => `<div class="ctile" data-in="${(0.5 + i * 0.15).toFixed(2)}">
       <div class="row" style="gap:12px"><span class="lab">${t.label}</span>${src(t.source)}</div>
       <div class="num ${t.dir ? C(t.dir) : ''}" style="font-size:48px;margin-top:8px">${t.dir ? arrow(t.dir) + ' ' : ''}${t.value}</div>
       <div class="note" style="margin-top:6px">${t.sub}</div></div>`).join('');
    const [va, vb] = [a, b].map(signed);
    let chart;
    if (va < 0 || vb < 0) {
      // A fall is never drawn as a rising bar (11/03, 11/05 AM: '▼4.13%' stood taller than '▲2.22%' under an upward
      // arrow): with a fall in the pair, both go on one axis, rises to the right in red, falls to the left in blue.
      const max = Math.max(Math.abs(va), Math.abs(vb)) || 1, half = 200, x0 = 225 + half;
      chart = `<div style="position:relative;height:320px;margin-top:48px"><div style="position:absolute;left:${x0}px;top:20px;width:1px;height:220px;background:var(--rule2)"></div>`
        + [[a, va], [b, vb]].map(([r, v], i) => {
          const y = 40 + i * 110, w = Math.max(4, Math.abs(v) / max * half), neg = v < 0;
          return `<div style="position:absolute;left:0;top:${y + 4}px;width:${x0 - half - 18}px;text-align:right;font-size:28px;font-weight:700;line-height:1.2">${r.name}</div>`
            + (v !== 0 ? `<div class="bar" style="top:${y}px;height:64px;${neg ? `left:${x0 - w}px;transform-origin:right` : `left:${x0 + 1}px;transform-origin:left`};width:${w}px;background:${neg ? 'var(--dn)' : 'var(--up)'}" data-bar="${(0.6 + i * 0.3).toFixed(2)}"></div>` : '')
            + `<div class="num ${v === 0 ? 'muted' : neg ? 'dnn' : 'upn'}" style="position:absolute;top:${y + 8}px;left:${neg || v === 0 ? x0 + 18 : x0 + w + 16}px;font-size:40px;white-space:nowrap" data-in="${(1.1 + i * 0.2).toFixed(2)}">${r.label}</div>`;
        }).join('')
        + `<div style="position:absolute;left:0;right:0;top:270px;font-size:26px;font-weight:700;color:${ac}" data-role="arrow-text" data-in="1.8">${d.arrow_text}</div></div>`;
    } else {
      const max = Math.max(va, vb) || 1, ha = BH * va / max, hb = BH * vb / max;
      chart = `<svg width="800" height="320" style="margin-top:48px;overflow:visible">
       <defs><marker id="ahc_${d.id}" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11" fill="none" stroke="${ac}" stroke-width="2.5"/></marker></defs>
       <line x1="60" y1="282" x2="752" y2="282" stroke="#3A4C70" stroke-width="2"/>
       <rect x="151" y="${282 - ha}" width="190" height="${ha}" fill="#8E9AB0" data-vbar="0.6"/>
       <rect x="471" y="${282 - hb}" width="190" height="${hb}" fill="#FFD43B" data-vbar="0.9"/>
       <text x="246" y="${282 - ha - 16}" text-anchor="middle" font-size="40" font-weight="700" fill="#F2F4F8" data-in="1.1">${a.label}</text>
       <text x="566" y="${282 - hb - 16}" text-anchor="middle" font-size="40" font-weight="700" fill="#FFD43B" data-in="1.3">${b.label}</text>
       <text x="246" y="320" text-anchor="middle" font-size="25" font-weight="500" fill="#AEB7C7">${a.name}</text>
       <text x="566" y="320" text-anchor="middle" font-size="25" font-weight="500" fill="#AEB7C7">${b.name}</text>
       <path d="M352,${282 - ha - 72} C392,${282 - ha - 102} 432,${282 - hb - 100} 462,${282 - hb - 72}" fill="none" stroke="${ac}" stroke-width="3" marker-end="url(#ahc_${d.id})" data-role="arrow" data-draw="1.5,0.6"/>
       <text x="407" y="${Math.max(4, Math.min(282 - ha, 282 - hb) - 118)}" text-anchor="middle" font-size="24" font-weight="700" fill="${ac}" data-role="arrow-text" data-in="1.8">${d.arrow_text}</text></svg>`;
    }
    return header(d) + `<div class="paper" style="left:0;top:${TOP}px;width:800px;height:${H}px;padding:20px 36px;display:flex;flex-direction:column;justify-content:center" data-in="0.2">${tiles}</div>
     <div class="glass fill" style="left:840px;top:${TOP}px;width:888px;height:${H}px;padding:30px 44px" data-in="0.3">
      <div class="row" style="gap:12px"><span class="lab strong">${d.chart_title}</span>${src(d.chart_source)}</div>
      ${chart}
      <div style="display:flex;gap:18px;margin-top:22px" data-in="2.0">${d.extra.map(e => `<div data-extra class="xtile"><div class="note">${e[0]}</div><div class="num one" data-fit style="font-size:40px;margin-top:4px">${e[1]}</div></div>`).join('')}</div></div>`;
  },

  // Diverging bars (rise red, fall blue) from one axis; optional photo on the left or a small second chart on the right.
  bars(d) {
    const W = d.image ? 1000 : (d.side ? 1020 : 1728), X = d.image ? 728 : 0, n = d.rows.length;
    const rowsN = d.rows.map(signed), max = Math.max(...rowsN.map(Math.abs)) || 1;
    const nameW = d.name_w || 300, gap = d.gap || Math.min(76, Math.floor(470 / n)), bh = Math.min(44, gap - 22);
    // name column | negative bars | axis | positive bars + value. Negative values sit just right of the axis.
    const half = Math.floor((W - 36 - nameW - 20 - 230) / 2), x0 = 36 + nameW + 20 + half;
    let h = `<div class="lab" style="position:absolute;left:36px;top:28px">${d.unit_note}</div><div style="position:absolute;left:${x0}px;top:80px;width:1px;height:${n * gap + 10}px;background:var(--rule2)"></div>`;
    d.rows.forEach((r, i) => {
      const v = rowsN[i], y = 90 + i * gap, w = Math.max(4, Math.abs(v) / max * half), neg = v < 0;
      h += `<div style="position:absolute;left:36px;top:${r.tag ? y - 6 : y + (bh - 34) / 2}px;width:${nameW}px;font-size:28px;font-weight:700;line-height:1.15">${r.name}${r.tag ? `<div class="note" style="font-size:18px;margin-top:2px">${r.tag}</div>` : ''}</div>`;
      if (v !== 0) h += `<div class="bar" style="top:${y}px;height:${bh}px;${neg ? `left:${x0 - w}px;transform-origin:right` : `left:${x0 + 1}px;transform-origin:left`};width:${w}px;background:${neg ? 'var(--dn)' : 'var(--up)'}" data-bar="${(0.4 + i * 0.06).toFixed(2)}"></div>`;
      h += `<div class="num ${v === 0 ? 'muted' : neg ? 'dnc' : 'upc'}" style="position:absolute;top:${y + (bh - 36) / 2}px;left:${neg || v === 0 ? x0 + 16 : x0 + w + 14}px;font-size:28px;white-space:nowrap" data-in="${(0.8 + i * 0.06).toFixed(2)}">${r.label}</div>`;
    });
    let left = '';
    if (d.image) {
      const labels = (d.image.labels || []).map((l, i) => `<div class="ilabel" style="left:${Math.round(l.x * 688 / 700)}px;top:${Math.round(l.y * 410 / 430)}px" data-in="${(0.8 + i * 0.15).toFixed(2)}">${l.text}${l.dir ? ` <span class="${C(l.dir, 1)}">${arrow(l.dir)}</span>` : ''}</div>`).join('');
      left = `<div class="paper" style="left:0;top:${TOP}px;width:688px;height:${H}px" data-in="0.2">
        <div class="imgbox" style="width:688px;height:410px"><img src="${d.image.file}" alt="">${labels}</div>
        <div style="padding:${d.image.credit ? 44 : 24}px 32px 0;position:relative">${d.image.credit ? `<span class="credit" style="top:8px;bottom:auto;left:32px">${d.image.credit}</span>` : ''}<div class="body" style="font-size:27px;font-weight:600">${d.image.caption}</div></div></div>`;
    }
    let side = '';
    if (d.side) {
      // Rows may come as [name, label] pairs, and without n they are a list of values with no bars (11/04 PM, 11/05 PM:
      // pairs drew 'undefined' on every row).
      const sr = d.side.rows.map(r => Array.isArray(r) ? { name: r[0], label: r[1], n: r[2] } : r);
      const bars = sr.some(r => Number.isFinite(+r.n) && r.n !== null && r.n !== '');
      const sv = sr.map(signed), m = Math.max(...sv.map(Math.abs)) || 1, hw = 150, c2 = 330;
      let g = bars ? `<div style="position:absolute;left:${c2}px;top:84px;width:1px;height:186px;background:var(--rule2)"></div>` : '';
      sr.forEach((r, i) => {
        const y = 96 + i * 58, w = Math.abs(sv[i]) / m * hw, neg = sv[i] < 0;
        g += `<div style="position:absolute;left:38px;top:${y + 2}px;font-size:28px;font-weight:700">${r.name ?? ''}</div>`;
        if (bars) g += `<div class="bar" style="top:${y}px;height:38px;${neg ? `left:${c2 - w}px;transform-origin:right` : `left:${c2 + 1}px;transform-origin:left`};width:${w}px;background:${neg ? 'var(--dn)' : 'var(--up)'}" data-bar="${(0.7 + i * 0.06).toFixed(2)}"></div>`;
        g += `<div class="num ${bars || /[▲▼]/.test(r.label) ? (neg ? 'dnn' : 'upn') : ''}" style="position:absolute;top:${y + 1}px;right:38px;font-size:30px" data-in="${(1.0 + i * 0.06).toFixed(2)}">${r.label ?? ''}</div>`;
      });
      side = `<div class="glass fill" style="left:1060px;top:${TOP}px;width:668px;height:${H}px" data-in="0.3">
        <div class="lab" style="position:absolute;left:38px;top:32px">${d.side.title}</div>${g}
        <div class="hline" style="left:38px;right:38px;width:auto;top:300px"></div>
        <div style="position:absolute;left:38px;right:38px;top:326px" data-in="1.3"><div class="lab strong ylw">확인할 신호</div>
        <div class="body" style="font-size:28px;margin-top:12px">${d.side.signal}</div></div></div>`;
    }
    return header(d) + left + `<div class="paper" style="left:${X}px;top:${TOP}px;width:${W}px;height:${H}px" data-in="0.2">${h}${d.source ? `<div style="position:absolute;right:30px;bottom:24px">${src(d.source)}</div>` : ''}</div>` + side;
  },

  photo(d) {
    const rows = d.rows.map((r, i) => `<div class="prow" data-in="${(0.5 + i * 0.15).toFixed(2)}">
       <div class="row" style="gap:12px"><span class="pchip ${r.kind || ''}">${r.chip}</span>${src(r.source)}</div>
       <div class="tnum" style="font-size:${r.big ? 44 : 30}px;font-weight:700;margin-top:10px;line-height:1.25;text-wrap:balance">${r.big ? dirArrow(r.dir) + r.big : ''}${r.big && r.text ? ' ' : ''}${r.text ? `<span style="font-size:30px">${r.text}</span>` : ''}</div>
       ${r.sub ? `<div class="note" style="margin-top:6px">${r.sub}</div>` : ''}</div>`).join('');
    return header(d) + `<div class="paper imgbox" style="left:0;top:${TOP}px;width:840px;height:560px" data-in="0.2"><img src="${d.image.file}" alt="">${d.image.credit ? `<span class="credit">${d.image.credit}</span>` : ''}</div>
      <div class="glass col" style="left:888px;top:${TOP}px;width:840px;height:${H}px;justify-content:space-between" data-in="0.3">${rows}</div>`;
  },

  map(d) {
    POST.push(() => drawMap(d));
    const bars = d.bars.map((b, i) => `<div class="${b.gold ? 'ylw' : 'muted'}" style="margin-top:${i ? 14 : 20}px;font-size:22px;font-weight:700">${b.name}</div>
      <div style="display:flex;align-items:center;gap:14px;margin-top:6px"><div data-track style="position:relative;flex:none;width:240px;height:40px;background:var(--panel2)"><div class="bar" style="left:0;top:0;height:40px;width:${Math.max(0, Math.min(100, +b.w || 0))}%;background:${b.gold ? 'var(--y)' : 'var(--gray)'};transform-origin:left" data-bar="${(0.7 + i * 0.12).toFixed(2)}"></div></div><span class="num one" data-fit style="flex:1;min-width:0;font-size:28px" data-in="${(1.0 + i * 0.12).toFixed(2)}">${b.label}</span></div>`).join('');
    return header(d) + `<div class="glass" style="left:0;top:${TOP}px;width:1000px;height:${H}px;overflow:hidden;padding:0" data-in="0.2" id="mapcard_${d.id}"></div>
     <div class="glass fill" style="left:1040px;top:${TOP}px;width:688px;height:${H}px;padding:28px 36px;display:flex;flex-direction:column;gap:16px" data-in="0.3">
      <div data-fitbox style="flex:1;min-height:0"><div>
      <div class="lab strong">${d.panel_title}</div>${bars}
      <div class="${d.delta_dir ? C(d.delta_dir, 1) : ''}" data-delta data-fit style="font-size:44px;font-weight:700;margin-top:18px;white-space:nowrap;overflow:hidden" data-in="1.4">${d.delta}</div>
      <div style="margin-top:6px" data-in="1.4">${src(d.delta_source)}</div>
      <div class="note" style="margin-top:10px;line-height:1.45;text-wrap:balance" data-in="1.6">${d.alt}</div></div></div>
      <div class="takeaway" style="position:relative;flex:none;padding:14px 0 14px 22px;border-left-width:4px" data-in="${d.cost_at ? Math.min(d.cost_at, 4) : 2.0}">
       <div style="font-size:22px;font-weight:700;line-height:1.35"><span class="muted">${d.cost_head}</span><div data-cost style="color:var(--y);font-size:${d.cost.length > 24 ? 24 : 30}px;font-weight:700;text-wrap:balance;white-space:pre-line;margin-top:4px">${d.cost}</div></div></div></div>`;
  },

  // The channel's fixed opening and closing (motion.py BRAND), never written by the script. The wordmark underlines
  // its first phrase ('그래서,') with a yellow stroke, like the channel art.
  sting(d) {
    const name = d.title.replace(/^([^,\s]+,)/, '<span class="mk">$1</span>');
    return `<div class="sting"><img src="${EP.logo || 'brand_mark.svg'}" alt="" data-in="0"><div class="name" data-in="0.15">${name}</div>
      <div class="series" data-in="0.3">${d.series || EP.brand_label || ''}</div></div>`;
  },

  endcard(d) {
    const svg = (path, fill = 'none') => `<svg width="64" height="64" viewBox="0 0 24 24" fill="${fill}" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round">${path}</svg>`;
    return `<div class="endcard"><div class="mark" data-in="0.05"><img src="${EP.logo || 'brand_mark.svg'}" alt="">${d.title}</div>
      <div class="btns"><div class="ebtn sub" data-in="0.15">${svg('<path d="M8 5v14l11-7z"/>', 'currentColor')}<div class="w"><span class="w1">구독</span><span class="w2">구독중 ✓</span></div></div>
      <div class="ebtn like" data-in="0.25">${svg('<path d="M7 11v9H4v-9h3zm0 0 4-7a2 2 0 0 1 3 2l-1 4h5a2 2 0 0 1 2 2.3l-1.3 6A2 2 0 0 1 16.7 20H7"/>')}</div>
      <div class="ebtn bell" data-in="0.35">${svg('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0"/>')}</div></div>
      <svg class="cursor" viewBox="0 0 24 24"><path d="M4 2l16 10-7 1.5L10 21z" fill="#fff" stroke="#0A1830" stroke-width="1.4"/></svg></div>`;
  },
};

// Per-scene motion seek() cannot express with data attributes, driven by the scene-local time lt.
const SEEK = {
  endcard(scn, lt) {
    const at = e => { let x = e.offsetWidth * 0.6, y = e.offsetHeight * 0.62; for (let q = e; q && q !== scn; q = q.offsetParent) { x += q.offsetLeft; y += q.offsetTop; } return [x, y]; };
    const sub = $('.ebtn.sub', scn), like = $('.ebtn.like', scn), bell = $('.ebtn.bell', scn), cur = $('.cursor', scn);
    // Arrive at each button, press it 0.1 s later: subscribe, like, then the bell.
    const stops = [[1560, 1010], at(sub), at(like), at(bell)], times = [0.55, 1.15, 1.95, 2.75];
    let p = stops[0];
    for (let i = 1; i < stops.length; i++) {
      const from = i === 1 ? times[0] : times[i - 1] + 0.2;
      const k = CAM(prog(lt, from, times[i] - from));
      if (lt >= from) p = [stops[i - 1][0] + (stops[i][0] - stops[i - 1][0]) * k, stops[i - 1][1] + (stops[i][1] - stops[i - 1][1]) * k];
    }
    const press = times.slice(1).some(t => lt >= t + 0.05 && lt < t + 0.2);
    cur.style.opacity = clamp((lt - times[0]) / 0.2);
    cur.style.transform = `translate(${p[0] - 14}px,${p[1] - 8}px) scale(${press ? 0.88 : 1})`;
    const on = lt >= times[1] + 0.1;
    sub.classList.toggle('on', on);
    $('.w1', sub).style.opacity = on ? 0 : 1;
    $('.w2', sub).style.opacity = on ? 1 : 0;
    const liked = lt >= times[2] + 0.1;
    like.style.color = liked ? 'var(--y)' : '';
    like.style.borderColor = liked ? 'var(--y)' : '';
    $('svg', like).style.fill = liked ? 'var(--y)' : 'none';
    const rang = lt >= times[3] + 0.1;
    bell.style.color = rang ? 'var(--y)' : '';
    bell.style.borderColor = rang ? 'var(--y)' : '';
  },
};

function drawMap(d) {
  const M = window.MAP, s = el('svg', { width: 1000, height: 580, viewBox: `0 0 ${M.W} ${M.H}` });
  s.innerHTML = `<rect width="${M.W}" height="${M.H}" fill="#0C1F3D"/>
   <path d="${M.land}" fill="#1A2E50" stroke="#3A4C70" stroke-width="1.2"/>
   <path d="${M.borders}" fill="none" stroke="#3A4C70" stroke-width="1" stroke-dasharray="4 4"/>`;
  const L = M.labels, lab = (p, t, o = {}) => `<g data-in="${o.t || 0.6}"><text x="${p[0]}" y="${p[1]}" text-anchor="middle" font-size="${o.size || 24}" font-weight="${o.w || 500}" fill="${o.fill || '#AEB7C7'}" letter-spacing="${o.ls || 0}">${t}</text></g>`;
  s.innerHTML += lab(L.iran, '이란', { size: 30 }) + lab(L.oman, '오만') + lab(L.uae, '아랍에미리트') + lab(L.saudi, '사우디아라비아') + lab(L.qatar, '카타르', { size: 20 })
    + lab(L.gulf, '페르시아만', { fill: '#8E9AB0', size: 26, ls: 4 });
  const st = L.strait;
  s.innerHTML += `<circle cx="${st[0]}" cy="${st[1]}" r="34" fill="none" stroke="#FFD43B" stroke-width="2" stroke-opacity=".7"/><g data-in="0.8"><text x="${st[0] + 6}" y="${st[1] - 46}" text-anchor="middle" font-size="26" font-weight="700" fill="#FFD43B">호르무즈 해협</text></g>`;
  const p = 'M' + M.route.map(q => q.join(',')).join(' L');
  s.innerHTML += `<path id="route_${d.id}" d="${p}" fill="none" stroke="#FFD43B" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" data-draw="1.0,1.2"/>
   <g data-follow="#route_${d.id},1.0,1.2"><circle r="9" fill="#F2F4F8"/><circle r="4" fill="#0A1830"/></g>`;
  // No preset markers: '미군 호위 · 오만 연안' and '해협 밖 환적' came from the design sample and appeared on every Hormuz
  // map as if reported (10/14 AM review); the map shows only geography, the route sketch and the strait.
  s.innerHTML += `<text x="${M.W - 18}" y="${M.H - 16}" text-anchor="end" font-size="16" font-weight="500" fill="#6E7C96">지도 데이터: Natural Earth · 경로는 개념도</text>`;
  document.getElementById('mapcard_' + d.id).appendChild(s);
}
