let CH=[];
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const eo=p=>1-Math.pow(1-p,4);              // ease-out quart ~ cubic-bezier(.2,.8,.2,1)
const eio=p=>p<.5?8*p*p*p*p:1-Math.pow(-2*p+2,4)/2;
const back=p=>{const c1=1.70158,c3=c1+1;return 1+c3*Math.pow(p-1,3)+c1*Math.pow(p-1,2);};
const prog=(lt,s,d)=>clamp((lt-s)/d);
const NS='http://www.w3.org/2000/svg';
const el=(tag,attrs={},html='')=>{const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(html)e.innerHTML=html;return e;};


/* ---------- pictogram library (64 grid, stroke) ---------- */
const ICONS={
 chartUp:['<path d="M8 56H56"/>','<rect x="12" y="40" width="8" height="12" rx="1.5"/>','<rect x="26" y="31" width="8" height="21" rx="1.5"/>','<rect x="40" y="22" width="8" height="30" rx="1.5"/>','<path d="M10 29L24 19L34 24L52 9"/>','<path d="M43 9H52V18"/>'],
 chip:['<rect x="16" y="16" width="32" height="32" rx="5"/>','<rect x="25" y="25" width="14" height="14" rx="2"/>','<path d="M24 16V9M32 16V9M40 16V9M24 55V48M32 55V48M40 55V48M16 24H9M16 32H9M16 40H9M55 24H48M55 32H48M55 40H48"/>'],
 memory:['<rect x="6" y="18" width="52" height="24" rx="3"/>','<rect x="11" y="23" width="8" height="12" rx="1"/>','<rect x="23" y="23" width="8" height="12" rx="1"/>','<rect x="35" y="23" width="8" height="12" rx="1"/>','<rect x="47" y="23" width="6" height="12" rx="1"/>','<path d="M10 42V50M18 42V50M26 42V50M38 42V50M46 42V50M54 42V50"/>'],
 calendar:['<rect x="9" y="13" width="46" height="42" rx="6"/>','<path d="M9 25H55"/>','<path d="M21 8V18M43 8V18"/>','<path d="M19 35H25M30 35H36M41 35H47M19 45H25M30 45H36"/>'],
 cal8:['<rect x="9" y="13" width="46" height="42" rx="6"/>','<path d="M9 25H55"/>','<path d="M21 8V18M43 8V18"/>','<text x="32" y="50" text-anchor="middle" font-size="22" font-weight="800" stroke="none" fill="COLOR">8</text>'],
 ship:['<path d="M5 40H59L52 52H12Z"/>','<path d="M38 40V27H51V40"/>','<path d="M42 27V20H46V27"/>','<path d="M13 40V34H32V40"/>','<path d="M4 59C8 56 12 56 16 59S24 62 28 59S36 56 40 59S48 62 52 59S58 57 60 58"/>'],
 doc:['<path d="M16 7H38L50 19V57H16Z"/>','<path d="M38 7V19H50"/>','<path d="M23 31H43M23 39H43M23 47H35"/>'],
 bank:['<path d="M8 22L32 9L56 22Z"/>','<path d="M11 27H53"/>','<path d="M16 31V48M27 31V48M37 31V48M48 31V48"/>','<path d="M8 54H56"/>'],
 drop:['<path d="M32 7C32 7 15 28 15 39A17 17 0 0 0 49 39C49 28 32 7 32 7Z"/>','<path d="M24 41A8 8 0 0 0 31 48"/>'],
 bolt:['<path d="M36 6L15 36H30L27 58L49 27H34Z"/>'],
 basket:['<path d="M7 25H57L51 53H13Z"/>','<path d="M20 25L28 10M44 25L36 10"/>','<path d="M24 32V46M32 32V46M40 32V46"/>'],
 clock:['<circle cx="32" cy="34" r="22"/>','<path d="M32 21V34L41 40"/>','<path d="M26 6H38M32 6V12"/>'],
 percent:['<circle cx="19" cy="19" r="7"/>','<circle cx="45" cy="45" r="7"/>','<path d="M48 13L16 51"/>'],
 shield:['<path d="M32 6L53 14V30C53 44 43 52 32 58C21 52 11 44 11 30V14Z"/>','<path d="M23 31L30 38L42 25"/>'],
 gauge:['<path d="M9 44A23 23 0 0 1 55 44"/>','<path d="M32 44L45 27"/>','<circle cx="32" cy="44" r="3.5"/>','<path d="M14 52H50"/>'],
 balance:['<path d="M32 10V54"/>','<path d="M14 18H50"/>','<path d="M14 18L6 36H22Z"/>','<path d="M50 18L42 36H58Z"/>','<path d="M22 54H42"/>'],
 check:['<path d="M12 33L26 47L52 18"/>'],
 globe:['<circle cx="32" cy="32" r="23"/>','<path d="M9 32H55"/>','<path d="M32 9C24 18 24 46 32 55C40 46 40 18 32 9Z"/>'],
};
function icon(name,size=64,color='#FDC749',ds=null,dd=0.8,sw=3.6){
 const prims=ICONS[name].map(p=>{p=p.replace(/COLOR/g,color);
   if(ds!==null && /^<(path|rect|circle|line|polyline|polygon)/.test(p)) p=p.replace(/^<(\w+)/,`<$1 data-draw="${ds},${dd}"`);
   return p;}).join('');
 return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${prims}</svg>`;
}
function injectIcons(root=document){
 for(const e of $$('[data-icon]',root)){const [n,s,c,ds,dd]=e.dataset.icon.split(',');e.innerHTML=icon(n,+s,c,ds!==undefined?+ds:null,dd!==undefined?+dd:0.8);e.classList.add('ico');e.removeAttribute('data-icon');}
}

/* ---------- boot: fixed frame + scenes from EP ---------- */
const PARTS=[];
function boot(){
 for(let i=0;i<28;i++){const d=document.createElement('i');const s=2+(i%3);d.style.width=d.style.height=s+'px';$('#parts').appendChild(d);
  PARTS.push({e:d,x0:(i*733)%1960,y0:110+((i*397)%780),v:6+((i*37)%12),a:8+((i*53)%26),ph:i*0.7,o:0.10+((i*13)%10)/50});}
 $('#dd').textContent=EP.date_label; $('#da').textContent='자료 기준 '+EP.asof; $('#ds').textContent=EP.sample_mark||''; if(EP.brand_label) $('#bl').textContent=EP.brand_label;
 CH=EP.chapter_names; $('#chapters').innerHTML=CH.map(c=>`<div class="pill"><i></i><b>${c}</b></div>`).join('');
 $('#tlab').textContent=EP.ticker.label;
 const tk=EP.ticker.items.map(it=>{const up=it.dir==='up';return `<span class="it"><span class="n">${it.name}</span>${it.value?`<span class="v tnum">${it.value}</span>`:''}<span class="${up?'upn':'dnn'} tnum">${up?'▲':'▼'} ${it.change}</span>${it.note?`<span class="n" style="margin-left:10px;font-weight:600">${it.note}</span>`:''}</span>`}).join('');
 $('#track').innerHTML=tk+tk;
 const stage=$('#stage');
 for(const sc of EP.scenes){const d=document.createElement('div');d.className='scene';d.dataset.t0=sc.t0;d.dataset.t1=sc.t1;d.dataset.ch=sc.chapter;d.dataset.id=sc.id;
  d.innerHTML=CARDS[sc.type](sc.data,sc);stage.appendChild(d);}
 injectIcons();
 for(const f of POST) f();
}

let tkW=0;
function count(e,p){const tg=e.dataset.target;const dec=(tg.split('.')[1]||'').length;const v=parseFloat(tg.replace(/,/g,''))*eo(p);
 let s=v.toFixed(dec); if(tg.includes(',')){const [a,b]=s.split('.');s=a.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(b!==undefined?'.'+b:'');} e.textContent=s;}
function seek(t){
 // background
 $('#glow').style.transform=`translate(${360+220*Math.sin(t*2*Math.PI/60)}px,${40+60*Math.cos(t*2*Math.PI/60)}px)`;
 for(const q of PARTS){const x=((q.x0+t*q.v)%1960)-20, y=q.y0+q.a*Math.sin(t*0.35+q.ph);q.e.style.transform=`translate(${x}px,${y}px)`;q.e.style.opacity=q.o*(0.6+0.4*Math.sin(t*0.8+q.ph));}
 let curCh='';
 for(const scn of $$('.scene')){
  const t0=+scn.dataset.t0,t1=+scn.dataset.t1, vis=t>=t0&&t<t1; scn.style.display=vis?'block':'none'; if(!vis)continue;
  curCh=scn.dataset.ch; const lt=t-t0;
  const ex=clamp((t-(t1-0.4))/0.4); scn.style.opacity=1-ex*ex; scn.style.transform=`translateY(${-20*ex}px)`;
  for(const e of $$('[data-in]',scn)){const [s,d=0.5,dy=40]=e.dataset.in.split(',').map(Number);const p=eo(prog(lt,s,d));e.style.opacity=p;
   if(e instanceof SVGElement){e.setAttribute('transform',`translate(0,${(1-p)*dy})`);} else e.style.transform=`translateY(${(1-p)*dy}px) scale(${0.98+0.02*p})`;}
  for(const e of $$('[data-pop]',scn)){const [s,d=0.45]=e.dataset.pop.split(',').map(Number);const p=prog(lt,s,d);e.style.opacity=p>0?Math.min(1,p*3):0;e.style.transform=`scale(${p>0?back(p):0.001})`;}
  for(const e of $$('[data-count]',scn)){const [s,d=0.9]=e.dataset.count.split(',').map(Number);count(e,prog(lt,s,d));}
  for(const e of $$('[data-bar]',scn)){const [s,d=0.8]=e.dataset.bar.split(',').map(Number);e.style.transform=`scaleX(${eo(prog(lt,s,d))})`;}
  for(const e of $$('[data-vbar]',scn)){const [s,d=0.8]=e.dataset.vbar.split(',').map(Number);const p=eo(prog(lt,s,d));
   if(!e.dataset.y){e.dataset.y=e.getAttribute('y');e.dataset.h=e.getAttribute('height');}const H=+e.dataset.h*p;e.setAttribute('height',H);e.setAttribute('y',+e.dataset.y+(+e.dataset.h-H));}
  for(const e of $$('[data-draw]',scn)){const [s,d=1.2]=e.dataset.draw.split(',').map(Number);const L=e.__L||(e.__L=e.getTotalLength()+1);const p=eio(prog(lt,s,d));e.style.strokeDasharray=`${L} ${L}`;e.style.strokeDashoffset=L*(1-p);e.style.opacity=p>0?1:0;}
  for(const e of $$('[data-follow]',scn)){const [sel,s,d]=e.dataset.follow.split(',');const path=$(sel,scn);const L=path.__L||(path.__L=path.getTotalLength());const p=eio(prog(lt,+s,+d));const q=path.getPointAtLength(Math.min(L-1,L*p));e.setAttribute('transform',`translate(${q.x},${q.y})`);e.style.opacity=p>0?1:0;}
  for(const e of $$('[data-pulse]',scn)){const k=(lt%2)/2;e.setAttribute('r',30+50*k);e.style.opacity=lt<1.4?0:(1-k);}
  for(const e of $$('[data-led]',scn)){const ph=+e.dataset.led;e.style.opacity=0.35+0.65*(0.5+0.5*Math.sin(2*Math.PI*(lt*0.6+ph)));}
  for(const e of $$('[data-kb]',scn)){const [d=13,z=0.06]=e.dataset.kb.split(',').map(Number);e.style.transform=`scale(${1+z*clamp(lt/d)})`;}
  const g=$('#illuG',scn); if(g){
   if(!g.dataset.base){const b=g.getBBox();const k=Math.min(680/b.width,400/b.height)*0.97;g.dataset.base=[350-(b.x+b.width/2)*k,222-(b.y+b.height/2)*k,k].join(',');}
   const [bx,by,bs]=g.dataset.base.split(',').map(Number);const z=1+0.06*clamp(lt/13);g.setAttribute('transform',`translate(${350+(bx-350)*z},${222+(by-222)*z}) scale(${bs*z})`);}
 }
 // chapters
 $$('.pill').forEach((p,i)=>{const on=CH[i]===curCh;p.querySelector('i').style.opacity=on?1:0;p.style.color=on?'#011A43':'';p.style.borderColor=on?'#FDC749':'';});
 $('#prog').style.width=(clamp(t/EP.total)*1920)+'px';
 // wipe at chapter changes
 const changes=EP.changes; let w=0,wx=-200;
 for(const c of changes){const p=(t-(c-0.35))/0.8;if(p>=0&&p<=1){w=Math.sin(Math.PI*p);wx=-200+p*2320;}}
 $('#wipe').style.opacity=w*0.55;$('#wipe').style.transform=`translateX(${wx}px)`;
 // captions
 const cb=$('#capbox'); const c=EP.captions.find(c=>t>=c[0]&&t<c[1]);
 if(c){if(cb.dataset.k!==String(c[0])){cb.innerHTML=c[2];cb.dataset.k=String(c[0]);}cb.style.opacity=Math.min(1,(t-c[0])/0.18,(c[1]-t)/0.18);}else cb.style.opacity=0;
 // ticker
 if(!tkW)tkW=$('#track').scrollWidth/2;
 $('#track').style.transform=`translateX(${300-((t*80)%tkW)}px)`;
}
window.seek=seek;
