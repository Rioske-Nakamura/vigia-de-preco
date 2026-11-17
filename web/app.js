"use strict";

const STORES = { meli: "Mercado Livre", amazon: "Amazon", kabum: "KaBuM!" };
const CATEGORIES = ["Celulares", "Impressão 3D", "Mangás e livros", "Peças de PC", "Outros"];
const EXAMPLES = [
  ["Galaxy S25", "Celulares", "capa, película, usado"],
  ["Galaxy S26 FE", "Celulares", "capa, película, usado"],
  ["Anycubic Kobra X", "Impressão 3D", "bico, hotend, reposição"],
  ["Mangá One Piece", "Mangás e livros", "poster, pôster"],
  ["SSD NVMe 1 TB", "Peças de PC", "case, adaptador"],
];
const ICONS = ["▯", "▧", "▤", "⌘", "◈"];
const LABELS = { painel: "Visão geral", cupons: "Cupons", alertas: "Alertas", ajustes: "Configurações" };
const content = document.querySelector("#content");
const dialog = document.querySelector("#dialog");
const dialogContent = document.querySelector("#dialog-content");
const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const date = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });
const cur = n => n == null ? "—" : brl.format(n / 100);
const stamp = n => n ? date.format(new Date(n * 1000)) : "—";
const reais = n => n == null ? "" : (n / 100).toFixed(2).replace(".", ",");
const esc = v => String(v ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const safe = url => /^https:\/\//.test(url || "") ? esc(url) : "#";
const badge = (s, style="") => `<span class="pill ${style}">${esc(s)}</span>`;
const shop = s => badge(STORES[s], "store-" + s);
const icon = (glyph, extra="") => `<span class="tile-icon ${extra}" aria-hidden="true">${glyph}</span>`;
const link = (url, label) => `<a href="${safe(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ›</a>`;
const button = (action, label, style="secondary", data="") => `<button type="button" class="${style}" data-action="${action}" ${data}>${label}</button>`;

let state, timer, renderRequest = 0, appliedRequest = 0, lastSnapshot;
let page = "painel";
let filter = "";
let category = "Todas";
let payment = "preferred";
let pending = false;
let searchText = "";
let searchPending = false;
let searchResult = null;
let searchError = "";
const expanded = new Set();

async function api(action, data) {
  if (action === "search" || action === "scan") {
    window.postMessage("vigia-consultar", location.origin);
    setTimeout(() => window.postMessage("vigia-consultar", location.origin), 350);
  }
  const response = await fetch("/api/" + action, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Falha ao acessar o painel.");
  return result;
}

function notify(message) {
  const toast = document.querySelector("#message");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(timer);
  timer = setTimeout(() => toast.hidden = true, 3900);
}

async function refresh(force=false) {
  const request = ++renderRequest;
  const next = await api("state");
  if (request < appliedRequest) return;
  appliedRequest = request;
  const serialized = JSON.stringify(next);
  const changed = serialized !== lastSnapshot;
  lastSnapshot = serialized;
  state = next;
  updateHeader();
  if ((changed || force) && (force || (page !== "ajustes" && !dialog.open && !searchPending))) render();
}

function watchStatus(id, offer) {
  if (offer.source === "manual") return "Registro manual";
  if (offer.source === "import") return "Página salva · importada em";
  if (!state.sources[offer.store]) return "Fonte desativada";
  if (!offer.active) return "Fora da última busca";
  return state.scans[id]?.[offer.store]?.state === "error" ? "Última consulta falhou" : "Consulta automática";
}

function active(id, o) {
  return o.source !== "import" && o.active && state.watches[id].stores.includes(o.store) &&
    (o.source === "manual" || (state.sources[o.store] && state.scans[id]?.[o.store]?.state === "ok"));
}

function browserStatus() {
  if (!state.browser_connected) return badge("Extensão desconectada", "warn");
  const version = state.browser_diagnostics?.version;
  if (!version) return badge("Extensão antiga · recarregue", "warn");
  return badge("Navegador conectado · " + version, "ok");
}

function browserLastResult() {
  const result = state.browser_diagnostics?.last_result;
  if (!result) return "Nenhuma consulta recebida da extensão ainda.";
  const status = {ok:"Página recebida", empty:"Página sem preços legíveis", error:"Falha ao abrir a página",
    unreadable:"Anúncios ainda sem leitura", verification:"Verificação do Mercado Livre", timeout:"Consulta sem resposta"}[result.status];
  const stage = {background:"aba em segundo plano", foreground:"janela em primeiro plano", url:"endereço",
    opening:"abertura da aba", focus:"ativação da janela", redirect:"redirecionamento",
    response:"aguardando resposta", waiting:"aguardando a extensão"}[result.stage];
  return [stamp(result.time), status, stage].filter(Boolean).join(" · ");
}

function updateHeader() {
  const count = state.alerts.filter(a => !a.read).length;
  const status = document.querySelector("#browser-connection");
  if (status) status.innerHTML = browserStatus();
  const last = document.querySelector("#browser-last-result");
  if (last) last.textContent = browserLastResult();
  document.querySelector("#nav-alerts").innerHTML = count ? badge(count, "ok") : "";
  document.querySelector("#local-status").textContent = state.running ? "Consultando lojas…" : "Em execução neste computador";
  document.querySelector(".local .dot").classList.remove("off");
  const b = document.querySelector("#scan-all");
  b.disabled = state.running || pending;
  b.innerHTML = state.running ? '<span aria-hidden="true">↻</span> Consultando…' : '<span aria-hidden="true">↻</span> Verificar agora';
}

function metrics() {
  const w = Object.values(state.watches).filter(o => o.active).length;
  const a = state.alerts.filter(o => !o.read).length;
  return `<section class="metrics" aria-label="Resumo">${[
    ["⌖", "Vigias ativas", w], ["≡", "Loja monitorada", "Mercado Livre"], ["♧", "Alertas novos", a],
  ].map(([i, label, value]) => `<div class="metric"><i aria-hidden="true">${i}</i><span><small>${label}</small><strong>${value}</strong></span></div>`).join("")}</section>`;
}

function dashboard() {
  const count = Object.keys(state.watches).length;
  return `<section class="hero">
    <div class="hero-copy">
      <span class="chip"><span class="dot"></span> Monitoramento local · a cada ${state.settings.interval} min</span>
      <h1>Busque e compare preços.</h1>
      <p>Veja as ofertas do Mercado Livre ou cole o link de um anúncio.</p>
      <form id="product-search" class="hero-search" role="search">
        <input name="query" aria-label="Produto para buscar" placeholder="Ex.: Galaxy S25 256 GB" required maxlength="400" value="${esc(searchText)}">
        <button class="light" ${searchPending ? "disabled" : ""}>${searchPending ? "Buscando…" : "Buscar preços"}</button>
      </form>
    </div>
    <div class="radar" aria-hidden="true"><i></i><i></i><i></i></div>
  </section>
  ${searchPanel()}
  ${metrics()}
  <section aria-labelledby="watched">
    <div class="section-head"><div><h2 id="watched">Minhas vigias</h2><p>${count ? "Abra um produto para comparar os valores e o histórico." : "Cadastre um produto ou comece por uma das sugestões."}</p></div>
      ${button("new-watch", "+ Nova vigia", "primary")}</div>
    ${count ? `<div class="tools">
      <input id="filter" aria-label="Filtrar vigias" placeholder="Filtrar minhas vigias" value="${esc(filter)}">
      <label><select id="category-filter" aria-label="Filtrar por categoria">${options(["Todas", ...new Set(Object.values(state.watches).map(w => w.category))], category)}</select></label>
      <label><select id="payment-filter" aria-label="Ordenar as ofertas por pagamento">${options({preferred:"Preferência da vigia",cash:"Menor total à vista",card:"Menor total parcelado"}, payment)}</select></label>
    </div><div id="watch-list">${watchList()}</div>` : `<div class="card empty">
      <div class="examples">${EXAMPLES.map(([title, cat], i) => button("example", icon(ICONS[CATEGORIES.indexOf(cat)]) + `<span>${esc(title)}<small>${cat}</small></span>`, "example", `data-index="${i}"`)).join("")}</div>
    </div>`}
    <div class="sourcebar">${shop("meli")} <span>Consulta à página pública</span></div>
  </section>`;
}

function searchPanel() {
  if (searchPending) return '<section id="search-results" class="card search-results" aria-live="polite">Consultando o Mercado Livre…</section>';
  if (searchError) return `<section id="search-results" class="card search-results" role="alert">${esc(searchError)}</section>`;
  if (!searchResult) return "";
  const {query, scans, links, offers} = searchResult;
  const rows = offers.slice().sort((a,b) => (
    Number(b.cash.total != null) - Number(a.cash.total != null) ||
    (a.cash.total ?? a.cash.base) - (b.cash.total ?? b.cash.base)
  ));
  const status = scans.meli;
  const resultsText = status.state === "error" ? status.message : rows.length
    ? `${rows.length} oferta(s) na página ${status.imported ? "salva do" : "do"} Mercado Livre.` : "Nenhuma oferta compatível nesta página.";
  return `<section id="search-results" class="card search-results" aria-labelledby="results-title">
    <div class="section-head"><div><h2 id="results-title">${esc(query)}</h2><p>${esc(resultsText)}</p></div>
      ${button("watch-search","+ Colocar em vigia","primary")}</div>
    <div class="source-line">${shop("meli")} ${link(links.meli, "Abrir no Mercado Livre")}</div>
    ${status.state === "error" && !state.browser_connected ? '<p class="hint"><a href="/guia#extensao">Ativar a busca automática pelo navegador ›</a></p>' : ""}
    ${status.state === "error" ? importControl() : ""}
    ${rows.length ? `<div class="table-box"><table class="table"><thead><tr><th>Oferta</th><th>À vista + frete</th><th>Parcelado + frete</th></tr></thead><tbody>
      ${rows.map(o => `<tr><td><div class="offer-name"><strong>${esc(o.title)}</strong></div>${shop(o.store)}
          ${o.old > o.price ? badge("De " + cur(o.old), "ok") : ""}<small>${esc(o.note)}</small>
          ${link(o.url, o.external ? "Ver no Google Shopping" : "Abrir anúncio")}</td><td>${payCell(o,"cash")}</td><td>${payCell(o,"card")}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
  </section>`;
}

function importControl(id="") {
  return `<details class="meli-import"><summary>Importar página salva</summary>
    <p class="hint">Abra o link do Mercado Livre acima. Na aba da loja, com os preços visíveis, pressione Ctrl+S e escolha “Página da Web, somente HTML”. Volte ao Vigia e selecione o arquivo.</p>
    <label class="field">Arquivo HTML do Mercado Livre <input type="file" class="meli-file" data-watch-id="${id}" accept=".html,.htm,text/html"></label>
    <p class="hint meli-import-status" role="status"></p>
  </details>`;
}

function watchList() {
  const rows = Object.entries(state.watches).filter(([,w]) => (category === "Todas" || category === w.category) &&
    (w.name + " " + w.query).toLocaleLowerCase("pt-BR").includes(filter.toLocaleLowerCase("pt-BR")));
  return rows.length ? rows.map(([id,w]) => watchRow(id,w)).join("") : '<div class="card empty"><p>Nenhuma vigia corresponde aos filtros.</p></div>';
}

function watchRow(id, w) {
  const m = payment === "preferred" ? w.mode : payment;
  const offers = (state.offers[id] || []).filter(o => w.stores.includes(o.store));
  const current = offers.filter(o => active(id, o)).sort((a,b) => (
    (a[m].total ?? Infinity) - (b[m].total ?? Infinity) || (a[m].base ?? Infinity) - (b[m].base ?? Infinity)
  ));
  const imported = offers.filter(o => o.source === "import" && o.active);
  const history = offers.filter(o => !active(id, o) && !(o.source === "import" && o.active)).sort((a,b) => b.time-a.time);
  const matching = w.target == null ? current : current.filter(o => ["final", "provisional"].includes(o.target_match.kind))
    .sort((a,b) => (a.target_match.kind === "provisional") - (b.target_match.kind === "provisional") ||
      a.target_match.amount - b.target_match.amount);
  const other = w.target == null ? [] : current.filter(o => !["final", "provisional"].includes(o.target_match.kind));
  const first = w.target == null ? current.find(o => o[m].total != null) : matching[0];
  const firstPrice = w.target == null ? first?.[m].total : first?.target_match.amount;
  const firstLabel = w.target == null ? "menor total " + (m === "cash" ? "à vista" : "parcelado") :
    first?.target_match.kind === "provisional" ? "meta estimada" : "dentro da meta";
  const tags = `data-id="${id}"`;
  return `<details class="watch card" data-watch="${id}" ${expanded.has(id) ? "open" : ""}>
  <summary>${icon(ICONS[CATEGORIES.indexOf(w.category)] || "◈")}
    <span class="name"><strong>${esc(w.name)}</strong><small>${esc(w.category)} · Meta ${w.target == null ? "livre" : cur(w.target)} · ${w.mode === "cash" ? "à vista" : "parcelado"}${w.target == null ? "" : w.target_scope === "product" ? " · sem frete" : " · com frete"}</small></span>
    ${badge(w.active ? "Ativa" : "Pausada", w.active ? "ok" : "warn")}
    <span class="amount">${firstPrice != null ? cur(firstPrice) : "—"}<small>${first ? firstLabel : w.target == null ? "total pendente" : "sem oferta na meta"}</small></span>
  </summary>
  <div class="watch-body"><div class="actions">
    ${button("scan-one","↻ Atualizar","secondary small",tags)}
    ${button("manual","+ Registrar oferta","primary small",tags)}
    ${button("edit-watch","Editar","secondary small",tags)}
    ${button("toggle",w.active?"Pausar":"Retomar","secondary small",tags)}
    ${button("delete","Excluir","danger small",tags)}
  </div>
  ${w.stores.filter(s => s === "meli").map(s => {
    const log = state.scans[id]?.[s];
    const desc = !state.sources[s] ? "Consulta automática pausada nesta loja" : !log ? "Aguardando consulta" :
      log.state === "ok" ? `${log.count} resultado(s) · ${stamp(log.time)}` : log.message + " · " + stamp(log.time);
    return `<div class="source-line">${shop(s)} ${link(w.links[s], "Abrir no Mercado Livre")} <span>${esc(desc)}</span></div>`;
  }).join("")}
  ${w.stores.includes("meli") ? importControl(id) : ""}
  <h3>Comparação de ofertas</h3>
  ${matching.length ? offerTable(id,matching) : `<p class="hint">${current.length ? "Nenhuma oferta atende ao limite de preço definido para esta vigia." : "Sem ofertas atuais."}</p>`}
  ${other.length ? `<details><summary>Ofertas acima da meta ou sem preço comparável (${other.length})</summary>${offerTable(id,other)}</details>` : ""}
  ${imported.length ? `<h3>Ofertas da página importada</h3><p class="hint">Preços da página salva. Confira se continuam válidos na loja.</p>${offerTable(id,imported)}` : ""}
  ${history.length ? `<details><summary>Resultados anteriores e indisponíveis (${history.length})</summary>${offerTable(id,history)}</details>` : ""}
  </div></details>`;
}

function payCell(o, mode) {
  const p = o[mode];
  if (p.base == null) return "<span class='dim'>Não informado</span>";
  const origin = mode === "cash" ? (p.advertised_coupon ? "Preço anunciado com cupom" : o.cash != null ? "À vista informado" : "Preço do anúncio") :
    o.installment_value != null ? `${o.installments}× de ${cur(o.installment_value)} antes de descontos` : `Total anunciado em ${o.installments}×`;
  const display = p.total != null ? cur(p.total) : p.estimated_total != null ? cur(p.estimated_total) : cur(p.subtotal) + " + frete";
  const shipping = p.estimated_total != null ? `<small>${badge("Estimativa", "warn")} ${p.shipping_estimate === 0 ? "Frete grátis anunciado" : "Frete anunciado " + cur(p.shipping_estimate)} · confirme no CEP</small>` :
    o.shipping_text ? `<small>${esc(o.shipping_text)} · confira na loja</small>` : "";
  const coupon = o.public_coupon && mode === "cash" ? `<small>${badge(o.coupon_code || "Cupom anunciado", "warn")} confira no checkout</small>` : "";
  const simulation = p.sim_codes.length ? `<small>${badge("Simulação", "warn")} ${p.simulation == null ? "Desconto de " + cur(p.sim_discount) : cur(p.simulation)} · ${esc(p.sim_codes.join(" + "))}</small>` : "";
  return `<strong>${display}</strong>
  <small>${esc(origin)}${p.freight == null ? "" : " · " + (p.freight ? "Frete " + cur(p.freight) : "Frete grátis")}${p.freight != null && o.meli_freight ? " · Meli+" : ""}</small>
  ${p.codes.length ? `<small>${badge("Cupom confirmado", "ok")} ${esc(p.codes.join(" + "))} · −${cur(p.discount)}</small>` : ""}${simulation}${shipping}${coupon}`;
}

function offerTable(id, offers) {
  return `<div class="table-box"><table class="table"><thead><tr>
    <th>Oferta</th><th>À vista + frete</th><th>Parcelado + frete</th><th>Registro</th>
  </tr></thead><tbody>${offers.map(o => `<tr><td><div class="offer-name">${o.external ? esc(o.title) + "<br>" + link(o.url,"Ver no Google Shopping") : link(o.url,o.title)}</div>${shop(o.store)}
    ${active(id,o) && o.target_match.kind === "provisional" ? badge("Meta estimada", "warn") : ""}
    ${o.old > o.price ? badge("De " + cur(o.old), "ok") : ""}${o.seller ? `<small>Vendedor: ${esc(o.seller)}</small>` : ""}
    ${o.note ? `<small>${esc(o.note)}</small>` : ""}
    </td><td>${payCell(o,"cash")}</td><td>${payCell(o,"card")}</td>
    <td><small>${esc(watchStatus(id,o))} · ${stamp(o.time)}</small>
    <div class="actions">${button("history","Histórico","secondary small",`data-id="${id}" data-key="${esc(o.id)}"`)}
    ${o.source === "manual" ? button("edit-offer","Editar","secondary small",`data-id="${id}" data-key="${esc(o.id)}"`) : o.external ? "" : button("copy-offer","Registrar checkout","secondary small",`data-id="${id}" data-key="${esc(o.id)}"`)}</div></td></tr>`).join("")}</tbody></table></div>`;
}

function heading(title, description, action="") {
  return `<div class="page-head"><div><h1>${title}</h1><p>${description}</p></div>${action}</div>`;
}

function couponSources() {
  const sources = Object.entries(state.coupon_sources);
  return `<section class="card campaign-sources">
    <div class="section-head"><div><h2>Vigia de cupons</h2>
      <p>Compara os produtos das campanhas com suas vigias ativas.</p></div>
      ${sources.length && !state.coupons_running ? button("scan-coupons","↻ Verificar cupons","secondary") : ""}
    </div>
    ${state.coupons_running ? '<p class="hint">Consultando campanhas…</p>' : ""}
    ${sources.length ? sources.map(([key,store]) => {
      const result = state.coupon_checks[key];
      const message = result?.state === "ok" ? `${result.count} cupom(ns) · ${stamp(result.time)}` :
        result?.message || "Aguardando consulta";
      return `<div class="source-line">${shop(store)} <span>${key === "awin" ? "Awin" : "Feed de cupons"} · ${esc(message)}</span></div>`;
    }).join("") : '<p class="hint">O painel verifica os cupons exibidos nos anúncios durante a consulta das vigias do Mercado Livre.</p>'}
    <div class="source-line"><a href="/guia#cupons-auto" target="_blank" rel="noopener">Como funciona ›</a>
      <a href="#alertas">Ver alertas de correspondência ›</a></div>
  </section>`;
}

function coupons() {
  const rows = Object.entries(state.coupons);
  const localDay = new Intl.DateTimeFormat("sv-SE", {timeZone:"America/Cuiaba", year:"numeric", month:"2-digit", day:"2-digit"}).format();
  return heading("Cupons", "Simule descontos e registre os códigos aceitos no checkout.", button("coupon","+ Novo cupom","primary")) + couponSources() +
    (rows.length ? `<div class="coupon-grid">${rows.map(([id,c]) => `<article class="card coupon-card">${shop(c.store)}
      ${c.expiry && c.expiry < localDay ? badge("Expirado", "warn") : ""}
      ${c.meli_plus ? badge("Meli+", "ok") : ""}<h2>${esc(c.code)}</h2>
      <div class="value">${c.kind === "percent" ? (c.value/100).toLocaleString("pt-BR") + "%" : cur(c.value)} OFF</div>
      <p>${c.minimum ? "Mínimo " + cur(c.minimum) + " · " : ""}${c.cap != null ? "Teto " + cur(c.cap) + " · " : ""}
      ${c.watch ? esc(state.watches[c.watch]?.name || "") : "Todas as vigias da loja"}<br>
      ${c.expiry ? "Até " + c.expiry.split("-").reverse().join("/") : "Validade em aberto"}${c.group ? " · Grupo " + esc(c.group) : ""}</p>
      <div class="actions">${button("coupon","Editar","secondary small",`data-id="${id}"`)} ${button("remove-coupon","Excluir","danger small",`data-id="${id}"`)}</div>
    </article>`).join("")}</div>` : '<div class="card empty"><h3>Nenhum cupom cadastrado</h3><p>Adicione o código, o desconto e as regras exibidas na loja.</p></div>');
}

function alerts() {
  return heading("Alertas", "Avisos de preço-alvo e cupons relacionados aos produtos em vigia.",
    state.alerts.some(a => !a.read) ? button("read-alerts","Marcar como lidos","secondary") : "") +
    (state.alerts.length ? `<div class="alert-list">${state.alerts.map(a => a.kind === "coupon" ?
      `<article class="card alert-row">${icon("◇")}
      <div class="info"><strong>${esc(a.name)} · Cupom ${esc(a.code)}</strong> ${a.read ? "" : badge("Novo","ok")}
      ${badge(a.source === "Mercado Livre" ? "Cupom exibido no anúncio" : a.match === "listed" ? "Produto na lista da campanha" : "Possível correspondência", a.match === "listed" ? "ok" : "warn")}
      <p>${esc(a.title)} · ${esc(a.source)} · ${stamp(a.time)}${a.expires ? " · Até " + stamp(a.expires) : ""}</p>
      ${a.minimum ? `<p>Compra mínima de ${cur(a.minimum)}</p>` : ""}
      ${a.terms ? `<p>${esc(a.terms)}</p>` : ""}
      <details><summary>${a.products.length} produto(s) relacionado(s)</summary><ul>${a.products.map(p =>
        `<li>${p.url ? link(p.url,p.title) : esc(p.title)}${p.price != null ? " · Anunciado " + cur(p.price) : ""}
        ${p.coupon_price != null ? " · Com cupom " + cur(p.coupon_price) : ""}</li>`).join("")}</ul></details>
      </div>${link(a.url,STORES[a.store])}</article>` :
      `<article class="card alert-row">${icon("↘","green")}
      <div class="info"><strong>${esc(a.name)} · ${cur(a.price)}</strong> ${a.read ? "" : badge("Novo","ok")} ${a.note ? badge("Provisório", "warn") : ""}
      <p>${esc(a.title)} · ${a.mode === "cash" ? "À vista" : "Parcelado"}${a.target_scope === "product" ? " · sem frete" : ""} · ${stamp(a.time)}${a.note ? " · " + esc(a.note) : ""}</p></div>
      ${link(a.url, a.external ? "Google Shopping" : STORES[a.store])}</article>`).join("")}</div>` :
      '<div class="card empty"><h3>Nenhum alerta ainda</h3><p>Defina um preço-alvo ou aguarde os cupons anunciados nos itens em vigia.</p></div>');
}

function settings() {
  const s = state.settings;
  return heading("Configurações", "Defina o CEP de referência e o intervalo da vigia.") + `
  <div class="card form-card"><h2>Entrega e monitoramento</h2>
    <form id="settings-form" data-submit="settings">
    <div class="form-grid">
      ${field("CEP de entrega","cep",s.cep,{hint:"Referência para os valores de checkout que você registrar.",placeholder:"00000-000",max:9})}
      ${field("Intervalo em minutos","interval",s.interval,{type:"number",min:15,max:1440,required:true,hint:"Entre 15 e 1.440 minutos."})}
      <div class="wide">${check("meli_plus","Tenho Meli+",s.meli_plus)}</div>
    </div><p class="form-error" role="alert"></p><div class="form-footer"><button class="primary">Salvar ajustes</button></div></form>
  </div>
  <div class="form-section"><h2>Mercado Livre</h2>
    <span id="browser-connection">${browserStatus()}</span>
    <p class="hint" id="browser-last-result">${esc(browserLastResult())}</p>
    <p class="hint"><a href="/guia#extensao">Instalar a extensão para Chrome ou Edge ›</a></p>
    <p class="hint">Última rodada: ${stamp(state.last_run)}. Mantenha o computador ligado e o servidor em execução.</p>
  </div>`;
}

function options(values, selected) {
  return (Array.isArray(values) ? values.map(x => [x,x]) : Object.entries(values)).map(([v,name]) =>
    `<option value="${esc(v)}" ${String(selected) === String(v) ? "selected" : ""}>${esc(name)}</option>`).join("");
}

function field(label, name, value="", opts={}) {
  const attr = `name="${name}" ${opts.required ? "required" : ""}`;
  const input = opts.options ? `<select ${attr}>${options(opts.options,value)}</select>` :
    `<input ${attr} type="${opts.type || "text"}" ${opts.decimal ? 'inputmode="decimal"' : ""} value="${esc(value)}"
    ${opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : ""} ${opts.min != null ? `min="${opts.min}"` : ""}
    ${opts.max != null ? opts.type === "number" ? `max="${opts.max}"` : `maxlength="${opts.max}"` : ""}>`;
  return `<label class="field ${opts.wide ? "wide" : ""}"><span>${esc(label)}</span>${input}${opts.hint ? `<small>${esc(opts.hint)}</small>` : ""}</label>`;
}

function check(name, text, checked, value="yes") {
  return `<label class="check"><input type="checkbox" name="${name}" value="${esc(value)}" ${checked ? "checked" : ""}>${esc(text)}</label>`;
}

function hidden(name, value) {
  return `<input type="hidden" name="${name}" value="${esc(value)}">`;
}

function render() {
  if (!state) return;
  document.querySelector("#breadcrumb").textContent = "Painel / " + LABELS[page];
  document.querySelectorAll("[data-page]").forEach(n => {
    if (n.dataset.page === page) n.setAttribute("aria-current", "page");
    else n.removeAttribute("aria-current");
  });
  if (category !== "Todas" && !Object.values(state.watches).some(w => w.category === category)) category = "Todas";
  const focus = document.activeElement?.id;
  const searchFocus = document.activeElement?.form?.id === "product-search" && document.activeElement.name === "query";
  const searchCursor = searchFocus ? document.activeElement.selectionStart : null;
  const cursor = focus === "filter" ? document.activeElement.selectionStart : null;
  content.innerHTML = ({painel:dashboard, cupons:coupons, alertas:alerts, ajustes:settings}[page])();
  if (searchFocus) {
    const input = document.querySelector("#product-search input");
    if (input) { input.focus(); input.setSelectionRange(searchCursor, searchCursor); }
  }
  if (focus === "filter") {
    const input = document.querySelector("#filter");
    if (input) { input.focus(); input.setSelectionRange(cursor,cursor); }
  }
}

function showDialog(title, form, action, footer="Salvar") {
  dialogContent.innerHTML = `<div class="dialog-inner">
    <div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button type="button" data-action="close" aria-label="Fechar" class="secondary small">×</button></div>
    ${action ? `<form data-submit="${action}"><div class="form-grid">${form}</div><p class="form-error" role="alert"></p>
      <div class="form-footer">${button("close","Cancelar")}<button class="primary">${esc(footer)}</button></div></form>` : form}</div>`;
  if (!dialog.open) dialog.showModal();
  dialog.querySelector("input:not([type=hidden]),select")?.focus();
}

function watchForm(id, sample) {
  const w = state.watches[id] || {query:sample?.[0], category:sample?.[1], exclude:sample?.[2], mode:"cash", provisional:true, stores:Object.keys(STORES)};
  showDialog(id ? "Editar vigia" : "Nova vigia", `${hidden("id", id)}
    ${field("Produto ou código","query",w.query,{wide:true,required:true,placeholder:"Ex.: Samsung Galaxy S25 256 GB",hint:"Use o modelo, a capacidade ou o código do produto para refinar a busca."})}
    ${field("Nome no painel (opcional)","name",w.name,{placeholder:"Igual ao termo de busca"})}
    ${field("Categoria","category",w.category || "Outros",{options:CATEGORIES})}
    ${field("Excluir palavras (opcional)","exclude",w.exclude,{wide:true,placeholder:"capa, película, usado",hint:"Separe por vírgula."})}
    ${field("Preço-alvo em R$","target",reais(w.target),{decimal:true,placeholder:"2.500,00"})}
    ${field("Forma de pagamento da meta","mode",w.mode,{options:{cash:"À vista",card:"Parcelado · pelo total"}})}
    ${field("O limite considera","target_scope",w.target_scope || "delivery",{options:{delivery:"Preço + frete",product:"Só o preço do produto"}})}
    <div class="wide">${check("provisional","Mostrar e avisar sobre ofertas dentro da meta estimada quando faltar confirmar frete ou cupom",w.provisional)}</div>
    ${hidden("stores","meli")}
  `, "watch", id ? "Salvar vigia" : "Começar a vigiar");
}

function offerForm(id, key, copy=false) {
  const w = state.watches[id];
  const o = state.offers[id]?.find(x => x.id === key) || {};
  if (copy) key = "";
  const from = (name,label) => field(label,name,reais(o[name]),{decimal:true});
  showDialog(key ? "Editar oferta" : "Registrar oferta", `${hidden("id",id)}${hidden("offer_id",key)}
    ${field("Loja","store",o.store || w.stores[0],{options:Object.fromEntries(w.stores.map(s => [s,STORES[s]]))})}
    ${field("Vendedor (opcional)","seller",o.seller)}
    ${field("Título da oferta","title",o.title,{required:true,wide:true,placeholder:w.name})}
    ${field("Link do produto","url",o.url,{type:"url",required:true,wide:true,placeholder:"https://…"})}
    ${field("Preço do anúncio (R$)","price",reais(o.price),{required:true,decimal:true,hint:"Antes do cupom. Se houver um desconto público, use o preço já em promoção."})}
    ${from("old","Preço anterior (R$)")}
    ${from("cash","Preço à vista / Pix (R$)")}
    ${field("Frete (R$)","freight",o.freight_cep === state.settings.cep ? reais(o.freight) : "",{decimal:true,hint:state.settings.cep ? "CEP " + state.settings.cep + ". Use 0 para frete grátis." : "Use 0 para frete grátis ou deixe em branco quando incerto."})}
    ${field("Número de parcelas","installments",o.installments,{type:"number",min:2,max:48})}
    ${field("Valor de cada parcela (R$)","installment_value",reais(o.installment_value),{decimal:true,hint:"Antes do cupom e do frete, já com juros se houver."})}
    <div class="wide">${check("meli_freight","O frete informado inclui o benefício Meli+",o.meli_freight)}</div>
    ${field("Códigos aceitos no checkout","verified",o.verified?.join(", "),{wide:true,hint:"Cadastre os códigos em Cupons. Separe por vírgula. Marque cupons cumulativos apenas se a loja permitir a combinação."})}
    ${field("Observação","note",o.note,{wide:true,placeholder:"Ex.: pagamento em cartão da loja"})}
    ${key ? `<div class="wide">${button("remove-offer","Excluir registro","danger small",`data-id="${id}" data-key="${esc(key)}"`)}</div>` : ""}
  `, "offer");
}

function couponForm(id) {
  const c = state.coupons[id] || {};
  showDialog(id ? "Editar cupom" : "Novo cupom", `${hidden("id",id)}
    ${field("Código","code",c.code,{required:true,placeholder:"Ex.: OFERTA10"})}
    ${field("Loja","store",c.store || "meli",{options:{meli:"Mercado Livre"}})}
    ${field("Tipo de desconto","kind",c.kind || "percent",{options:{percent:"Porcentagem",fixed:"Valor fixo (R$)"}})}
    ${field("Desconto","value",reais(c.value),{required:true,decimal:true,placeholder:"Ex.: 10"})}
    ${field("Compra mínima (R$)","minimum",reais(c.minimum),{decimal:true})}
    ${field("Teto de desconto (R$)","cap",reais(c.cap),{decimal:true})}
    ${field("Validade","expiry",c.expiry,{type:"date"})}
    ${field("Pagamento aceito","mode",c.mode || "both",{options:{both:"À vista e parcelado",cash:"Somente à vista",card:"Somente parcelado"}})}
    ${field("Produto elegível","watch",c.watch || "",{wide:true,options:{"":"Todas as vigias da loja",...Object.fromEntries(Object.entries(state.watches).map(([k,w]) => [k,w.name]))}})}
    ${field("Grupo cumulativo (opcional)","group",c.group,{wide:true,hint:"Use o mesmo nome em cupons que a loja permite combinar. Em branco, o painel testa o código sozinho."})}
    <div class="wide">${check("meli_plus","Exclusivo para Meli+ (Mercado Livre)",c.meli_plus)}</div>
  `, "coupon");
}

document.addEventListener("click", async e => {
  const btn = e.target.closest("button");
  if (!btn || !state) return;
  if (btn.dataset.page) { location.hash = btn.dataset.page; return; }
  const { action, id, key, index } = btn.dataset;
  if (!action) return;
  try {
    switch (action) {
      case "new-watch": return watchForm("");
      case "watch-search": return watchForm("",[searchResult.query]);
      case "example": return watchForm("",EXAMPLES[Number(index)]);
      case "edit-watch": return watchForm(id);
      case "manual": return offerForm(id);
      case "edit-offer": return offerForm(id,key);
      case "copy-offer": return offerForm(id,key,true);
      case "coupon": return couponForm(id);
      case "close": return dialog.close();
      case "history": {
        const o = state.offers[id]?.find(x => x.id === key);
        if (!o) return;
        return showDialog("Histórico da oferta", `<p>${esc(o.title)}</p><table class="table"><thead><tr><th>Registro</th><th>Preço do anúncio</th></tr></thead>
          <tbody>${o.history.slice().reverse().map(h => `<tr><td>${stamp(h.time)}</td><td>${cur(h.price)}</td></tr>`).join("")}</tbody></table>`);
      }
      case "scan-coupons": {
        const result = await api("scan-coupons", {});
        notify(result.started ? "Consulta de cupons iniciada." : "Já há uma consulta em andamento.");
        await refresh(true);
        return;
      }
      case "scan":
      case "scan-one": {
        const sources = id ? state.watches[id].stores : [...new Set(Object.values(state.watches).filter(w => w.active).flatMap(w => w.stores))];
        if (!sources.length) return notify("Adicione ou retome uma vigia para consultar.");
        if (!sources.some(s => state.sources[s])) return notify("Edite a vigia para consultar no Mercado Livre.");
        pending = true;
        updateHeader();
        try {
          const result = await api("scan", id ? {id} : {});
          notify(result.started ? "Consulta iniciada." : "Uma consulta já está em andamento.");
          await refresh(true);
        } finally { pending = false; updateHeader(); }
        return;
      }
      case "delete":
        if (!confirm("Excluir a vigia e o histórico deste produto?")) return;
        break;
      case "remove-coupon":
        if (!confirm("Excluir este cupom?")) return;
        break;
      case "remove-offer":
        if (!confirm("Excluir esta oferta registrada e seu histórico?")) return;
        break;
      case "toggle":
      case "read-alerts":
        break;
      default: return;
    }
    await api(action, {id,offer_id:key});
    if (dialog.open) dialog.close();
    await refresh(true);
  } catch (error) { notify(error.message); }
});

document.addEventListener("submit", async e => {
  if (e.target.id === "product-search") {
    e.preventDefault();
    if (searchPending) return;
    const query = e.target.elements.query.value.trim();
    if (!query) return;
    searchText = query;
    searchPending = true;
    searchResult = null;
    searchError = "";
    render();
    try { searchResult = await api("search", {query}); }
    catch (error) { searchError = error.message; }
    finally {
      searchPending = false;
      if (page === "painel") render();
    }
    return;
  }
  const form = e.target.closest("form[data-submit]");
  if (!form) return;
  e.preventDefault();
  const data = new FormData(form);
  const body = Object.fromEntries(data);
  if (form.dataset.submit === "watch") body.stores = data.getAll("stores");
  const submit = form.querySelector('button:not([type=button])');
  const errors = form.querySelector(".form-error");
  errors.textContent = "";
  submit.disabled = true;
  try {
    const result = await api(form.dataset.submit, body);
    if (form.dataset.submit === "watch") { expanded.add(result.id); filter = ""; category = "Todas"; }
    if (dialog.open) dialog.close();
    await refresh(true);
    notify("Alteração salva.");
  } catch (error) { errors.textContent = error.message; }
  finally { submit.disabled = false; }
});

document.addEventListener("input", e => {
  if (e.target.name === "query" && e.target.form?.id === "product-search") searchText = e.target.value;
  if (e.target.id === "filter") {
    filter = e.target.value;
    document.querySelector("#watch-list").innerHTML = watchList();
  }
});
document.addEventListener("change", async e => {
  if (e.target.matches(".meli-file")) {
    const file = e.target.files[0];
    const watchId = e.target.dataset.watchId;
    if (!file || (!watchId && !searchResult) || searchPending) return;
    const status = e.target.closest(".meli-import").querySelector(".meli-import-status");
    if (file.size > 8_000_000) { status.textContent = "O arquivo deve ter até 8 MB."; return; }
    const query = watchId ? "" : searchResult.query;
    searchPending = true;
    e.target.disabled = true;
    status.textContent = "Lendo a página salva…";
    try {
      const result = await api("import-meli", {query, watch_id:watchId, html: await file.text()});
      searchPending = false;
      if (watchId) {
        expanded.add(watchId);
        await refresh(true);
        notify(`${result.offers.length} oferta(s) importada(s).`);
      } else {
        searchResult = result;
        if (page === "painel") render();
      }
    } catch (error) {
      status.textContent = error.message;
      e.target.value = "";
    } finally { searchPending = false; e.target.disabled = false; }
    return;
  }
  if (!["category-filter","payment-filter"].includes(e.target.id)) return;
  if (e.target.id === "category-filter") category = e.target.value;
  else payment = e.target.value;
  document.querySelector("#watch-list").innerHTML = watchList();
});
document.addEventListener("toggle", e => {
  // Toggle events queued by a removed element must not overwrite current UI state.
  if (!e.target.isConnected || !e.target.dataset?.watch) return;
  if (e.target.open) expanded.add(e.target.dataset.watch);
  else expanded.delete(e.target.dataset.watch);
}, true);
function route() {
  const target = location.hash.slice(1);
  page = Object.hasOwn(LABELS, target) ? target : "painel";
  render();
}
window.addEventListener("hashchange", route);
route();
refresh(true).catch(error => content.textContent = error.message);
setInterval(() => refresh().catch(() => {
  document.querySelector("#local-status").textContent = "Servidor indisponível";
  document.querySelector(".local .dot").classList.add("off");
}), 3000);
