const base = "http://127.0.0.1:8765/api/browser-";
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let running = false;

function allowed(url) {
  try {
    const p = new URL(url);
    if (p.protocol !== "https:" || p.username || p.password || p.port) return false;
    if (p.hostname === "lista.mercadolivre.com.br") return /^\/[^/]+\/?$/.test(p.pathname);
    return ["www.mercadolivre.com.br", "produto.mercadolivre.com.br", "mercadolivre.com.br"].includes(p.hostname) &&
      /(?:^|[/\-])MLB-?\d+(?:$|[/\-])/i.test(p.pathname);
  } catch { return false; }
}

async function request(action, data = {}) {
  const response = await fetch(base + action, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data),
    signal:AbortSignal.timeout(4500),
  });
  if (!response.ok) throw new Error("Painel indisponível");
  return response.json();
}

function capture() {
  const cardNodes = Array.from(document.querySelectorAll(".poly-card, .ui-search-layout__item"))
    .filter(card => !card.parentElement?.closest(".poly-card, .ui-search-layout__item") || card.classList.contains("ui-search-layout__item"));
  const selectors = [
    ".ui-pdp-title", ".ui-pdp-price__second-line", ".ui-pdp-price__original-value",
    ".ui-pdp-price__subtitles", ".ui-pdp-shipping", ".ui-pdp-coupons",
    ".ui-search-no-results", ".ui-search-rescue",
  ];
  const nodes = [...cardNodes, ...document.querySelectorAll(selectors.join(","))];
  function clean(node) {
    const copy = node.cloneNode(true);
    copy.querySelectorAll("script,form,input,iframe,template,button,textarea,svg,img").forEach(n => n.remove());
    for (const element of [copy, ...copy.querySelectorAll("*")]) {
      for (const attr of [...element.attributes]) {
        if (!["class","href","aria-label","aria-hidden"].includes(attr.name)) element.removeAttribute(attr.name);
      }
    }
    return copy.outerHTML;
  }
  const snippets = nodes.slice(0, 80).map(clean);
  if (!cardNodes.length) {
    const embedded = document.getElementById("__NORDIC_RENDERING_CTX__")?.textContent || "";
    const objectStart = embedded.indexOf("{");
    const objectEnd = embedded.lastIndexOf("}");
    if (objectStart >= 0 && objectEnd > objectStart) {
      try {
        const items = JSON.parse(embedded.slice(objectStart, objectEnd + 1)).appProps.pageProps.initialState.results;
        if (Array.isArray(items)) {
          const results = items.slice(0, 80).filter(r => r.polycard).map(({polycard:p}) => ({
            polycard:{
              metadata:{url:p.metadata?.url, url_fragments:p.metadata?.url_fragments},
              components:(p.components || []).flatMap(c => {
                if (c.type === "title") return [{type:"title", title:{text:c.title?.text}}];
                if (c.type === "price") {
                  const prices = c.price || {};
                  return [{type:"price", price:Object.fromEntries(
                    ["current_price","previous_price","original_price"].map(key =>
                      [key, {value:prices[key]?.value, currency:prices[key]?.currency}])
                  )}];
                }
                return [];
              }),
            }
          }));
          const payload = {appProps:{pageProps:{initialState:{results}}}};
          snippets.push('<script id="__NORDIC_RENDERING_CTX__">' + JSON.stringify(payload).replaceAll("<","\\u003c") + "</script>");
        }
      } catch {}
    }
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent);
        const product = (Array.isArray(data) ? data : (data["@graph"] || [data])).find(x => x["@type"] === "Product");
        if (product?.offers) {
          const {price, priceCurrency, availability} = product.offers;
          const item = {"@type":"Product", name:product.name, offers:{price, priceCurrency, availability}};
          snippets.push('<script type="application/ld+json">' + JSON.stringify(item).replaceAll("<","\\u003c") + "</script>");
          break;
        }
      } catch {}
    }
  }
  if (snippets.length) return {status:"ok", html:snippets.join("").slice(0, 1_500_000), url: location.href};
  const text = (document.body?.innerText || "").slice(0, 4000);
  const blocked = document.querySelector("iframe[src*='captcha'], .g-recaptcha, .h-captcha, form[action*='auth.mercadolivre']") ||
    /verifique se você é humano|confirme que você é humano|verificação de segurança|verifique sua identidade|access denied|hubo un error accediendo/i.test(text);
  return {status:blocked ? "verification" : "empty"};
}

async function navigate(url) {
  if (!allowed(url)) return {status:"error"};
  const tab = await chrome.tabs.create({url, active:false});
  try {
    for (let attempt = 0; attempt < 25; attempt++) {
      const current = await chrome.tabs.get(tab.id);
      if (current.status === "complete" && current.url) {
        if (!allowed(current.url)) return {status:"verification"};
        const result = await chrome.scripting.executeScript({target:{tabId:tab.id}, func:capture});
        const data = result[0]?.result;
        if (data && data.status !== "empty") return data;
      }
      await delay(650);
    }
    return {status:"empty"};
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function tick() {
  if (running) return;
  running = true;
  try {
    for (let i = 0; i < 4; i++) {
      const {job} = await request("next");
      if (!job) break;
      let result;
      try { result = await navigate(job.url); }
      catch { result = {status:"error"}; }
      await request("result", {id:job.id, ...result});
      if (result.status === "verification") break;
    }
  } catch {
    // The local application may be closed.
  } finally { running = false; }
}

async function start() {
  if (!await chrome.alarms.get("vigia")) await chrome.alarms.create("vigia", {periodInMinutes:0.5});
  await tick();
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "vigia") void tick(); });
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message === "consultar" && sender.url?.startsWith("http://127.0.0.1:8765/")) void tick();
});
chrome.runtime.onInstalled.addListener(() => void start());
chrome.runtime.onStartup.addListener(() => void start());
