/* Card catalog (spec §5). Each function returns the scene's inner HTML from episode data only.
   Coordinates and motion values come from the v2 prototype; scenes may not add other card types. */
const POST = [];
const DIR = { up: ['upc', 'upn', '▲'], dn: ['dnc', 'dnn', '▼'], flat: ['', '', ''] };
const arrow = d => (DIR[d] || DIR.flat)[2];
const C = (d, navy) => (DIR[d] || DIR.flat)[navy ? 1 : 0];

function header(d) {
  const chips = (d.chips || []).map((c, i) => `<span class="chip ${i === 0 && d.gold !== false ? 'tag-gold' : 'tag-line'}">${c}</span>`).join('');
  return `<div class="hdr" data-in="0.2,0.5">${chips}<div class="h1" style="margin-left:${chips ? 6 : 0}px">${d.title}</div>${d.tag ? `<span class="chip tag-line" style="margin-left:8px">${d.tag}</span>` : ''}</div>`;
}
const src = s => s ? `<span class="chip src">${s}</span>` : '';
const cnt = (v, s, dur = 0.9) => {
  const m = String(v).match(/^([^\d]*)([\d,]+(?:\.\d+)?)(.*)$/);
  return m ? `${m[1]}<span data-count="${s},${dur}" data-target="${m[2]}">0</span>${m[3]}` : v;
};

const CARDS = {
  cold_open(d) {
    const q = d.question.map((line, i) => `<div>${line.map(([w, g], k) => `<span data-in="${(0.5 + i * 0.45 + k * 0.15).toFixed(2)},0.45"${g ? ' style="color:var(--gold)"' : ''}>${w}</span>`).join(' ')}</div>`).join('');
    const stats = d.stats.map((s, i) => {
      const x = [0, 580, 1160][i], w = i === 2 ? 600 : 540, b = 2.0 + i * 0.3;
      return `<div class="glass" style="left:${x}px;top:400px;width:${w}px;height:240px;padding:30px 34px" data-in="${b},0.5">
        <div class="lab muted">${s.label}</div>
        <div class="tnum ${s.big_dir ? C(s.big_dir, 1) : ''}" style="font-size:80px;font-weight:800;margin-top:8px">${s.big_dir ? arrow(s.big_dir) + ' ' : ''}${cnt(s.value, b + 0.2)}</div>
        <div class="${s.sub_dir ? C(s.sub_dir, 1) : ''}" style="font-size:30px;font-weight:800;margin-top:6px">${s.sub}</div></div>`;
    }).join('');
    return `<div class="lab" style="position:absolute;top:10px;left:0;color:var(--gold)" data-in="0.2,0.5">${d.kicker}</div>
      <div style="position:absolute;top:70px;left:0;font-size:92px;font-weight:800;letter-spacing:-.03em;line-height:1.18">${q}</div>${stats}`;
  },

  summary3(d) {
    const cards = d.cards.map((c, i) => {
      const x = i * 600, b = 0.6 + i * 0.15;
      return `<div class="paper" style="left:${x}px;top:100px;width:560px;height:600px;overflow:hidden" data-in="${b},0.55">
       <div class="spot"><div class="num" data-pop="${b + 0.3},0.5">${c.n}</div>
        ${c.items.map((it, k) => `<div class="spot-item"><div style="height:132px;display:flex;align-items:center"><span data-icon="${it[0]},${it[1]},${it[2]},${b + 0.4 + k * 0.25},0.9"></span></div>${it[4] ? `<div class="badge ${it[4]}" data-pop="${b + 1.1 + k * 0.2},0.45">${it[5]}</div>` : ''}<div class="cap">${it[3]}</div></div>`).join('')}
       </div>
       <div style="position:relative;height:350px;padding:28px 36px 0"><div class="h2">${c.title}</div>
        <div style="position:absolute;left:36px;right:36px;bottom:34px;display:grid;grid-template-columns:1fr 1fr;gap:16px">
         ${c.stats.map((s, k) => `<div class="stat" data-in="${b + 1.3 + k * 0.15},0.45" style="padding:14px 18px;border-radius:16px;background:rgba(11,21,48,.05);border:1px solid rgba(11,21,48,.08)"><div class="k">${s[0]}</div><div class="v tnum ${s[2] ? C(s[2]) : ''}" style="${s[2] ? '' : 'color:var(--ink)'};font-size:${s[1].length > 8 ? 30 : 36}px">${s[1]}</div></div>`).join('')}
        </div></div></div>`;
    }).join('');
    return `<div class="h1" style="position:absolute;top:0;left:0" data-in="0.15,0.5">${d.title}</div>${cards}`;
  },

  market_board(d) {
    const tiles = d.tiles.map((b, i) => {
      const x = (i % 4) * 450, y = 110 + Math.floor(i / 4) * 300;
      const tag = b.badge ? `<span class="chip ${b.badge.startsWith('★') ? 'tag-gold' : ''}" style="position:absolute;right:22px;top:22px;font-size:18px;padding:4px 12px;${b.badge.startsWith('★') ? '' : 'color:var(--ink-6);border-width:1px'}" data-pop="${1.5 + i * 0.12},0.45">${b.badge}</span>` : '';
      return `<div class="paper" style="left:${x}px;top:${y}px;width:410px;height:265px;padding:26px 30px" data-in="${0.5 + i * 0.12},0.5">${tag}
        <div class="lab ink6">${b.name}</div>
        <div class="tnum" style="font-size:62px;font-weight:800;margin-top:6px;letter-spacing:-.02em">${cnt(b.value, 0.7 + i * 0.12)}</div>
        <div class="${C(b.dir)}" style="font-size:30px;font-weight:800;margin-top:2px">${arrow(b.dir)} <span class="tnum">${b.change}</span></div>
        <div class="ink6" style="font-size:20px;font-weight:600;margin-top:10px">${b.sub}</div></div>`;
    }).join('');
    return header(d) + tiles;
  },

  headline(d) {
    const stats = d.stats.map((s, i) => `<div data-in="${1.2 + i * 0.25},0.45" style="padding:${d.stats.length > 2 ? 14 : 20}px 26px;border-radius:18px;background:rgba(0,17,46,.45);border:1px solid rgba(252,241,219,.14);margin-top:${i ? 16 : 0}px">
        <div class="muted" style="font-size:22px;font-weight:700">${s.label}</div>
        <div style="display:flex;align-items:baseline;gap:16px;margin-top:4px"><span class="tnum" style="font-size:${d.stats.length > 2 ? 46 : 54}px;font-weight:800">${cnt(s.value, 1.4 + i * 0.25)}</span>${s.change ? `<span class="${C(s.dir, 1)} tnum" style="font-size:30px;font-weight:800">${arrow(s.dir)} ${s.change}</span>` : ''}</div>
        ${s.sub ? `<div class="muted" style="font-size:20px;font-weight:600;margin-top:4px">${s.sub}</div>` : ''}</div>`).join('');
    const labels = (d.labels || []).map((l, i) => `<div class="ilabel" style="left:${l.x}px;top:${l.y}px" data-pop="${1.6 + i * 0.3},0.5"><span class="tile" style="width:40px;height:40px;background:rgba(253,199,73,.18)"><span data-icon="${l.icon},28,${l.dir === 'dn' ? '#5EA0FF' : l.dir === 'up' ? '#FF5C6C' : '#FDC749'},${1.7 + i * 0.3},0.5"></span></span>${l.text}${l.dir ? `<span class="${C(l.dir, 1)}" style="margin-left:4px">${arrow(l.dir)}</span>` : ''}</div>`).join('');
    return header(d) + `<div class="paper imgbox" style="left:0;top:110px;width:1060px;height:590px" data-in="0.5,0.55">
        <img src="${d.image.file}" data-kb="14,0.06"><span class="chip credit">${d.image.credit}</span>${labels}</div>
      <div class="glass" style="left:1100px;top:110px;width:660px;height:590px;padding:30px 34px" data-in="0.9,0.55">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:22px"><span class="lab">${d.panel_title}</span>${src(d.source)}</div>${stats}</div>`;
  },

  flow(d) {
    const nodes = d.nodes.map((n, i) => {
      const x = i * 620, b = 0.7 + i * 0.9;
      const inner = n.big ? `<div class="tnum" style="font-size:76px;font-weight:800;letter-spacing:-.02em;margin-top:10px;line-height:1">${cnt(n.big, b + 0.3, 1.0)}</div><div class="${C(n.dir)}" style="font-size:28px;font-weight:800;margin-top:10px">${n.sub}</div>`
        : n.rows ? n.rows.map((r, k) => `<div class="tnum" style="display:flex;justify-content:space-between;align-items:baseline;margin-top:${k ? 12 : 22}px;font-size:32px;font-weight:800"><span>${r[0]}</span><span class="${C(r[2])}">${arrow(r[2])} ${r[1]}</span></div>`).join('')
        : `<div class="h2" style="margin-top:20px;font-size:38px">${n.text}</div>`;
      return `<div class="paper" style="left:${x}px;top:${d.top || 105}px;width:520px;height:${d.h || 330}px;padding:26px 32px" data-in="${b},0.5">
        <div style="display:flex;gap:10px;align-items:center">${n.chips.map(c => c === '보도 해석' ? `<span class="chip interp">${c}</span>` : `<span class="chip" style="color:var(--ink-6)">${c}</span>`).join('')}${src(n.source)}</div>
        <div style="display:flex;align-items:center;gap:14px;margin-top:18px"><span class="tile navy" style="width:56px;height:56px"><span data-icon="${n.icon},38,#FDC749,${b + 0.3},0.7"></span></span><span style="font-size:28px;font-weight:800">${n.head}</span>${n.badge ? `<span class="chip tag-gold" data-pop="${b + 0.6},0.5">${n.badge}</span>` : ''}</div>${inner}</div>`;
    }).join('');
    const y = (d.top || 105) + (d.h || 330) / 2;
    const arrows = `<svg style="position:absolute;left:0;top:0;overflow:visible" width="1760" height="730"><defs><marker id="ah_${d.id}" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11" fill="none" stroke="#FDC749" stroke-width="2.5"/></marker></defs>
      <path d="M530,${y} L600,${y}" stroke="#FDC749" stroke-width="4" fill="none" marker-end="url(#ah_${d.id})" data-draw="1.25,0.45"/>
      <path d="M1150,${y} L1220,${y}" stroke="#FDC749" stroke-width="4" fill="none" marker-end="url(#ah_${d.id})" data-draw="2.15,0.45"/></svg>`;
    const foot = d.foot ? `<div class="glass" style="left:0;top:${(d.top || 105) + (d.h || 330) + 40}px;width:1760px;height:150px;padding:0 40px;display:flex;align-items:center;gap:22px" data-in="3.4,0.5"><span class="chip interp" style="color:var(--gold);background:rgba(253,199,73,.12)">${d.foot.chip}</span><div class="body" style="font-weight:600">${d.foot.text}</div></div>` : '';
    return header(d) + nodes + arrows + foot;
  },

  counter(d) {
    const blocks = d.blocks.map((b, i) => {
      const t = 1.0 + i * 0.6;
      return `<div style="position:absolute;left:${b.x}px;top:40px" data-in="${t},0.45">
        <div class="lab muted">${b.label}</div>
        <div class="tnum" style="font-size:76px;font-weight:800;line-height:1;margin-top:14px;${b.gold ? 'color:var(--gold)' : ''}">${cnt(b.value, t + 0.2)}</div>
        ${b.sub ? `<div class="${b.dir ? C(b.dir, 1) : 'muted'}" style="font-size:25px;font-weight:800;margin-top:4px"${b.dir ? ` data-pop="${t + 0.9},0.5"` : ''}>${b.sub}</div>` : ''}</div>`;
    }).join('');
    const rows = (d.rows || []).map((r, i) => `<div class="row" data-in="${2.6 + i * 0.5},0.45" style="margin-top:${i ? 18 : 0}px"><span class="tile deep" style="width:64px;height:64px"><span data-icon="${r.icon},42,#B28DFF,${2.8 + i * 0.5},0.7"></span></span><div class="body" style="font-weight:600;font-size:30px">${r.text}</div>${src(r.source)}</div>`).join('');
    const ch = 360 + (d.rows || []).length * 82;
    return header({ ...d, chips: d.chips }) + `<div class="glass" style="left:0;top:${110 + Math.round((590 - ch) / 2)}px;width:1760px;height:${ch}px;border-color:rgba(178,141,255,.6)" data-in="0.5,0.5">
      <div style="position:absolute;left:40px;top:34px;width:420px"><div class="lab" style="color:var(--counter);font-size:28px">◆ 다르게 볼 점</div>
       <div style="display:flex;align-items:center;gap:14px;margin-top:22px"><span class="tile deep" style="width:64px;height:64px"><span data-icon="${d.icon},42,#B28DFF,0.9,0.8"></span></span><span style="font-size:28px;font-weight:800">${d.topic}</span></div>
       ${d.source ? `<div style="margin-top:18px">${src(d.source)}</div>` : ''}</div>
      <div style="position:absolute;left:470px;top:30px;width:1px;height:240px;background:rgba(252,241,219,.16)"></div>
      <div style="position:absolute;left:510px;top:0;width:1210px;height:280px">${blocks}</div>
      <div style="position:absolute;left:40px;right:40px;top:300px;height:1px;background:rgba(252,241,219,.16)"></div>
      <div style="position:absolute;left:40px;right:40px;top:336px">${rows}</div></div>`;
  },

  signals(d) {
    const used = d.rows.length * 182 + (d.note ? 130 : 0) + (d.disclaimer ? 50 : 0);
    d.top = d.top || 106 + Math.max(0, Math.floor((610 - used) / 2));
    const rows = d.rows.map((r, i) => {
      const y = d.top + i * 182, b = 0.5 + i * 0.3;
      return `<div class="glass" style="left:0;top:${y}px;width:1760px;height:160px;padding:0 36px;display:flex;align-items:center;gap:24px" data-in="${b},0.5">
       <span class="tile" style="width:84px;height:84px;background:rgba(60,207,180,.14);border:1px solid rgba(60,207,180,.4)"><span data-icon="${r.icon},52,#3CCFB4,${b + 0.3},0.7"></span></span>
       <div style="width:150px;font-size:32px;font-weight:800;color:var(--signal)">${r.topic}</div>
       <div style="flex:1;display:flex;align-items:center">${r.conds.map((c, j) => `${j ? `<span class="plus" data-pop="${b + 0.5 + j * 0.2},0.45">+</span>` : ''}<span class="pillc" data-pop="${b + 0.5 + j * 0.2},0.45">${c}</span>`).join('')}<span style="flex:1;height:0;border-top:2px dashed rgba(253,199,73,.35);margin:0 4px 0 22px" data-in="${b + 0.9},0.5,0"></span></div>
       <svg width="70" height="30" viewBox="0 0 70 30"><path d="M4 15H60M48 4L62 15L48 26" fill="none" stroke="#FDC749" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" data-draw="${b + 1.0},0.4"/></svg>
       <div style="width:470px;font-size:32px;font-weight:800;color:var(--gold)" data-in="${b + 1.2},0.45,10">${r.then}</div></div>`;
    }).join('');
    const note = d.note ? `<div class="glass" style="left:0;top:${(d.top || 106) + d.rows.length * 182 + 10}px;width:1760px;height:120px;padding:0 36px;display:flex;align-items:center" data-in="2.2,0.5"><div class="body" style="font-weight:600">${d.note}</div></div>` : '';
    const disc = d.disclaimer ? `<div class="muted" style="position:absolute;left:0;top:${d.top + d.rows.length * 182 + 6}px;font-size:22px;font-weight:600" data-in="2.6,0.5">${d.disclaimer}</div>` : '';
    return header(d) + rows + note + disc;
  },

  calendar(d) {
    if (d.mode === 'day') {
      const rows = d.rows.map(([ic, t, s], i) => `<div data-in="${1.2 + i * 0.25},0.45" style="display:flex;align-items:center;gap:24px;padding:16px 0;${i < d.rows.length - 1 ? 'border-bottom:1px solid rgba(11,21,48,.10)' : ''}">
        <span class="tile navy" style="width:84px;height:84px"><span data-icon="${ic},52,#FDC749,${1.4 + i * 0.25},0.7"></span></span>
        <div><div style="font-size:36px;font-weight:800;letter-spacing:-.01em">${t}</div><div class="ink6" style="font-size:23px;font-weight:600;margin-top:4px">${s}</div></div></div>`).join('');
      const side = d.side.map((s, i) => `<div data-in="${2.6 + i * 0.5},0.45" style="padding:22px 28px;border-radius:18px;background:rgba(0,17,46,.45);border:1px solid rgba(252,241,219,.14);">
        <div style="display:flex;align-items:center;gap:12px"><span class="muted" style="font-size:22px;font-weight:700">${s.label}</span>${src(s.source)}</div>
        <div class="tnum" style="font-size:50px;font-weight:800;margin-top:6px;${s.dir ? '' : 'color:var(--gold)'}"><span class="${s.dir ? C(s.dir, 1) : ''}">${cnt(s.value, 2.8 + i * 0.5)}</span></div>
        <div class="muted" style="font-size:21px;font-weight:600;margin-top:4px">${s.sub}</div></div>`).join('');
      return header(d) + `<div class="paper" style="left:0;top:110px;width:900px;height:590px;overflow:hidden" data-in="0.6,0.55">
        <div style="background:var(--navy-800);color:var(--cream);padding:24px 40px;display:flex;align-items:center;gap:18px">
         <span data-icon="calendar,52,#FDC749,0.9,0.6"></span><div class="tnum" style="font-size:60px;font-weight:800;color:var(--gold);line-height:1">${d.date}</div><div style="font-size:34px;font-weight:800">${d.dow}</div>${d.badge ? `<div style="margin-left:auto" class="chip tag-gold" data-pop="1.2,0.5">${d.badge}</div>` : ''}</div>
        <div style="position:relative;padding:10px 40px 0">${rows}</div></div>
       <div class="glass" style="left:940px;top:110px;width:820px;height:590px;padding:30px 36px;display:flex;flex-direction:column" data-in="0.9,0.55"><div class="lab" style="margin-bottom:20px">${d.side_title}</div><div style="flex:1;display:flex;flex-direction:column;justify-content:space-between">${side}</div></div>`;
    }
    const LX = 330, top = 106 + Math.max(0, Math.floor((610 - d.rows.length * 118) / 2));
    let h = `<div style="position:absolute;left:${LX - 1}px;top:${top + 26}px;width:3px;height:${d.rows.length * 118 - 90}px;background:rgba(253,199,73,.35)" data-in="0.4,0.5"></div>`;
    d.rows.forEach(([dt, w, bd, ic, e, s], i) => {
      const y = top + i * 118;
      h += `<div data-in="${0.6 + i * 0.15},0.45" style="position:absolute;left:0;top:${y}px;width:1760px;height:100px">
       <div style="position:absolute;left:0;top:10px;width:290px;text-align:right;font-size:38px;font-weight:800;color:var(--gold);line-height:1.1" class="tnum">${dt}${w ? `<span style="font-size:22px;color:var(--muted);margin-left:10px">${w}</span>` : ''}
         ${bd ? `<div style="margin-top:6px"><span class="chip tag-gold" style="font-size:18px;padding:3px 12px">${bd}</span></div>` : ''}</div>
       <div style="position:absolute;left:${LX - 9}px;top:38px;width:19px;height:19px;border-radius:50%;background:var(--gold);box-shadow:0 0 0 7px rgba(253,199,73,.18)"></div>
       <div class="glass" style="left:${LX + 42}px;top:0;width:${1760 - LX - 42}px;height:96px;padding:0 26px 0 18px;display:flex;align-items:center;gap:20px">
         <span class="tile deep" style="width:62px;height:62px"><span data-icon="${ic},40,#FDC749,${0.9 + i * 0.15},0.6"></span></span>
         <div style="font-size:31px;font-weight:700;flex:1">${e}</div><span class="chip" style="color:var(--muted);border-color:rgba(174,183,212,.4);font-size:20px">${s}</span></div></div>`;
    });
    return header(d) + h;
  },

  compare(d) {
    const [a, b] = d.bars, max = Math.max(a.n, b.n), H = 200;
    const ha = H * a.n / max, hb = H * b.n / max;
    const tiles = d.tiles.map((t, i) => `<div data-in="${1.0 + i * 0.4},0.45" style="padding:22px 28px;border-radius:18px;background:rgba(11,21,48,.05);border:1px solid rgba(11,21,48,.08);margin-top:${i ? 20 : 0}px">
       <div style="display:flex;align-items:center;gap:12px"><span class="ink6" style="font-size:22px;font-weight:700">${t.label}</span>${src(t.source)}</div>
       <div class="tnum ${t.dir ? C(t.dir) : ''}" style="font-size:48px;font-weight:800;margin-top:6px">${t.dir ? arrow(t.dir) + ' ' : ''}${cnt(t.value, 1.2 + i * 0.4)}</div>
       <div class="ink6" style="font-size:21px;font-weight:600;margin-top:4px">${t.sub}</div></div>`).join('');
    return header(d) + `<div class="paper" style="left:0;top:110px;width:820px;height:590px;padding:30px 36px" data-in="0.6,0.55">${tiles}</div>
     <div class="glass" style="left:860px;top:110px;width:900px;height:590px;padding:30px 44px" data-in="0.9,0.55">
      <div style="display:flex;align-items:center;gap:12px"><span class="lab">${d.chart_title}</span>${src(d.chart_source)}</div>
      <svg width="812" height="330" style="margin-top:56px;overflow:visible">
       <defs><marker id="ahc_${d.id}" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11" fill="none" stroke="#5EA0FF" stroke-width="2.5"/></marker></defs>
       <line x1="60" y1="282" x2="752" y2="282" stroke="rgba(252,241,219,.25)" stroke-width="2"/>
       <rect x="151" y="${282 - ha}" width="190" height="${ha}" rx="10" fill="#AEB7D4" data-vbar="1.6,0.8"/>
       <rect x="471" y="${282 - hb}" width="190" height="${hb}" rx="10" fill="#FDC749" data-vbar="2.0,0.8"/>
       <text x="246" y="${282 - ha - 16}" text-anchor="middle" font-size="40" font-weight="800" fill="#FCF1DB" data-in="2.3,0.4,10">${a.label}</text>
       <text x="566" y="${282 - hb - 16}" text-anchor="middle" font-size="40" font-weight="800" fill="#FDC749" data-in="2.7,0.4,10">${b.label}</text>
       <text x="246" y="320" text-anchor="middle" font-size="25" font-weight="700" fill="#AEB7D4">${a.name}</text>
       <text x="566" y="320" text-anchor="middle" font-size="25" font-weight="700" fill="#AEB7D4">${b.name}</text>
       <path d="M352,${282 - ha - 52} C392,${282 - ha - 82} 432,${282 - ha - 78} 462,${282 - hb - 50}" fill="none" stroke="#5EA0FF" stroke-width="3.5" marker-end="url(#ahc_${d.id})" data-draw="3.0,0.6"/>
       <text x="407" y="${Math.max(-30, 282 - ha - 100)}" text-anchor="middle" font-size="24" font-weight="800" fill="#5EA0FF" data-in="3.3,0.4,8">${d.arrow_text}</text></svg>
      <div style="display:flex;gap:18px;margin-top:22px" data-in="3.6,0.45">${d.extra.map(e => `<div style="flex:1;padding:16px 22px;border-radius:16px;background:rgba(0,17,46,.45);border:1px solid rgba(252,241,219,.14)"><div class="muted" style="font-size:20px;font-weight:700">${e[0]}</div><div class="tnum" style="font-size:40px;font-weight:800;margin-top:2px">${e[1]}</div></div>`).join('')}</div></div>`;
  },

  bars(d) {
    const W = d.image ? 1020 : (d.side ? 1040 : 1760), X = d.image ? 740 : 0;
    const max = Math.max(...d.rows.map(r => Math.abs(r.n)));
    const nameW = d.name_w || 300, gap = d.gap || 68, bh = d.gap ? 56 : 44;
    // name column | negative bars | axis | positive bars + value. Negative values sit just right of the axis.
    const half = Math.floor((W - 36 - nameW - 20 - 230) / 2), x0 = 36 + nameW + 20 + half;
    let h = `<div class="lab ink6" style="position:absolute;left:36px;top:28px">${d.unit_note}</div><div style="position:absolute;left:${x0}px;top:82px;width:2px;height:${d.rows.length * gap + 20}px;background:rgba(11,21,48,.22)"></div>`;
    d.rows.forEach((r, i) => {
      const y = 92 + i * gap, w = Math.max(4, Math.abs(r.n) / max * half), neg = r.n < 0;
      h += `<div style="position:absolute;left:36px;top:${r.tag ? y - 4 : y + 6}px;width:${nameW}px;font-size:28px;font-weight:800;line-height:1.1">${r.name}${r.tag ? `<div style="font-size:18px;font-weight:700;color:var(--ink-6);margin-top:3px">${r.tag}</div>` : ''}</div>`;
      h += `<div class="bar" style="top:${y}px;height:${bh}px;${neg ? `left:${x0 - w}px;transform-origin:right` : `left:${x0 + 2}px;transform-origin:left`};width:${w}px;background:${neg ? 'var(--down-c)' : 'var(--up-c)'}" data-bar="${1.2 + i * 0.1},0.7"></div>`;
      h += `<div class="tnum ${neg ? 'dnc' : 'upc'}" style="position:absolute;top:${y + (bh - 34) / 2}px;left:${neg ? x0 + 16 : x0 + w + 14}px;font-size:28px;font-weight:800;white-space:nowrap" data-in="${1.6 + i * 0.1},0.35,10">${r.label}</div>`;
    });
    let left = '';
    if (d.image) {
      const labels = (d.image.labels || []).map((l, i) => `<div class="ilabel" style="left:${l.x}px;top:${l.y}px" data-pop="${1.6 + i * 0.3},0.5"><span class="tile" style="width:40px;height:40px;background:rgba(253,199,73,.18)"><span data-icon="${l.icon},28,${l.dir === 'dn' ? '#5EA0FF' : '#FF5C6C'},${1.7 + i * 0.3},0.5"></span></span>${l.text}<span class="${C(l.dir, 1)}" style="margin-left:4px">${arrow(l.dir)}</span></div>`).join('');
      left = `<div class="paper" style="left:0;top:110px;width:700px;height:590px;overflow:hidden" data-in="0.5,0.55">
        <div class="imgbox" style="width:700px;height:430px"><img src="${d.image.file}" data-kb="14,0.06"><span class="chip credit">${d.image.credit}</span>${labels}</div>
        <div style="padding:22px 32px"><div class="body" style="font-size:27px;font-weight:600">${d.image.caption}</div></div></div>`;
    }
    let side = '';
    if (d.side) {
      const m = Math.max(...d.side.rows.map(r => Math.abs(r.n))), hw = 150, c2 = 330;
      let g = `<div style="position:absolute;left:${c2}px;top:84px;width:2px;height:186px;background:rgba(252,241,219,.25)"></div>`;
      d.side.rows.forEach((r, i) => {
        const y = 96 + i * 58, w = Math.abs(r.n) / m * hw, neg = r.n < 0;
        g += `<div style="position:absolute;left:38px;top:${y + 2}px;font-size:28px;font-weight:700">${r.name}</div>`;
        g += `<div class="bar" style="top:${y}px;height:38px;${neg ? `left:${c2 - w}px;transform-origin:right` : `left:${c2 + 2}px;transform-origin:left`};width:${w}px;background:${neg ? 'var(--down-n)' : 'var(--up-n)'}" data-bar="${1.4 + i * 0.12},0.7"></div>`;
        g += `<div class="tnum ${neg ? 'dnn' : 'upn'}" style="position:absolute;top:${y + 1}px;right:38px;font-size:30px;font-weight:800" data-in="${1.8 + i * 0.12},0.4,10">${r.label}</div>`;
      });
      side = `<div class="glass" style="left:1080px;top:110px;width:680px;height:590px" data-in="1.0,0.55">
        <div class="lab muted" style="position:absolute;left:38px;top:32px">${d.side.title}</div>${g}
        <div style="position:absolute;left:38px;right:38px;top:300px;height:1px;background:rgba(252,241,219,.15)"></div>
        <div style="position:absolute;left:38px;right:38px;top:326px" data-in="2.4,0.45"><div style="display:flex;align-items:center;gap:12px"><span class="tile" style="width:44px;height:44px;background:rgba(60,207,180,.16)"><span data-icon="check,30,#3CCFB4,2.6,0.5"></span></span><span class="lab" style="color:var(--signal)">확인할 신호</span></div>
        <div class="body" style="font-size:28px;margin-top:12px">${d.side.signal}</div></div></div>`;
    }
    return header(d) + left + `<div class="paper" style="left:${X}px;top:110px;width:${W}px;height:590px" data-in="0.8,0.55">${h}${d.source ? `<div style="position:absolute;right:30px;bottom:24px">${src(d.source)}</div>` : ''}</div>` + side;
  },

  photo(d) {
    const rows = d.rows.map((r, i) => `<div data-in="${1.2 + i * 0.5},0.45" style="padding:18px 0;${i < d.rows.length - 1 ? 'border-bottom:1px solid rgba(252,241,219,.12)' : ''}">
       <div style="display:flex;align-items:center;gap:12px"><span class="chip ${r.kind === 'counter' ? '' : r.kind === 'interp' ? 'interp' : 'tag-line'}" style="${r.kind === 'counter' ? 'color:var(--counter)' : r.kind === 'signal' ? 'color:var(--signal);border-color:var(--signal)' : ''};font-size:19px;padding:4px 12px">${r.chip}</span>${src(r.source)}</div>
       <div style="font-size:${r.big ? 44 : 30}px;font-weight:800;margin-top:10px;line-height:1.25" class="tnum">${r.big ? cnt(r.big, 1.4 + i * 0.5) : ''}${r.big && r.text ? ' ' : ''}${r.text ? `<span style="font-size:30px">${r.text}</span>` : ''}</div>
       ${r.sub ? `<div class="muted" style="font-size:22px;font-weight:600;margin-top:4px">${r.sub}</div>` : ''}</div>`).join('');
    return header(d) + `<div class="paper imgbox" style="left:0;top:110px;width:860px;height:590px" data-in="0.5,0.55"><img src="${d.image.file}" data-kb="14,0.06"><span class="chip credit">${d.image.credit}</span></div>
      <div class="glass" style="left:900px;top:110px;width:860px;height:590px;padding:22px 40px;display:flex;flex-direction:column;justify-content:space-between" data-in="0.9,0.55">${rows}</div>`;
  },

  map(d) {
    POST.push(() => drawMap(d));
    const bars = d.bars.map((b, i) => `<div style="margin-top:${i ? 14 : 20}px;font-size:22px;font-weight:700;${b.gold ? 'color:var(--gold)' : ''}" class="${b.gold ? '' : 'muted'}">${b.name}</div>
      <div style="position:relative;height:44px;margin-top:6px"><div class="bar" style="left:0;top:0;width:${b.w}px;background:${b.gold ? 'var(--gold)' : '#AEB7D4'};transform-origin:left" data-bar="${1.4 + i * 0.4},0.8"></div><span class="tnum" style="position:absolute;left:${b.w + 14}px;top:4px;font-size:28px;font-weight:800;white-space:nowrap" data-in="${2.0 + i * 0.4},0.4,10">${b.label}</span></div>`).join('');
    return header(d) + `<div class="glass" style="left:0;top:110px;width:1060px;height:590px;overflow:hidden;padding:0" data-in="0.5,0.55" id="mapcard_${d.id}"></div>
     <div class="glass" style="left:1100px;top:110px;width:660px;height:590px;padding:28px 36px" data-in="0.9,0.55">
      <div style="display:flex;align-items:center;gap:12px"><span data-icon="drop,34,#FDC749,1.1,0.6"></span><span class="lab">${d.panel_title}</span></div>${bars}
      <div style="display:flex;align-items:center;gap:14px;margin-top:18px" data-in="2.6,0.4"><span class="dnn" style="font-size:44px;font-weight:800">${d.delta}</span>${src(d.delta_source)}</div>
      <div class="muted" style="font-size:20px;font-weight:600;margin-top:10px;line-height:1.45" data-in="3.0,0.4">${d.alt}</div>
      <div style="position:absolute;left:36px;right:36px;bottom:28px;padding:14px 20px;border-radius:14px;background:rgba(253,199,73,.12);border:1px solid rgba(253,199,73,.45);display:flex;align-items:center;gap:16px" data-in="${d.cost_at || 6.0},0.45">
       <span data-icon="ship,52,#FDC749,${(d.cost_at || 6.0) + 0.2},0.8"></span><div style="font-size:22px;font-weight:700;line-height:1.35">${d.cost_head}<br><span style="color:var(--gold);font-size:31px;font-weight:800">${d.cost}</span>까지</div></div></div>`;
  },
};

function drawMap(d) {
  const M = window.MAP, s = el('svg', { width: 1060, height: 590, viewBox: `0 0 ${M.W} ${M.H}` });
  s.innerHTML = `<defs><linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0E3170"/><stop offset="1" stop-color="#0A2A63"/></linearGradient>
   <radialGradient id="pulse"><stop offset="0" stop-color="#FDC749" stop-opacity=".55"/><stop offset="1" stop-color="#FDC749" stop-opacity="0"/></radialGradient></defs>
   <rect width="${M.W}" height="${M.H}" fill="url(#sea)"/>
   <path d="${M.land}" fill="#1C3B78" stroke="rgba(252,241,219,.35)" stroke-width="1.2"/>
   <path d="${M.borders}" fill="none" stroke="rgba(252,241,219,.28)" stroke-width="1" stroke-dasharray="4 4"/>`;
  const L = M.labels, lab = (p, t, o = {}) => `<g data-in="${o.t || 1.2},0.5,10"><text x="${p[0]}" y="${p[1]}" text-anchor="middle" font-size="${o.size || 24}" font-weight="${o.w || 700}" fill="${o.fill || 'rgba(252,241,219,.78)'}" letter-spacing="${o.ls || 0}">${t}</text></g>`;
  s.innerHTML += lab(L.iran, '이란', { size: 30 }) + lab(L.oman, '오만') + lab(L.uae, '아랍에미리트') + lab(L.saudi, '사우디아라비아') + lab(L.qatar, '카타르', { size: 20 })
    + lab(L.gulf, '페르시아만', { fill: 'rgba(174,183,212,.85)', size: 26, ls: 4 });
  const st = L.strait;
  s.innerHTML += `<circle cx="${st[0]}" cy="${st[1]}" r="60" fill="url(#pulse)" data-pulse="1"/><g data-in="1.6,0.5,10"><text x="${st[0] + 6}" y="${st[1] - 42}" text-anchor="middle" font-size="26" font-weight="800" fill="#FDC749">호르무즈 해협</text></g>`;
  const p = 'M' + M.route.map(q => q.join(',')).join(' L');
  s.innerHTML += `<path d="${p}" fill="none" stroke="rgba(253,199,73,.25)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round" data-draw="2.0,2.6"/>
   <path id="route_${d.id}" d="${p}" fill="none" stroke="#FDC749" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" data-draw="2.0,2.6"/>
   <g data-follow="#route_${d.id},2.0,2.6"><circle r="11" fill="#FCF1DB"/><circle r="5" fill="#011A43"/></g>`;
  const e = M.escort, t = M.transship;
  s.innerHTML += `<g data-in="4.6,0.45,10"><circle cx="${e[0]}" cy="${e[1]}" r="9" fill="#3CCFB4"/><rect x="${e[0] + 16}" y="${e[1] - 20}" width="214" height="40" rx="10" fill="rgba(0,17,46,.82)" stroke="#3CCFB4"/><text x="${e[0] + 28}" y="${e[1] + 8}" font-size="21" font-weight="700" fill="#FCF1DB">미군 호위 · 오만 연안</text></g>
   <g data-in="5.2,0.45,10"><circle cx="${t[0]}" cy="${t[1]}" r="9" fill="#FDC749"/><rect x="${t[0] + 16}" y="${t[1] - 20}" width="150" height="40" rx="10" fill="rgba(0,17,46,.82)" stroke="#FDC749"/><text x="${t[0] + 28}" y="${t[1] + 8}" font-size="21" font-weight="700" fill="#FCF1DB">해협 밖 환적</text></g>
   <text x="${M.W - 18}" y="${M.H - 16}" text-anchor="end" font-size="16" font-weight="600" fill="rgba(174,183,212,.7)">지도 데이터: Natural Earth · 경로는 개념도</text>`;
  document.getElementById('mapcard_' + d.id).appendChild(s);
}
