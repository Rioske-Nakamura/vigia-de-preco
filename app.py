import argparse
import copy
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import campanhas
import meli_publico
from lojas import STORES, ProviderError, Providers, cents, make_offer, store_link, url_for, words

ROOT = Path(__file__).resolve().parent
try:
    BRAZIL = ZoneInfo("America/Cuiaba")
except ZoneInfoNotFoundError:
    BRAZIL = datetime.now().astimezone().tzinfo

def now():
    return time.time()

def today():
    return datetime.now(BRAZIL).date().isoformat()

def string(body, key, limit=160):
    return str(body.get(key) or "").strip()[:limit]

def money(body, key, required=False):
    raw = body.get(key)
    value = cents(raw)
    if (required and value is None) or (raw not in (None, "") and value is None):
        raise ValueError("Confira o valor do campo " + key + ".")
    return value

def number(raw, low, high):
    try:
        value = int(str(raw))
        if low <= value <= high:
            return value
    except ValueError:
        pass
    raise ValueError(f"Informe um número entre {low} e {high}.")

def configure(body):
    cep = string(body, "cep", 12).replace("-", "")
    if cep and not re.fullmatch(r"\d{8}", cep):
        raise ValueError("Informe um CEP com 8 dígitos.")
    return dict(cep=cep, meli_plus=bool(body.get("meli_plus")), interval=number(body.get("interval"), 15, 1440))

def watch_from(body):
    query = string(body, "query", 400)
    if not words(query):
        raise ValueError("Informe o produto que quer buscar.")
    stores = body.get("stores")
    if not isinstance(stores, list) or not stores or any(s not in STORES for s in stores):
        raise ValueError("Escolha pelo menos uma loja.")
    mode = body.get("mode")
    if mode not in ("cash", "card"):
        raise ValueError("Escolha à vista ou parcelado.")
    return dict(name=string(body, "name") or query, query=query, category=string(body, "category", 40) or "Outros",
                exclude=string(body, "exclude", 200), stores=["meli"],
                target=money(body, "target"), mode=mode, provisional=bool(body.get("provisional")))

def coupon_from(body, watches):
    code = string(body, "code", 45).upper()
    store = body.get("store")
    if not code or store not in STORES:
        raise ValueError("Informe o código e a loja do cupom.")
    value = money(body, "value", True)
    kind = body.get("kind")
    mode = body.get("mode")
    if kind not in ("percent", "fixed") or not value or (kind == "percent" and value > 10_000):
        raise ValueError("Confira o desconto do cupom.")
    if mode not in ("both", "cash", "card"):
        raise ValueError("Confira a forma de pagamento do cupom.")
    expiry = string(body, "expiry", 10)
    if expiry:
        try:
            datetime.strptime(expiry, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Data de validade inválida.") from None
    watch = string(body, "watch")
    if watch and watch not in watches:
        raise ValueError("Produto do cupom indisponível.")
    group = string(body, "group", 40).casefold()
    return dict(code=code, store=store, kind=kind, value=value, mode=mode,
                minimum=money(body, "minimum") or 0, cap=money(body, "cap"),
                watch=watch, expiry=expiry, group=group, meli_plus=store == "meli" and bool(body.get("meli_plus")))

def manual_from(body, cep):
    store = body.get("store")
    link = store_link(store, string(body, "url", 1000)) if store in STORES else ""
    title = string(body, "title", 260)
    price = money(body, "price", True)
    if not link or not price or not title:
        raise ValueError("Informe título, preço positivo e um link HTTPS da loja escolhida.")
    freight = money(body, "freight")
    n = number(body["installments"], 2, 48) if body.get("installments") else None
    per = money(body, "installment_value")
    if bool(n) != bool(per):
        raise ValueError("Informe a quantidade e o valor de cada parcela.")
    verified = [v.strip().upper() for v in string(body, "verified", 240).split(",") if v.strip()]
    return make_offer(store, link, title, link, price, cash=money(body, "cash"), freight=freight,
                      freight_cep=cep if freight is not None else "", installments=n, installment_value=per,
                      old=money(body, "old"), seller=string(body, "seller"), verified=verified,
                      note=string(body, "note", 220), meli_freight=store == "meli" and bool(body.get("meli_freight")))

def coupon_best(available, base, only=None):
    candidates = [(0, [])]
    groups = {}
    for c in available:
        if (only is None or c["code"] in only) and base >= c["minimum"]:
            candidates.append((discount(c, base), [c["code"]]))
            if c["group"]:
                groups.setdefault(c["group"], []).append(c)
    for group in groups.values():
        remaining = base
        codes = []
        for c in sorted(group, key=lambda x: (x["kind"] != "percent", x["code"])):
            if remaining >= c["minimum"]:
                d = discount(c, remaining)
                if d:
                    remaining -= d
                    codes.append(c["code"])
        candidates.append((base - remaining, codes))
    return max(candidates, key=lambda item: (item[0], -len(item[1])))

def discount(c, base):
    amount = c["value"] if c["kind"] == "fixed" else (base * c["value"] + 5000) // 10000
    return min(base, amount, c["cap"] if c["cap"] is not None else base)

def calc(offer, watch_id, mode, state):
    settings = state["settings"]
    if mode == "card":
        base = offer.get("card_total")
        if base is None and offer["installments"] and offer["installment_value"] is not None:
            base = offer["installments"] * offer["installment_value"]
    else:
        base = offer["cash"] if offer["cash"] is not None else offer["price"]
    if base is None:
        return dict(base=None, freight=None, total=None, simulation=None, discount=0, codes=[], sim_codes=[], sim_discount=0)
    freight = offer["freight"] if offer["freight_cep"] == settings["cep"] else None
    if offer.get("meli_freight") and not settings["meli_plus"]:
        freight = None
    advertised_coupon = offer.get("coupon_price", False) and mode == "cash"
    candidates = [c for c in state["coupons"].values()
                  if not advertised_coupon and c["store"] == offer["store"] and c["watch"] in ("", watch_id)
                  and c["mode"] in ("both", mode) and (not c["expiry"] or c["expiry"] >= today())
                  and (not c["meli_plus"] or settings["meli_plus"])]
    # Cupom incide no subtotal do produto, sem frete ou juros do cartão.
    product_base = min(base, offer["price"]) if mode == "card" else base
    confirmed, codes = coupon_best(candidates, product_base, offer["verified"])
    estimate, sim_codes = coupon_best(candidates, product_base)
    shipping_estimate = offer.get("shipping_estimate") if freight is None else None
    effective = None if freight is None or advertised_coupon else base + freight - confirmed
    return dict(base=base, freight=freight, total=effective, subtotal=base-confirmed, discount=confirmed, codes=codes,
                simulation=(base + freight - estimate if freight is not None else None) if estimate > confirmed else None,
                sim_discount=estimate if estimate > confirmed else 0, sim_codes=sim_codes if estimate > confirmed else [],
                advertised_coupon=advertised_coupon, shipping_estimate=shipping_estimate,
                estimated_total=None if shipping_estimate is None else base + shipping_estimate - confirmed)

class App:
    def __init__(self, directory, config):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "vigia.json"
        self.config_path = config
        self.cfg = json.loads(config.read_text(encoding="utf-8-sig")) if config.is_file() else {}
        self.providers = Providers(self.cfg, directory)
        self.lock = threading.RLock()
        self.running = False
        self.search_slots = threading.BoundedSemaphore(2)
        self.coupons_running = False
        self.coupon_cache = {}
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else dict(
            settings=dict(cep="", meli_plus=True, interval=60), watches={}, coupons={}, offers={}, scans={}, alerts=[], last_run=None)

        self.state.setdefault("coupon_checks", {})
        self.state.setdefault("coupon_seen", {})

    def trigger_coupons(self, force=True):
        with self.lock:
            sources = {key: src for key, src in campanhas.sources(self.cfg).items() if src[0] == "meli"}
            if force:
                due = sources
            else:
                interval = self.state["settings"]["interval"] * 60
                due = {key: item for key, item in sources.items()
                       if now() >= self.state["coupon_checks"].get(key, {}).get("time", 0) + interval}
            if self.coupons_running or not due:
                return False
            self.coupons_running = True
        threading.Thread(target=self.scan_coupons, args=(due,), daemon=True).start()
        return True

    def scan_coupons(self, sources):
        try:
            with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
                jobs = {key: pool.submit(campanhas.fetch, self.cfg, key, *source)
                        for key, source in sources.items()}
                for key, job in jobs.items():
                    try:
                        coupons = job.result()
                        status = dict(state="ok", count=len(coupons), time=now())
                    except (ProviderError, ValueError, TypeError, KeyError) as exc:
                        coupons = []
                        message = str(exc) if isinstance(exc, ProviderError) else "Formato de cupons inesperado na fonte."
                        status = dict(state="error", message=message, time=now())
                    with self.lock:
                        if status["state"] == "ok":
                            self.coupon_cache[key] = coupons
                            for coupon in coupons:
                                self.evaluate_campaign(coupon)
                        self.state["coupon_checks"][key] = status
                        self.commit()
        finally:
            with self.lock:
                self.coupons_running = False

    def evaluate_campaign(self, coupon, watch_id=None):
        current = now()
        if ((coupon["starts"] is not None and coupon["starts"] > current)
            or (coupon["expires"] is not None and coupon["expires"] < current)):
            return
        state = self.state
        store = coupon["store"]
        for wid, watch in state["watches"].items():
            if (watch_id and watch_id != wid) or not watch["active"] or store not in watch["stores"]:
                continue
            offers = [o for _, o in self.active_offers(wid)]
            products, match = campanhas.matched_products(coupon, watch, offers)
            if not products:
                continue
            dedupe = "|".join((store, coupon["code"], str(coupon["starts"] or coupon["expires"] or coupon["key"]), wid))
            # Reavalie listas atualizadas sem notificar de novo os mesmos itens.
            previous = state["coupon_seen"].get(dedupe, {"products": [], "listed": False})
            identities = {campanhas.product_key(store, p["url"]) or p.get("sku") or words(p["title"]) for p in products}
            new_products = identities - set(previous["products"])
            upgrade = match == "listed" and not previous["listed"]
            if not new_products and not upgrade:
                continue
            if previous["listed"] and match != "listed":
                continue
            state["coupon_seen"][dedupe] = {
                "products": sorted(set(previous["products"]) | identities),
                "listed": previous["listed"] or match == "listed",
            }
            target = coupon["url"] or next((p["url"] for p in products if p["url"]), url_for(store, watch["query"]))
            state["alerts"].insert(0, dict(kind="coupon", id=uuid4().hex, watch=wid, name=watch["name"],
                title=coupon["title"] or coupon["code"], code=coupon["code"], read=False, time=current,
                store=store, url=target, products=products, match=match, expires=coupon["expires"],
                minimum=coupon["minimum"], source=coupon["source"], terms=coupon["terms"]))
            del state["alerts"][200:]

    def evaluate_public_coupons(self, watch_id, offers):
        grouped = {}
        for offer in offers:
            if not offer.get("public_coupon"):
                continue
            code = offer.get("coupon_code", "")
            # Without a visible code, keep each listing separate.
            key = code or offer["url"]
            found = grouped.setdefault(key, {
                "id": "publico:" + key, "code": code or "Cupom anunciado", "source": "Mercado Livre",
                "title": "Cupom exibido no anúncio", "products": [],
            })
            found["products"].append({
                "title": offer["title"], "url": offer["url"],
                "price": None if offer.get("coupon_price") else offer["price"] / 100,
                "coupon_price": offer["price"] / 100 if offer.get("coupon_price") else None,
            })
        for data in grouped.values():
            self.evaluate_campaign(campanhas.campaign("meli", data), watch_id)

    def commit(self):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
            json.dump(self.state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)

    def active_offers(self, watch_id):
        state = self.state
        watch = state["watches"][watch_id]
        for record in state["offers"].get(watch_id, {}).values():
            o = record["history"][-1]
            if o["store"] in watch["stores"] and record["active"]:
                yield record, o

    def evaluate(self, watch_id):
        state = self.state
        watch = state["watches"][watch_id]
        options = {"final": [], "provisional": []}
        if watch["active"] and watch["target"] is not None:
            for _, o in self.active_offers(watch_id):
                if o["source"] != "manual":
                    status = state["scans"].get(watch_id, {}).get(o["store"], {})
                    if status.get("state") != "ok" or not self.providers.enabled(o["store"]):
                        continue
                p = calc(o, watch_id, watch["mode"], state)
                if p["total"] is not None and p["total"] <= watch["target"]:
                    options["final"].append((p["total"], o, ""))
                elif watch.get("provisional") and p["base"] is not None:
                    amount = p["simulation"] if p["freight"] is not None else p["subtotal"] + (p.get("shipping_estimate") or 0) - p["sim_discount"]
                    if amount is not None and amount <= watch["target"]:
                        pending = []
                        if p["freight"] is None:
                            pending.append("frete")
                        if p.get("advertised_coupon"):
                            pending.append("cupom anunciado")
                        if p["sim_codes"]:
                            pending.append("cupom " + " + ".join(p["sim_codes"]))
                        if pending:
                            options["provisional"].append((amount, o, "Conferir " + " e ".join(pending)))
        for kind in options:
            candidates = options[kind]
            flag = "under" if kind == "final" else "provisional_under"
            if candidates and not (kind == "provisional" and options["final"]) and not watch.get(flag):
                amount, offer, note = min(candidates, key=lambda x: x[0])
                state["alerts"].insert(0, dict(price=amount, name=watch["name"], title=offer["title"],
                    store=offer["store"], url=offer["url"], watch=watch_id, time=now(), mode=watch["mode"], external=offer.get("external", False),
                    id=uuid4().hex, read=False, note=note))
                del state["alerts"][200:]
            watch[flag] = bool(candidates)

    def save_offer(self, watch_id, offer, source, replace=None):
        history = self.state["offers"].setdefault(watch_id, {})
        key = replace or source + ":" + offer["store"] + ":" + offer["key"]
        record = history.setdefault(key, dict(active=True, history=[]))
        obs = {**offer, "time": now(), "source": source}
        record["active"] = True
        before = record["history"]
        if before and all(before[-1].get(field) == value for field, value in obs.items() if field != "time"):
            before[-1]["time"] = obs["time"]
        else:
            obs["first_seen"] = obs["time"]
            before.append(obs)
            del before[:-100]

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            state["offers"] = {}
            for w in state["watches"]:
                state["watches"][w]["links"] = {s: url_for(s, state["watches"][w]["query"]) for s in state["watches"][w]["stores"]}
                state["offers"][w] = [{"id": key, **record["history"][-1], "active": record["active"],
                    **{m: calc(record["history"][-1], w, m, state) for m in ("cash", "card")},
                    "history": [{"time": h.get("first_seen", h["time"]), "price": h["price"]} for h in record["history"]]}
                    for key, record in self.state["offers"].get(w, {}).items()]
            state["sources"] = {s: self.providers.enabled(s) for s in STORES}
            state["coupon_sources"] = {key: store for key, (store, _) in campanhas.sources(self.cfg).items() if store == "meli"}
            state["coupons_running"] = self.coupons_running
            state.pop("coupon_seen", None)
            state["running"] = self.running
            return state

    def query_store(self, store, watch, settings):
        try:
            offers = self.providers.scan(store, watch, settings)
            return offers, dict(state="ok", count=len(offers), time=now())
        except ProviderError as exc:
            return [], dict(state="error", message=str(exc), time=now())
        except Exception as exc:
            return [], dict(state="error", message="Formato inesperado da fonte (" + type(exc).__name__ + ").", time=now())

    def search(self, params, imported=False):
        query = string(params, "query", 400)
        if not words(query):
            raise ValueError("Informe o produto que quer buscar.")
        if not self.search_slots.acquire(blocking=False):
            raise ValueError("Já há buscas em andamento. Tente novamente quando finalizarem.")
        try:
            watch = dict(query=query, exclude=string(params, "exclude", 200))
            with self.lock:
                settings = self.state["settings"].copy()
                calculation = dict(settings=settings, coupons=copy.deepcopy(self.state["coupons"]))
            link = url_for("meli", query)
            if imported:
                html = params.get("html")
                if not isinstance(html, str) or not html or len(html) > 8_000_000:
                    raise ValueError("Escolha uma página HTML de até 8 MB.")
                detail = not link.startswith("https://lista.mercadolivre.com.br/")
                if detail:
                    # Bind a saved detail page to its advertised canonical listing.
                    canonical = next((n.attrs.get("href") for n in meli_publico.Page(html).root.walk()
                        if n.tag == "link" and n.attrs.get("rel") == "canonical"), None)
                    page_link = meli_publico.product_url(canonical, link) if canonical else ""
                    if not page_link or campanhas.product_key("meli", page_link) != campanhas.product_key("meli", link):
                        raise ValueError("O arquivo não identifica o mesmo anúncio da busca. Salve a página do anúncio correto.")
                try:
                    rows = self.providers.parse_meli(html, link, detail)
                except ProviderError as exc:
                    raise ValueError(str(exc)) from None
                offers = self.providers.meli_offers(rows, watch, detail, imported=True)
                status = dict(state="ok", time=now(), count=len(offers), imported=True)
            else:
                offers, status = self.query_store("meli", watch, settings)
            return {
                "query": query,
                "links": {"meli": url_for("meli", query)},
                "scans": {"meli": status},
                "offers": [{**o, **{m: calc(o, "", m, calculation) for m in ("cash", "card")}} for o in offers],
            }
        finally:
            self.search_slots.release()

    def trigger(self, watch_id=None, force=True):
        with self.lock:
            if self.running:
                return False
            if watch_id and watch_id not in self.state["watches"]:
                raise ValueError("Produto não encontrado.")
            self.running = True
        threading.Thread(target=self.scan, args=(watch_id, force), daemon=True).start()
        return True

    def scan(self, watch_id=None, force=True):
        try:
            with self.lock:
                interval = self.state["settings"]["interval"] * 60
                settings = self.state["settings"].copy()
                jobs = [(wid, w.copy()) for wid, w in self.state["watches"].items()
                        if (w["active"] or watch_id == wid) and (not watch_id or watch_id == wid)
                        and any(self.providers.enabled(s) for s in w["stores"])
                        and (force or now() >= w.get("last_attempt", 0) + interval)]
            for wid, w in jobs:
                for s in w["stores"]:
                    if not self.providers.enabled(s):
                        continue
                    offers, status = self.query_store(s, w, settings)
                    with self.lock:
                        current = self.state["watches"].get(wid)
                        if not current or current["rev"] != w["rev"] or self.state["settings"]["cep"] != settings["cep"]:
                            break
                        self.state["scans"].setdefault(wid, {})[s] = status
                        if status["state"] == "ok":
                            for rec in self.state["offers"].get(wid, {}).values():
                                last = rec["history"][-1]
                                if last["source"] == "auto" and last["store"] == s:
                                    rec["active"] = False
                            for o in offers:
                                self.save_offer(wid, o, "auto")
                            self.evaluate_public_coupons(wid, offers)
                        self.commit()
                with self.lock:
                    current = self.state["watches"].get(wid)
                    if current and current["rev"] == w["rev"] and self.state["settings"]["cep"] == settings["cep"]:
                        current["last_attempt"] = now()
                        self.state["last_run"] = now()
                        self.evaluate(wid)
                        for coupons in self.coupon_cache.values():
                            for coupon in coupons:
                                self.evaluate_campaign(coupon, wid)
                        self.commit()
        finally:
            with self.lock:
                self.running = False

    def scheduler(self):
        while True:
            with self.lock:
                s = self.state
                due = any(w["active"] and now() >= w.get("last_attempt", 0) + s["settings"]["interval"] * 60
                          and any(self.providers.enabled(store) for store in w["stores"]) for w in s["watches"].values())
            if due:
                self.trigger(force=False)
            self.trigger_coupons(force=False)
            time.sleep(5)

    def change(self, action, b):
        if action == "scan-coupons":
            return {"started": self.trigger_coupons()}
        if action in ("search", "import-meli"):
            return self.search(b, imported=action == "import-meli")
        if action == "scan":
            return {"started": self.trigger(b.get("id"))}
        with self.lock:
            s = self.state
            wid = string(b, "id")
            if action == "settings":
                settings = configure(b)
                if settings["cep"] != s["settings"]["cep"]:
                    for w in s["watches"].values():
                        w["last_attempt"] = 0
                s["settings"] = settings
            elif action == "watch":
                w = watch_from(b)
                if wid and wid not in s["watches"]:
                    raise ValueError("Produto não encontrado.")
                wid = wid or uuid4().hex
                previous = s["watches"].get(wid, {})
                if previous and any(previous[k] != w[k] for k in ("query", "exclude", "stores")):
                    s["scans"].pop(wid, None)
                    for record in s["offers"].get(wid, {}).values():
                        if record["history"][-1]["source"] == "auto":
                            record["active"] = False
                s["coupon_checks"].clear()
                s["watches"][wid] = {**w, "active": previous.get("active", True), "rev": uuid4().hex,
                                     "under": previous.get("under", False), "provisional_under": previous.get("provisional_under", False), "last_attempt": 0}
            elif action in ("toggle", "delete", "offer", "remove-offer"):
                if wid not in s["watches"]:
                    raise ValueError("Produto não encontrado.")
                w = s["watches"][wid]
                if action == "toggle":
                    w.update(active=not w["active"], last_attempt=0, rev=uuid4().hex)
                    if w["active"]:
                        s["coupon_checks"].clear()
                elif action == "delete":
                    s["watches"].pop(wid)
                    s["offers"].pop(wid, None)
                    s["scans"].pop(wid, None)
                    s["coupons"] = {k: c for k, c in s["coupons"].items() if c["watch"] != wid}
                    s["alerts"] = [a for a in s["alerts"] if a["watch"] != wid]
                    s["coupon_seen"] = {k: v for k, v in s["coupon_seen"].items() if not k.endswith("|" + wid)}
                else:
                    records = s["offers"].get(wid, {})
                    key = string(b, "offer_id", 1200)
                    if key and (key not in records or records[key]["history"][-1]["source"] != "manual"):
                        raise ValueError("Registro manual não encontrado.")
                    if action == "remove-offer":
                        records.pop(key, None)
                    else:
                        o = manual_from(b, s["settings"]["cep"])
                        if o["store"] not in w["stores"]:
                            raise ValueError("Ative esta loja na vigia do produto.")
                        if key and records[key]["history"][-1]["store"] != o["store"]:
                            raise ValueError("Para trocar a loja, adicione outra oferta.")
                        self.save_offer(wid, o, "manual", key)
            elif action == "coupon":
                cup = coupon_from(b, s["watches"])
                if any(k != wid and (c["code"], c["store"]) == (cup["code"], cup["store"]) for k, c in s["coupons"].items()):
                    raise ValueError("Já existe um cupom com esse código nesta loja.")
                s["coupons"][wid or uuid4().hex] = cup
            elif action == "remove-coupon":
                s["coupons"].pop(wid, None)
            elif action == "read-alerts":
                for a in s["alerts"]:
                    a["read"] = True
            else:
                raise ValueError("Ação desconhecida.")
            for key in s["watches"]:
                self.evaluate(key)
            self.commit()
            return {"id": wid}

FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
         "/style.css": ("style.css", "text/css"), "/guia": ("guia.html", "text/html")}

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, content, mime="application/json"):
        if mime == "application/json":
            content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.local():
            return self.reply(403, {"error": "Use o endereço local do painel."})
        path = urlsplit(self.path).path
        if path == "/api/state":
            return self.reply(200, self.server.app.snapshot())
        if path in FILES:
            file, mime = FILES[path]
            return self.reply(200, (ROOT / "web" / file).read_bytes(), mime)
        self.reply(404, {"error": "Página não encontrada."})

    def do_POST(self):
        host = self.headers.get("Host")
        if not self.local() or self.headers.get("Origin") not in (None, "http://" + str(host)):
            return self.reply(403, {"error": "Origem inválida."})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "Use JSON."})
        try:
            limit = 12_000_000 if self.path == "/api/import-meli" else 48_000
            length = number(self.headers.get("Content-Length"), 1, limit)
            b = json.loads(self.rfile.read(length))
            if not isinstance(b, dict) or not self.path.startswith("/api/"):
                raise ValueError("Pedido inválido.")
            result = self.server.app.change(self.path.removeprefix("/api/"), b)
            self.reply(200, result)
        except OSError:
            self.reply(500, {"error": "Não foi possível gravar na pasta do Vigia. Confira a permissão de escrita."})
        except (ValueError, TypeError, KeyError) as e:
            self.reply(400, {"error": str(e) if isinstance(e, ValueError) else "Confira os dados enviados."})

    def log_message(self, *_):
        pass

def main():
    parser = argparse.ArgumentParser(description="Vigia de Preço, painel local")
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--strictPort", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=ROOT / "dados")
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    args = parser.parse_args()
    app = App(args.data, args.config)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.app = app
        threading.Thread(target=app.scheduler, daemon=True).start()
        print(f"Vigia de Preço aberto em http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
