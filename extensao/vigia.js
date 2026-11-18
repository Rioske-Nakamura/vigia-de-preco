const base = "http://127.0.0.1:8765/api/browser-";
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let running = false;

function allowed(url) {
  try {
    const p = new URL(url);
    if (p.protocol !== "https:" || p.username || p.password || p.port) return false;
    if (p.hostname === "lista.mercadolivre.com.br") return /^\/(?:[^/]+\/){0,3}[^/]+\/?$/.test(p.pathname);
    return ["www.mercadolivre.com.br", "produto.mercadolivre.com.br", "mercadolivre.com.br"].includes(p.hostname) &&
      /(?:^|[/\-])MLB(?:U)?-?\d+(?:$|[/\-])/i.test(p.pathname);
  } catch { return false; }
}

async function request(action, data = {}, timeout = 4500) {
  const response = await fetch(base + action, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data),
    signal:AbortSignal.timeout(timeout),
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
  const explicitEmpty = Boolean(document.querySelector(".ui-search-no-results"));
  const priceSelector = ".ui-pdp-price__second-line, .poly-price__current, .ui-search-price__second-line";
  const priced = nodes.some(node => {
    if (node.matches(".ui-search-no-results")) return true;
    const price = node.matches(priceSelector) ? node : node.querySelector(priceSelector);
    return price && (price.matches("[aria-label*='reais']") ||
      price.querySelector(".andes-money-amount__fraction, [aria-label*='reais']") ||
      /R\$\s*\d/.test(price.textContent));
  });
  if (!priced) {
    const embedded = document.getElementById("__NORDIC_RENDERING_CTX__")?.textContent || "";
    const objectStart = embedded.indexOf("{");
    const objectEnd = embedded.lastIndexOf("}");
    if (objectStart >= 0 && objectEnd > objectStart) {
      try {
        const items = JSON.parse(embedded.slice(objectStart, objectEnd + 1)).appProps.pageProps.initialState.results;
        if (Array.isArray(items) && items.some(item => item?.polycard?.components?.some(c => c?.price?.current_price?.value > 0))) {
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

  const text = (document.body?.innerText || "").slice(0, 4000);
  const blocked = document.querySelector("iframe[src*='captcha'], .g-recaptcha, .h-captcha, form[action*='auth.mercadolivre']") ||
    /verifique se você é humano|confirme que você é humano|verificação de segurança|verifique sua identidade|access denied|hubo un error accediendo/i.test(text);
  if (blocked) return {status:"verification"};
  if (priced || snippets.length > nodes.slice(0, 80).length) {
    return {status:"ok", html:snippets.join("").slice(0, 1_500_000), url:location.href,
      noResults:explicitEmpty, complete:document.readyState === "complete", visible:document.visibilityState === "visible"};
  }
  return {status:"empty"};
}

async function navigate(job) {
  const {url, id} = job;
  if (!allowed(url)) return {status:"error", stage:"url"};
  let tab;
  let workWindow;
  let previousWindow;
  let stage = "opening";
  try {
    previousWindow = await chrome.windows.getLastFocused().catch(() => null);
    tab = await chrome.tabs.create({url, active:false});
    await chrome.tabs.update(tab.id, {autoDiscardable:false}).catch(() => {});
    stage = "background";
    const started = performance.now();
    let emptySince = null;
    let rejected = false;
    let lastCandidate;
    while (performance.now() - started < 75000) {
      const current = await chrome.tabs.get(tab.id);
      if (current.url && current.url !== "about:blank") {
        const host = new URL(current.url).hostname;
        if (host === "auth.mercadolivre.com.br") return {status:"verification", stage:"redirect"};
        // During a redirect the reported address can change before the DOM is ready.
        const result = allowed(current.url)
          ? await chrome.scripting.executeScript({target:{tabId:tab.id}, func:capture}).catch(() => [])
          : [];
        const data = result[0]?.result;
        if (data?.status === "verification") return {...data, stage};
        if (data?.status === "ok") {
          const visibleEmpty = data.noResults && data.complete && data.visible;
          emptySince = visibleEmpty ? (emptySince ?? performance.now()) : null;
          const settled = emptySince !== null && performance.now() - emptySince >= 22000;
          const changed = lastCandidate?.html !== data.html || lastCandidate?.url !== data.url || lastCandidate?.settled !== settled;
          if ((!data.noResults || settled) && changed) {
            lastCandidate = {html:data.html, url:data.url, settled};
            const result = await request("result", {id, status:"ok", html:data.html, url:data.url,
              stage, candidate:true, settled_empty:settled});
            if (result.accepted || result.continue === false) {
              return {status:result.status || "ok", delivered:true};
            }
            rejected = true;
          }
        } else {
          emptySince = null;
        }
      }
      if (!workWindow && performance.now() - started >= 6000) {
        workWindow = await chrome.windows.create({tabId:tab.id, focused:true, type:"normal"});
        if (workWindow?.id == null) return {status:"error", stage:"focus"};
        stage = "foreground";
        // Some window managers ignore focus during creation.
        await chrome.tabs.update(tab.id, {active:true});
        if (!workWindow.focused) await chrome.windows.update(workWindow.id, {focused:true});
      }
      await delay(650);
    }
    return {status:rejected ? "unreadable" : "empty", stage};
  } catch {
    return {status:"error", stage};
  } finally {
    const currentWindow = workWindow && await chrome.windows.get(workWindow.id).catch(() => null);
    if (tab) await chrome.tabs.remove(tab.id).catch(() => {});
    if (currentWindow?.focused && previousWindow?.id != null && previousWindow.id !== workWindow.id) {
      await chrome.windows.update(previousWindow.id, {focused:true}).catch(() => {});
    }
  }
}

async function tick() {
  if (running) return;
  running = true;
  try {
    for (let i = 0; i < 4; i++) {
      const {job} = await request("next", {wait:i === 0, version:chrome.runtime.getManifest().version}, 18000);
      if (!job) break;
      const result = await navigate(job);
      if (!result.delivered) await request("result", {id:job.id, ...result});
      if (result.status === "verification") break;
    }
  } catch {
    // The local application may be closed.
  } finally { running = false; }
}

async function ensureAlarm() {
  if (!await chrome.alarms.get("vigia")) await chrome.alarms.create("vigia", {periodInMinutes:0.5});
}

function start() {
  void ensureAlarm().catch(() => {});
  void tick();
}

chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "vigia") start(); });
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message === "consultar" && sender.url?.startsWith("http://127.0.0.1:8765/")) start();
});
chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
void ensureAlarm().catch(() => {});
