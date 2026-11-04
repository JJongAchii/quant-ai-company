let CH=[];
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const prog=(lt,s,d)=>clamp((lt-s)/d);
const NS='http://www.w3.org/2000/svg';
const el=(tag,attrs={},html='')=>{const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(html)e.innerHTML=html;return e;};

/* ---------- motion language (spec §5, template v5): one main movement per scene, then the frame holds still.
   Lines rise out of their own baseline (14 frames), lines and bars draw (36 / 18 frames), the camera moves sideways
   between scenes (22 frames). No count-ups, zooms, pops, glow or particles. Start times sit on a 6-frame grid. ---------- */
const FPS=30;
function bez(x1,y1,x2,y2){const cx=3*x1,bx=3*(x2-x1)-cx,ax=1-cx-bx,cy=3*y1,by=3*(y2-y1)-cy,ay=1-cy-by;
 const X=t=>((ax*t+bx)*t+cx)*t,Y=t=>((ay*t+by)*t+cy)*t,dX=t=>(3*ax*t+2*bx)*t+cx;
 return x=>{if(x<=0)return 0;if(x>=1)return 1;let t=x;for(let i=0;i<8;i++){const d=dX(t);if(Math.abs(d)<1e-6)break;t-=(X(t)-x)/d;}return Y(clamp(t));};}
const REV=bez(0.16,1,0.3,1), CAM=bez(0.65,0,0.35,1), DRAW=bez(0.33,0,0.25,1);
const REVEAL=14/FPS, MOVE=22/FPS, BAR=18/FPS, TAB=12/FPS, HL=12/FPS, TICKER=70;
// The camera leaves a scene 0.1 s before its end; the next scene's own motion waits until the camera has landed.
const LEAVE=0.1, SETTLE=MOVE-LEAVE-0.2;
const grid=s=>Math.round(s*FPS/6)*6/FPS;

/* ---------- fit: [data-fit] text shrinks until it fits its box (2026-10-13 PM: '배럴당 1~2달러', '19.6%' ran off screen).
   Measured once, at the first seek (the renderer awaits document.fonts.ready first). */
let fitted=false;
function fitText(){
 for(const scn of $$('.scene')){const shown=scn.style.display;scn.style.display='block';
  for(const e of $$('[data-fit]',scn)){
   let size=parseFloat(getComputedStyle(e).fontSize);const floor=size*0.45;
   while(e.scrollWidth>e.clientWidth+1&&size>floor){size*=0.94;e.style.fontSize=size.toFixed(1)+'px';}}
  // Values shown side by side share the smallest fitted size (10/14 PM: each counter value shrank on its own, so the
  // shortest number, '4000명', was drawn the largest beside '28만3000명' and read as the biggest change).
  const groups={};
  for(const e of $$('[data-fit-group]',scn))(groups[e.dataset.fitGroup]??=[]).push(e);
  for(const g of Object.values(groups)){const min=Math.min(...g.map(e=>parseFloat(getComputedStyle(e).fontSize)));
   g.forEach(e=>{e.style.fontSize=min.toFixed(1)+'px';});}
  // A [data-fitbox] region scales its content down until it fits the region's height (10/14 AM: the map panel's text ran
  // over its cost box and down to the captions).
  for(const box of $$('[data-fitbox]',scn)){const inner=box.firstElementChild;let z=1;
   while(inner&&box.scrollHeight>box.clientHeight+1&&z>0.62){z-=0.04;inner.style.zoom=z.toFixed(2);}}
  scn.style.display=shown;}
 fillTicker();
}

/* ---------- boot: fixed frame + scenes from EP ---------- */
let TK='';
function tickerItem(it){const up=it.dir==='up';
 return `<span class="it">${it.name}${it.value?`<span class="v tnum">${it.value}</span>`:''}<span class="c ${up?'upn':'dnn'} tnum">${up?'▲':'▼'}${it.change}</span>${it.note?`<span style="margin-left:10px">${it.note}</span>`:''}</span><i class="dot"></i>`;}
function boot(){
 // Load every weight now so document.fonts.ready (awaited before the first seek) covers text in hidden scenes too.
 for(const w of [500,600,700,800]) document.fonts.load(`${w} 40px Pretendard`);
 for(const w of [600,700,800]) document.fonts.load(`${w} 40px BarlowC`);
 $('#brand').textContent=EP.brand_name||'그래서, 오늘장'; if(EP.logo) $('#logo').src=EP.logo;
 $('#bl').textContent=EP.brand_label||''; $('#dd').textContent=EP.date_label||''; $('#ds').textContent=EP.sample_mark||'';
 CH=EP.chapter_names; $('#chapters').innerHTML=CH.map(c=>`<span class="tab">${c}</span>`).join('')+'<i id="ul"></i>';
 $('#tlab').textContent=EP.ticker.label;
 TK=EP.ticker.items.map(tickerItem).join(''); $('#track').innerHTML=TK;
 const stage=$('#stage');
 for(const sc of EP.scenes){const d=document.createElement('div');d.className=sc.brand?'scene full':'scene';d.dataset.t0=sc.t0;d.dataset.t1=sc.t1;d.dataset.ch=sc.chapter;d.dataset.id=sc.id;d.dataset.type=sc.type;
  d.innerHTML=CARDS[sc.type](sc.data,sc);stage.appendChild(d);}
 for(const f of POST) f();
}

// The ticker repeats its items until one loop plus the screen width is covered, so it never shows a gap.
let tkW=0;
function fillTicker(){const tr=$('#track');tr.innerHTML=TK;const one=Math.max(1,tr.scrollWidth);
 const n=Math.ceil((1920+one)/one)+1;tr.innerHTML=TK.repeat(n);tkW=one;}

// A line of text rises out of its own baseline: it starts one line-height low, clipped at its final bottom edge, so
// the top of the letters shows first. Panels and pictures (taller than 160px) do not travel; they only fade in.
function revealHTML(e,k){
 if(k>=1){if(e.__r){e.style.clipPath='';e.style.transform='';e.style.opacity='';e.__r=0;}return;}
 e.__r=1; const h=e.__h??(e.__h=e.offsetHeight);
 if(h>160){e.style.opacity=k.toFixed(3);return;}
 const p=((1-k)*100).toFixed(2);
 e.style.opacity=k>0?1:0; e.style.transform=`translateY(${p}%)`; e.style.clipPath=`inset(-12px -80px ${p}% -80px)`;
}
function revealSVG(e,k){e.style.opacity=k.toFixed(3);e.setAttribute('transform',`translate(0,${((1-k)*10).toFixed(2)})`);}

function animate(scn,lt){
 for(const e of $$('[data-in],[data-pop]',scn)){const s=grid(+(e.dataset.in||e.dataset.pop).split(',')[0]);
  if(e instanceof SVGElement) revealSVG(e,REV(prog(lt,s,10/FPS))); else revealHTML(e,REV(prog(lt,s,REVEAL)));}
 for(const e of $$('[data-count]',scn)) if(e.textContent!==e.dataset.target) e.textContent=e.dataset.target;
 for(const e of $$('[data-bar]',scn)){const s=grid(+e.dataset.bar.split(',')[0]);e.style.transform=`scaleX(${DRAW(prog(lt,s,BAR))})`;}
 for(const e of $$('[data-vbar]',scn)){const s=grid(+e.dataset.vbar.split(',')[0]);const p=DRAW(prog(lt,s,BAR));
  if(!e.dataset.y){e.dataset.y=e.getAttribute('y');e.dataset.h=e.getAttribute('height');}const H=+e.dataset.h*p;e.setAttribute('height',H);e.setAttribute('y',+e.dataset.y+(+e.dataset.h-H));}
 for(const e of $$('[data-draw]',scn)){const [s0,d=1.2]=e.dataset.draw.split(',').map(Number);const L=e.__L||(e.__L=e.getTotalLength()+1);const p=DRAW(prog(lt,grid(s0),d));e.style.strokeDasharray=`${L} ${L}`;e.style.strokeDashoffset=L*(1-p);e.style.opacity=p>0?1:0;}
 for(const e of $$('[data-follow]',scn)){const [sel,s,d]=e.dataset.follow.split(',');const path=$(sel,scn);const L=path.__L||(path.__L=path.getTotalLength());const p=DRAW(prog(lt,grid(+s),+d));const q=path.getPointAtLength(Math.min(L-1,L*p));e.setAttribute('transform',`translate(${q.x},${q.y})`);e.style.opacity=p>0?1:0;}
 for(const e of $$('[data-hl]',scn)){const k=DRAW(prog(lt,grid(+e.dataset.hl),HL));e.style.backgroundSize=`${(k*100).toFixed(1)}% 22%`;}
}

function tabs(t,curCh){
 const tabsEl=$$('.tab'), ul=$('#ul'); if(!ul) return;
 // The chapter shown TAB seconds ago: the underline slides from it to the current chapter.
 const at=x=>{let c='';for(const scn of $$('.scene')){if(x>=+scn.dataset.t0&&x<+scn.dataset.t1){c=scn.dataset.ch;break;}}return c;};
 let from=curCh,k=1;
 for(let f=1;f<=12;f++){const c=at(t-f/FPS);if(c!==curCh){from=c;k=REV((f-1)/12);break;}}
 const rect=c=>{const i=CH.indexOf(c);if(i<0)return null;const e=tabsEl[i];return [e.offsetLeft,e.offsetWidth];};
 const a=rect(from),b=rect(curCh);
 tabsEl.forEach((e,i)=>e.classList.toggle('on',CH[i]===curCh));
 if(!b){ul.style.width='0px';return;}
 const [x0,w0]=a||[b[0],0];
 ul.style.left=(x0+(b[0]-x0)*k)+'px'; ul.style.width=(w0+(b[1]-w0)*k)+'px';
}

// Balanced caption lines are shorter than the box; the box shrinks to its widest line.
function hug(box){box.style.width='';const rg=document.createRange();rg.selectNodeContents(box);let l=Infinity,r=-Infinity;
 for(const q of rg.getClientRects()){if(q.width<1)continue;l=Math.min(l,q.left);r=Math.max(r,q.right);}
 if(r>l) box.style.width=Math.ceil(r-l+2)+'px';}

function seek(t){
 if(!fitted){fitted=true;fitText();}
 const scenes=$$('.scene'), last=scenes.length-1;
 let cur=null, top=1;
 scenes.forEach((scn,i)=>{
  const t0=+scn.dataset.t0,t1=+scn.dataset.t1;
  const start=i===0?t0:t0-LEAVE, end=i===last?t1:t1-LEAVE+MOVE;
  const vis=t>=start&&t<end; scn.style.display=vis?'block':'none';
  if(t>=t0&&t<t1) cur=scn;
  if(!vis) return;
  const kin=i===0?1:CAM(prog(t,t0-LEAVE,MOVE)), kout=i===last?0:CAM(prog(t,t1-LEAVE,MOVE));
  scn.style.transform=(kin<1||kout>0)?`translateX(${((1-kin)*1920-kout*1920).toFixed(1)}px)`:'';
  // The top bar belongs to the briefing: it fades out while the camera moves onto the fixed opening or closing.
  if(scn.classList.contains('full')) top=Math.min(top,1-kin+kout);
  const lt=t-t0-(i===0?0:SETTLE);
  if(SEEK[scn.dataset.type]) SEEK[scn.dataset.type](scn,lt);
  animate(scn,lt);
 });
 if(!cur) cur=scenes[t<0?0:last];
 top=clamp(top); $('#top').style.opacity=top.toFixed(3); document.body.classList.toggle('full',top===0);
 tabs(t,cur?cur.dataset.ch:'');
 // captions rise out of their band
 const cb=$('#capbox'); const c=EP.captions.find(c=>t>=c[0]&&t<c[1]);
 if(c){if(cb.dataset.k!==String(c[0])){cb.style.transform='';cb.innerHTML=c[2];cb.dataset.k=String(c[0]);hug(cb);}const k=REV(prog(t,c[0],REVEAL));
  cb.style.opacity=1;cb.style.transform=k<1?`translateY(${((1-k)*100).toFixed(1)}%)`:'';}
 else {cb.style.opacity=0;cb.style.transform='';}
 // ticker: constant speed, independent of the scenes
 if(tkW) $('#track').style.transform=`translateX(${(300-((t*TICKER)%tkW)).toFixed(1)}px)`;
}
window.seek=seek;
