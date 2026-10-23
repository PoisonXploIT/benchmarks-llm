// Comprueba el sitio renderizado en un Chrome con CDP en :9222.
// Uso: node verify_render.mjs <urlPrefix> <ancho> <alto> [light|dark]
// Devuelve JSON: contraste, elementos cortados, desbordes, errores de consola.
const [prefix, w = "1440", h = "950", scheme = "light"] = process.argv.slice(2);

const list = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const t = list.find(x => x.type === "page" && x.url.startsWith(prefix));
if (!t) { console.log("sin pestana", prefix); process.exit(1); }

const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let id = 0; const pending = new Map();
const events = [];
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.method === "Runtime.exceptionThrown") {
    events.push("EXC " + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text));
  }
  if (m.method === "Log.entryAdded" && ["error", "warning"].includes(m.params.entry.level)) {
    events.push(m.params.entry.level.toUpperCase() + " " + m.params.entry.text.slice(0, 160));
  }
  if (m.id && pending.has(m.id)) {
    const { res, rej } = pending.get(m.id); pending.delete(m.id);
    m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
  }
});
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id; pending.set(i, { res, rej });
  ws.send(JSON.stringify({ id: i, method, params }));
});

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Emulation.setEmulatedMedia", {
  features: [{ name: "prefers-color-scheme", value: scheme }],
});
await send("Emulation.setDeviceMetricsOverride", {
  width: Number(w), height: Number(h), deviceScaleFactor: 1, mobile: Number(w) < 600,
});
await send("Page.navigate", { url: prefix });
await new Promise(r => setTimeout(r, 1600));

const expr = `(function(){
  const cs = getComputedStyle(document.documentElement);
  const px = v => parseFloat(v) || 0;
  function rgba(c){const m=c.match(/[\\d.]+/g).map(Number);return {r:m[0],g:m[1],b:m[2],a:m[3]===undefined?1:m[3]};}
  function lum(c){const {r,g,b}=rgba(c);const f=v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)};return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);}
  function ratio(a,b){const l1=lum(a),l2=lum(b);return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);}
  const pageBg = getComputedStyle(document.body).backgroundColor;
  const sample = sel => { const e=document.querySelector(sel); if(!e) return null; const s=getComputedStyle(e); return {fg:s.color,bg:s.backgroundColor,size:s.fontSize,weight:s.fontWeight,ratio:+ratio(s.color, s.backgroundColor==="rgba(0, 0, 0, 0)"?pageBg:s.backgroundColor).toFixed(2)};};
  const clipped=[...document.querySelectorAll("body *")].filter(e=>{
    const s=getComputedStyle(e);
    if(s.display==="none"||s.visibility==="hidden"||s.overflow==="visible"&&e.scrollWidth>e.clientWidth+2) return false;
    return e.scrollWidth>e.clientWidth+2 && (s.textOverflow==="ellipsis"||s.overflow==="hidden");
  }).slice(0,10).map(e=>({tag:e.tagName,cls:e.className,sw:e.scrollWidth,cw:e.clientWidth,text:(e.textContent||"").slice(0,40)}));
  const wide=[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(e=>({tag:e.tagName,cls:String(e.className).slice(0,30),right:Math.round(e.getBoundingClientRect().right)}));
  const sec = document.querySelector("#censura").getBoundingClientRect();
  const hero = document.querySelector(".hero").getBoundingClientRect();
  const card = document.querySelector(".card").getBoundingClientRect();
  return JSON.stringify({
    scheme: matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light",
    bg: pageBg, ink: cs.getPropertyValue("--ink").trim(),
    docW: document.documentElement.scrollWidth, winW: innerWidth,
    hero:{w:Math.round(hero.width),h:Math.round(hero.height)},
    card:{w:Math.round(card.width),h:Math.round(card.height)},
    sectionTop: Math.round(sec.top),
    styles:{ body:sample("body"), h2:sample("h2"), note:sample(".note"), cardStrong:sample(".card strong"), cardLabel:sample(".card span"), value:sample(".bar-row .value"), th:sample("thead th"), heroLede:sample(".hero-lede"), chip:sample(".chip"), navLink:sample("nav a"), eyebrow:sample(".eyebrow")},
    clipped, wide,
    counts:{cards:document.querySelectorAll(".card").length,rows:document.querySelectorAll(".bar-row").length,tables:document.querySelectorAll("table").length,err:!!document.querySelector(".error")}
  },null,1);
})()`;

const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
console.log(r.result?.value ?? JSON.stringify(r.result));
if (events.length) console.log("EVENTOS:", JSON.stringify(events, null, 1));
ws.close();
