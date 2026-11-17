import csv
import gzip
import io
import json
import os
import re
import time
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import meli_publico

STORES = {"meli": "Mercado Livre", "amazon": "Amazon", "kabum": "KaBuM!"}
DOMAINS = {"meli": "mercadolivre.com.br", "amazon": "amazon.com.br", "kabum": "kabum.com.br"}

class ProviderError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

def url_for(store, query):
    if store == "meli":
        product = meli_publico.product_url(query, "https://www.mercadolivre.com.br") if query.startswith("https://") else ""
        if product:
            return product
    q = urllib.parse.quote("-".join(query.lower().split()) if store == "meli" else query, safe="")
    return {
        "meli": f"https://lista.mercadolivre.com.br/{q}",
        "amazon": f"https://www.amazon.com.br/s?k={q}",
        "kabum": f"https://www.kabum.com.br/busca/{q}",
    }[store]

def store_link(store, url):
    try:
        p = urllib.parse.urlsplit(str(url))
        host = p.hostname or ""
        domain = DOMAINS[store]
        if p.scheme == "https" and not p.username and not p.password and (
            host == domain or host.endswith("." + domain)
        ):
            return p._replace(fragment="").geturl()
    except ValueError:
        pass
    return ""

def words(value):
    s = unicodedata.normalize("NFKD", str(value).lower())
    return " ".join(re.findall(r"[a-z]+|[0-9]+", "".join(c for c in s if not unicodedata.combining(c))))

def matches(title, watch):
    normalized = " " + words(title) + " "
    return (all(" " + term + " " in normalized for term in words(watch["query"]).split())
            and not any(" " + words(term) + " " in normalized
                        for term in watch.get("exclude", "").split(",") if words(term)))

def cents(value):
    try:
        if isinstance(value, str):
            value = value.strip().replace("R$", "").strip()
            if "," in value:
                value = value.replace(".", "").replace(",", ".")
        number = round(float(value) * 100)
        return number if 0 <= number <= 100_000_000_00 else None
    except (ValueError, TypeError, OverflowError):
        return None

def request(url, *, headers=None, data=None, limit=8_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "VigiaPrecoLocal/1.0", **(headers or {})}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as result:
            raw = result.read(limit + 1)
        if len(raw) > limit:
            raise ProviderError("Resposta acima do limite da fonte.")
        if raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as z:
                raw = z.read(limit + 1)
            if len(raw) > limit:
                raise ProviderError("Arquivo descompactado acima do limite da fonte.")
        return raw
    except urllib.error.HTTPError as e:
        raise ProviderError({401: "Credencial inválida ou expirada.", 403: "A fonte recusou o acesso.",
                             429: "Limite de consultas da fonte atingido."}.get(e.code, f"Resposta HTTP {e.code} da fonte."), status=e.code) from None
    except (OSError, ValueError):
        raise ProviderError("Falha de conexão ou formato da resposta.") from None

def json_request(url, **kwargs):
    try:
        data = json.loads(request(url, **kwargs))
    except (ValueError, UnicodeError):
        raise ProviderError("A fonte retornou um JSON inválido.") from None
    if not isinstance(data, dict):
        raise ProviderError("Formato de resposta inesperado.")
    return data

def query_url(base, **params):
    return base + "?" + urllib.parse.urlencode(params)

def make_offer(store, key, title, url, price, **extra):
    return dict(store=store, key=str(key), title=str(title)[:260], url=store_link(store, url),
                price=price, cash=None, freight=None, freight_cep="", installments=None,
                installment_value=None, old=None, seller="", verified=[], note="") | extra

class Providers:
    def __init__(self, cfg, directory):
        self.cfg = cfg
        self.tokens_file = Path(directory) / "meli-tokens.json"
        self.feed_cache = None
        self.meli_token = None
        self.locks = {store: threading.Lock() for store in STORES}
        self.shopping_lock = threading.Lock()
        self.shopping_cache = {}
        self.public_cache = {}
        self.public_retry_at = 0

    def native_enabled(self, store):
        return bool({
            "meli": self.cfg.get("MELI_ACCESS_TOKEN") or (
                self.cfg.get("MELI_CLIENT_ID") and self.cfg.get("MELI_CLIENT_SECRET")
                and (self.cfg.get("MELI_REFRESH_TOKEN") or self.tokens_file.is_file())
            ),
            "amazon": self.cfg.get("KEEPA_KEY"),
            "kabum": self.cfg.get("KABUM_AWIN_CSV"),
        }[store])

    def enabled(self, store):
        return store == "meli"

    def scan(self, store, watch, settings):
        if store != "meli":
            return []
        with self.locks[store]:
            return self.meli_public(watch)

    def meli_public(self, watch):
        url = url_for("meli", watch["query"])
        detail = not url.startswith("https://lista.mercadolivre.com.br/")
        now = time.monotonic()
        cached = self.public_cache.get(url)
        if cached and cached[0] > now:
            rows = cached[1]
        else:
            if now < self.public_retry_at:
                raise ProviderError("O Mercado Livre recusou consultas automáticas deste computador. Abra a busca na loja ou tente mais tarde.")
            try:
                html = request(url, headers={"Accept": "text/html", "Accept-Language": "pt-BR,pt;q=0.9"})
            except ProviderError as exc:
                if exc.status in (403, 429):
                    self.public_retry_at = now + 900
                    raise ProviderError("O Mercado Livre recusou a consulta direta. Abra a busca na loja ou tente mais tarde.") from None
                raise
            try:
                rows = self.parse_meli(html.decode("utf-8", errors="replace"), url, detail)
            except ProviderError as exc:
                if exc.status == 403:
                    self.public_retry_at = now + 900
                raise
            if len(self.public_cache) >= 60:
                self.public_cache.pop(next(iter(self.public_cache)))
            self.public_cache[url] = (now + 180, rows)
        return self.meli_offers(rows, watch, detail)

    def parse_meli(self, html, url, detail):
        rows = meli_publico.parse(html, url, detail)
        if rows is None:
            if meli_publico.verification_page(html):
                raise ProviderError("O Mercado Livre mostrou uma tela de verificação. Abra a busca no navegador.", status=403)
            raise ProviderError("A página veio sem ofertas legíveis. Abra a busca no navegador e importe a página de resultados.")
        return rows

    def meli_offers(self, rows, watch, detail, imported=False):
        offers = {}
        for row in rows:
            if not detail and not matches(row["title"], watch):
                continue
            fields = {k: v for k, v in row.items() if k not in ("title", "url", "price")}
            shipping_text = row.get("shipping_text", "")
            shipping = words(shipping_text)
            if not any(term in shipping for term in ("acima de", "a partir", "meli", "primeira compra")):
                if "frete gratis" in shipping or re.search(r"\bchegara .*gratis\b", shipping):
                    fields["shipping_estimate"] = 0
                else:
                    advertised = re.fullmatch(r"frete\s+" + meli_publico.MONEY, shipping_text, re.I)
                    if advertised:
                        fields["shipping_estimate"] = meli_publico.price(advertised[1])
            offers.setdefault(row["url"], make_offer("meli", row["url"], row["title"], row["url"], row["price"],
                provider="meli_public", note=("Página salva no navegador. Confira se o preço continua válido." if imported else
                    "Preço exibido na página pública do Mercado Livre."), **fields))
        return list(offers.values())[:20]

    def scan_native(self, store, watch, settings):
        method = {"meli": self.meli, "amazon": self.amazon, "kabum": self.kabum}[store]
        found = method(watch, settings)
        return list({o["key"]: o for o in found if o["url"] and o["price"] and o["title"]}.values())[:15]

    def shopping(self, store, watch):
        query = watch["query"]
        with self.shopping_lock:
            key = query.casefold()
            old = self.shopping_cache.get(key)
            if old and old[0] > time.monotonic():
                rows = old[1]
            else:
                url = query_url("https://serpapi.com/search.json", engine="google_shopping",
                                q=query, gl="br", hl="pt", google_domain="google.com.br",
                                direct_link="true", api_key=self.cfg["SERPAPI_KEY"])
                data = json_request(url)
                if data.get("error"):
                    raise ProviderError("O Google Shopping não pôde concluir a busca. Verifique a chave ou a cota da SerpApi.")
                rows = data.get("shopping_results", [])
                if not isinstance(rows, list):
                    raise ProviderError("Resposta inesperada do Google Shopping.")
                if len(self.shopping_cache) >= 100:
                    self.shopping_cache.pop(next(iter(self.shopping_cache)))
                self.shopping_cache[key] = (time.monotonic() + 300, rows)
        results = {}
        for row in rows:
            title = row.get("title") or ""
            if not matches(title, watch) or row.get("second_hand_condition"):
                continue
            price_text = str(row.get("price", ""))
            if not re.match(r"^R\$\s*[\d.,]+\s*$", price_text):
                continue
            price = cents(row.get("extracted_price")) or cents(price_text)
            if not price:
                continue
            raw_link = row.get("direct_link") or row.get("link") or ""
            direct = store_link(store, raw_link)
            named = {
                "meli": ("mercado livre", "mercadolivre"),
                "amazon": ("amazon", "amazon com br", "amazon brasil"),
                "kabum": ("kabum", "kabum com br"),
            }[store]
            if not direct:
                if any(store_link(other, raw_link) for other in STORES if other != store):
                    continue
                if words(row.get("source", "")) not in named:
                    continue
            google = row.get("immersive_product_page_link") or row.get("product_link") or ""
            if not re.match(r"^https://(?:www\.)?google\.com(?:\.br)?/shopping/", google):
                google = query_url("https://www.google.com.br/search", tbm="shop", q=query)
            old_price = str(row.get("old_price") or "")
            previous = cents(old_price) if old_price.startswith("R$") else None
            shipping_text = str(row.get("delivery") or "").strip()[:115]
            installment_text = str(row.get("installment") or "").strip()[:115]
            note = "Preço indexado pelo Google Shopping. Confira na loja."
            if shipping_text:
                note += " " + shipping_text + " (frete anunciado)."
            if installment_text:
                note += " " + installment_text + " (parcelamento anunciado)."
            item_key = direct or str(row.get("product_id") or title)[:300]
            offer = make_offer(store, item_key, title, direct, price, old=previous,
                               note=note, external=not bool(direct), provider="shopping")
            if not direct:
                offer["url"] = google
            results.setdefault(item_key, offer)
        return list(results.values())[:15]

    def meli_headers(self):
        cfg = self.cfg
        can_refresh = cfg.get("MELI_CLIENT_ID") and cfg.get("MELI_CLIENT_SECRET")
        if not can_refresh or not (cfg.get("MELI_REFRESH_TOKEN") or self.tokens_file.is_file()):
            return {"Authorization": "Bearer " + cfg["MELI_ACCESS_TOKEN"]}
        if self.meli_token is None and self.tokens_file.is_file():
            try:
                self.meli_token = json.loads(self.tokens_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.meli_token = None
        if not self.meli_token or self.meli_token["expires"] <= time.time() + 60:
            refresh = self.meli_token["refresh_token"] if self.meli_token else cfg.get("MELI_REFRESH_TOKEN")
            if not refresh:
                raise ProviderError("Configure um refresh token válido.")
            result = json_request("https://api.mercadolibre.com/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urllib.parse.urlencode(dict(grant_type="refresh_token", client_id=cfg["MELI_CLIENT_ID"],
                    client_secret=cfg["MELI_CLIENT_SECRET"], refresh_token=refresh)).encode())
            if not result.get("access_token") or not result.get("refresh_token"):
                raise ProviderError("O Mercado Livre não devolveu os tokens esperados.")
            self.meli_token = dict(access_token=result["access_token"], refresh_token=result["refresh_token"],
                                   expires=time.time() + result.get("expires_in", 21600))
            tmp = self.tokens_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
                json.dump(self.meli_token, f)
            tmp.replace(self.tokens_file)
        return {"Authorization": "Bearer " + self.meli_token["access_token"]}

    def meli(self, watch, settings):
        base = "https://api.mercadolibre.com"
        headers = self.meli_headers()
        field = "product_identifier" if re.fullmatch(r"\d{8,14}", watch["query"]) else "q"
        data = json_request(query_url(base + "/products/search", status="active", site_id="MLB",
                                      limit=15, **{field: watch["query"]}), headers=headers)
        if not isinstance(data.get("results"), list):
            raise ProviderError("O catálogo do Mercado Livre retornou um formato inesperado.")
        rows = [row for row in data["results"] if re.fullmatch(r"MLB\d+", str(row.get("id", "")))
                and (field == "product_identifier" or matches(row.get("name", ""), watch))][:8]
        cep = settings["cep"]

        def product(row):
            detail = json_request(base + "/products/" + row["id"], headers=headers)
            item = detail.get("buy_box_winner") or {}
            if item.get("currency_id") != "BRL" or item.get("condition") not in (None, "new"):
                return None
            key = str(item.get("item_id", ""))
            if not re.fullmatch(r"MLB\d+", key):
                return None
            freight = None
            if cep:
                try:
                    quote = json_request(query_url(base + "/items/" + key + "/shipping_options", zip_code=cep),
                                         headers=headers)
                    costs = [cents(opt.get("cost")) for opt in quote.get("options", [])
                             if opt.get("shipping_option_type") == "address"
                             and opt.get("currency_id", "BRL") == "BRL"]
                    freight = min((cost for cost in costs if cost is not None), default=None)
                except ProviderError:
                    pass
            return make_offer("meli", key, detail.get("name") or row["name"],
                detail.get("permalink") or "https://produto.mercadolivre.com.br/MLB-" + key[3:],
                cents(item.get("price")), freight=freight, freight_cep=cep if freight is not None else "",
                seller=str(item.get("seller_id") or ""), note="Preço do catálogo. Confirme as condições na sua conta.")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(product, rows))
        return [o for o in results if o]

    def amazon(self, watch, settings):
        base = "https://api.keepa.com/"
        query = watch["query"]
        exact = re.fullmatch(r"(?:B[A-Z0-9]{9}|[0-9]{9}[0-9X])", query.upper())
        args = {"key": self.cfg["KEEPA_KEY"], "domain": 12, "stats": 30, "history": 0}
        path = "product" if exact else "search"
        args.update({"asin": query.upper()} if exact else {"type": "product", "term": query})
        data = json_request(query_url(base + path, **args))
        if data.get("error") or not isinstance(data.get("products"), list):
            raise ProviderError("O Keepa não devolveu a lista de produtos esperada.")
        results = []
        for item in data["products"]:
            asin, title = item.get("asin", ""), item.get("title", "")
            if not re.fullmatch(r"[A-Z0-9]{10}", asin) or not (exact or matches(title, watch)):
                continue
            current = (item.get("stats") or {}).get("current") or []
            price = next((current[i] for i in (0, 1) if len(current) > i and isinstance(current[i], int)
                          and current[i] > 0), None)
            if price is not None:
                ts = item.get("lastUpdate")
                timestamp = time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime((ts + 21_564_000) * 60)) if isinstance(ts, int) and 0 < ts < 30_000_000 else ""
                results.append(make_offer("amazon", asin, title, "https://www.amazon.com.br/dp/" + asin,
                    price, note=("Dados Keepa · " + timestamp if timestamp else "Dados Keepa") + ". Frete e Prime no checkout."))
        return results

    def kabum(self, watch, settings):
        if not self.feed_cache or time.monotonic() >= self.feed_cache[0]:
            url = self.cfg["KABUM_AWIN_CSV"]
            if not url.startswith("https://productdata.awin.com/"):
                raise ProviderError("Use o link do Create-a-Feed em productdata.awin.com.")
            raw = request(url, limit=64_000_000)
            content = raw.decode("utf-8-sig")
            sample = content.partition("\n")[0]
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            if not reader.fieldnames or "product_name" not in reader.fieldnames or not (
                {"search_price", "price"} & set(reader.fieldnames)
            ):
                raise ProviderError("Use o CSV legado da Awin com nome, preço e link direto da KaBuM.")
            normalized = []
            for row in reader:
                title = row.get("product_name") or ""
                link = store_link("kabum", row.get("merchant_deep_link") or row.get("deep_link"))
                price = cents(row.get("search_price") or row.get("price"))
                if not title or not link or not price or row.get("currency") not in (None, "", "BRL"):
                    continue
                if str(row.get("in_stock", "")).strip().lower() in ("0", "no", "false", "out_of_stock"):
                    continue
                old = cents(row.get("store_price") or row.get("old_price") or row.get("rrp_price"))
                normalized.append(make_offer("kabum", link, title, link, price, old=old if old and old > price else None,
                                             note="Catálogo KaBuM via Awin. Consulte Pix, frete e parcelas na loja."))
            self.feed_cache = (time.monotonic() + 900, normalized)
        return [o for o in self.feed_cache[1] if matches(o["title"], watch)][:15]
