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

def book_query(query):
    normalized = words(re.sub(r"\s*\((?:portugu[eê]s|pt-br)\)\s*$", "", query, flags=re.I))
    volume = re.search(r"\b(?:vol(?:ume)?|edicao)\s+(\d+)\b", normalized)
    if not volume and re.search(r"\b(?:manga|livro|hq)\b", normalized):
        volume = re.search(r"\b(\d+)$", normalized)
    if not volume:
        return None
    stem = re.split(r"\s+[-–—]\s+|:", query, maxsplit=1)[0]
    stem = re.sub(r"\b(?:vol(?:ume)?|edição)\.?\s+\d+\b", "", stem, flags=re.I)
    parts = [word for word in words(stem).split() if word not in ("manga", "livro", "hq", "editora", "panini")]
    if parts and parts[-1].isdigit() and int(parts[-1]) == int(volume[1]):
        parts.pop()
    if not parts:
        return None
    return parts, int(volume[1])


def shopping_matches(title, watch):
    query = watch["query"]
    book = book_query(query)
    if book:
        stem, volume = book
        normalized = " " + words(title) + " "
        if not all(f" {term} " in normalized for term in stem):
            return False
        if re.search(r"\b(?:colecao|kit|combo|lote)\b|\b\d+\s+(?:ao?|ate)\s+\d+\b", normalized):
            return False
        volumes = re.findall(r"\b(?:vol(?:ume)?|edicao)\s+(\d+)\b", normalized)
        if volumes:
            valid = all(int(n) == volume for n in volumes)
        else:
            normalized_numbers = re.sub(r"(?<!\d)(?:97[89]\d{10}|\d{10})(?!\d)", "", normalized)
            valid = any(int(n) == volume for n in re.findall(r"\b\d+\b", normalized_numbers))
        return valid and not any(f" {words(term)} " in normalized
                                for term in watch.get("exclude", "").split(",") if words(term))
    normalized_title = re.sub(r"(?<!\d)0+(?=\d)", "", title)
    normalized_query = re.sub(r"(?<!\d)0+(?=\d)", "", query)
    return matches(normalized_title, {**watch, "query": normalized_query})


def indexed_web_rows(data):
    for row in data:
        if not isinstance(row, dict):
            continue
        link = row.get("link")
        if not store_link("meli", link) or not meli_publico.product_url(link, "https://www.mercadolivre.com.br"):
            continue
        rich = row.get("rich_snippet")
        if not isinstance(rich, dict):
            continue
        for section in ("top", "bottom"):
            block = rich.get(section)
            if not isinstance(block, dict):
                continue
            detected = block.get("detected_extensions") or {}
            price = cents(detected.get("price")) if isinstance(detected, dict) and detected.get("currency") in ("BRL", "R$") else None
            if price is None:
                extensions = block.get("extensions") or []
                amounts = [m[1] for v in extensions if isinstance(v, str)
                           and (m := re.fullmatch(r"R\$\s*([0-9.]+,[0-9]{2})", v.strip()))]
                price = cents(amounts[0]) if len(amounts) == 1 else None
            if price:
                amount, centavos = divmod(price, 100)
                yield {"title": row.get("title"), "link": link, "source": "Mercado Livre",
                       "price": f"R$ {amount},{centavos:02d}", "extracted_price": price / 100, "web": True}
                break


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

def request(url, *, headers=None, data=None, limit=8_000_000, include_url=False, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "VigiaPrecoLocal/1.0", **(headers or {})}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as result:
            raw = result.read(limit + 1)
            if include_url:
                final_url = result.geturl()
        if len(raw) > limit:
            raise ProviderError("Resposta acima do limite da fonte.")
        if raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as z:
                raw = z.read(limit + 1)
            if len(raw) > limit:
                raise ProviderError("Arquivo descompactado acima do limite da fonte.")
        return (raw, final_url) if include_url else raw
    except urllib.error.HTTPError as e:
        raise ProviderError({401: "Credencial inválida ou expirada.", 403: "A fonte recusou o acesso.",
                             429: "Limite de consultas da fonte atingido."}.get(e.code, f"Resposta HTTP {e.code} da fonte."), status=e.code) from None
    except TimeoutError:
        raise ProviderError("A fonte demorou mais que o limite da consulta.", status=504) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ProviderError("A fonte demorou mais que o limite da consulta.", status=504) from None
        raise ProviderError("Falha de conexão com a fonte.") from None
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
        urls = [url]
        book = book_query(watch["query"]) if not detail else None
        if book:
            terms, _ = book
            broad = url_for("meli", " ".join([*terms, "manga"]))
            if broad != url:
                urls.append(broad)
        for candidate in urls:
            try:
                rows = self.meli_public_rows(candidate, detail)
            except ProviderError as exc:
                if exc.status in (403, 429) or candidate == urls[-1]:
                    raise
                continue
            offers = self.meli_offers(rows, watch, detail)
            if offers:
                return offers
        return []

    def meli_public_rows(self, url, detail):
        now = time.monotonic()
        cached = self.public_cache.get(url)
        if cached and cached[0] > now:
            rows = cached[1]
        else:
            if now < self.public_retry_at:
                raise ProviderError("A consulta direta está em pausa após uma recusa do Mercado Livre.", status=403)
            try:
                html, final_url = request(url, headers={"Accept": "text/html", "Accept-Language": "pt-BR,pt;q=0.9"},
                                          include_url=True, timeout=25)
            except ProviderError as exc:
                if exc.status in (403, 429):
                    self.public_retry_at = now + 900
                    raise ProviderError(f"O Mercado Livre recusou a consulta direta (HTTP {exc.status}).",
                                        status=exc.status) from None
                raise
            html = html.decode("utf-8", errors="replace")
            if detail:
                source_url = meli_publico.product_url(final_url, url)
                if not meli_publico.same_listing(url, source_url):
                    if meli_publico.verification_page(html):
                        self.public_retry_at = now + 900
                        raise ProviderError("O Mercado Livre apresentou uma tela de verificação.", status=403)
                    raise ProviderError("A página aberta não confirmou o anúncio do link.")
            try:
                rows = self.parse_meli(html, url, detail)
            except ProviderError as exc:
                if exc.status == 403:
                    self.public_retry_at = now + 900
                raise
            if len(self.public_cache) >= 60:
                self.public_cache.pop(next(iter(self.public_cache)))
            self.public_cache[url] = (now + 180, rows)
        return rows

    def parse_meli(self, html, url, detail):
        rows = meli_publico.parse(html, url, detail)
        if rows is None:
            if meli_publico.verification_page(html):
                raise ProviderError("O Mercado Livre mostrou uma tela de verificação.", status=403)
            raise ProviderError("A página do Mercado Livre veio sem preços legíveis.")
        return rows

    def meli_offers(self, rows, watch, detail, imported=False):
        offers = {}
        for row in rows:
            if not detail and not shopping_matches(row["title"], watch):
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
        api_key = self.cfg.get("SERPAPI_KEY")
        if not api_key:
            raise ProviderError("Cadastre a chave da SerpApi em Configurações.")
        search_watch = watch
        exact = None
        listing = None
        if query.startswith("https://"):
            listing = meli_publico.product_url(query, "https://www.mercadolivre.com.br")
            if not listing:
                raise ProviderError("Pesquise pelo nome do produto ou informe um link de anúncio do Mercado Livre.")
            url_parts = urllib.parse.urlsplit(listing)
            exact = meli_publico.listing_key(listing)
            slug = [p for p in url_parts.path.strip("/").split("/") if p not in ("p", "up")]
            seed = re.sub(r"^MLB(?:U)?-?\d+-?", "", slug[0], flags=re.I).replace("-", " ").strip()
            query = seed or exact
            search_watch = dict(watch, query=query)
        first_query = f"{query} Mercado Livre"
        book = book_query(query)
        if book:
            terms, volume = book
            short = " ".join(terms) + f" volume {volume}"
        else:
            short = query
        domain_filter = "site:mercadolivre.com.br"
        if listing:
            alternate = re.sub(r"^(MLBU?)(\d+)$", r"\1-\2", exact)
            ids = " OR ".join(f'"{code}"' for code in dict.fromkeys((exact, alternate)))
            attempts = [
                ("google", f"{domain_filter} ({ids})"),
                ("google_shopping", first_query),
                ("google", f"{domain_filter} {short}"),
                ("google_shopping", short),
            ]
        else:
            attempts = [("google_shopping", first_query), ("google_shopping", short),
                        ("google", f"{domain_filter} {short}")]
            if book:
                edition = f"({volume} OR {volume:02d})" if volume < 10 else str(volume)
                attempts.append(("google", f"{domain_filter} {' '.join(terms)} mangá {edition}"))
        offers = {}
        for engine, term in dict.fromkeys(attempts):
            if len(offers) >= (1 if listing else 2):
                break
            try:
                rows = self.indexed_rows(engine, term, api_key)
            except ProviderError:
                if not offers:
                    raise
                break
            for identity, offer in self.shopping_offers(store, rows, search_watch, listing, query).items():
                offers.setdefault(identity, offer)
        return list(offers.values())[:15]

    def indexed_rows(self, engine, query, api_key):
        with self.shopping_lock:
            key = (api_key, engine, query.casefold())
            old = self.shopping_cache.get(key)
            if old and old[0] > time.monotonic():
                return old[1]
            url = query_url("https://serpapi.com/search.json", engine=engine, q=query, gl="br",
                            hl="pt", google_domain="google.com.br", api_key=api_key)
            for attempt in range(2):
                try:
                    data = json_request(url, timeout=30)
                    break
                except ProviderError as exc:
                    if attempt == 0 and exc.status in (500, 502, 503, 504):
                        continue
                    hints = {401:"Chave da SerpApi inválida. Confira em Configurações.",
                             403:"A SerpApi recusou a chave. Confira sua conta.",
                             429:"A SerpApi atingiu o limite de consultas.",
                             504:"A SerpApi excedeu o tempo de resposta nas duas tentativas."}
                    raise ProviderError(hints.get(exc.status, "A SerpApi não respondeu à consulta."), status=exc.status) from None
            error = data.get("error")
            if error and "hasn't returned any results" not in str(error).casefold():
                raise ProviderError("A SerpApi retornou um erro. Verifique a chave ou a cota.")
            rows = data.get("shopping_results" if engine == "google_shopping" else "organic_results", [])
            if not isinstance(rows, list):
                raise ProviderError("Resposta inesperada da SerpApi.")
            if engine == "google":
                rows = list(indexed_web_rows(rows))
            if len(self.shopping_cache) >= 100:
                self.shopping_cache.pop(next(iter(self.shopping_cache)))
            self.shopping_cache[key] = (time.monotonic() + 300, rows)
            return rows

    def shopping_offers(self, store, rows, watch, listing, query):
        results = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or ""
            if not isinstance(title, str) or not title or (listing is None and not shopping_matches(title, watch)) or row.get("second_hand_condition"):
                continue
            price_text = str(row.get("price", ""))
            if not re.match(r"^R\$\s*[\d.,]+\s*$", price_text):
                continue
            price = cents(row.get("extracted_price")) or cents(price_text)
            if not price:
                continue
            raw_link = row.get("direct_link") or row.get("link") or row.get("product_link") or ""
            direct = (meli_publico.product_url(raw_link, "https://www.mercadolivre.com.br")
                      if store == "meli" and store_link(store, raw_link) else store_link(store, raw_link))
            if listing and not meli_publico.same_listing(listing, direct):
                continue
            named = {
                "meli": ("mercado livre", "mercadolivre", "mercado livre com br", "mercadolivre com br"),
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
            installment = row.get("installment")
            installment_count = installment.get("period") if isinstance(installment, dict) else None
            installment_value = None
            if (type(installment_count) is int and 2 <= installment_count <= 48
                    and str(installment.get("price", "")).startswith("R$")):
                installment_value = cents(installment.get("extracted_price"))
            freight = 0 if words(shipping_text) in ("frete gratis", "entrega gratis", "entrega gratuita") else None
            source = "Busca do Google" if row.get("web") else "Google Shopping"
            note = f"Preço indexado pela fonte {source}. Confira o valor na loja."
            item_key = direct or str(row.get("product_id") or title)[:300]
            if store == "meli" and direct:
                item_key = meli_publico.listing_key(direct)
            offer = make_offer(store, direct or item_key, title, direct, price, old=previous,
                               note=note, external=not bool(direct), provider="shopping", indexed_source=source,
                               installments=installment_count if installment_value else None,
                               installment_value=installment_value, shipping_text=shipping_text,
                               shipping_estimate=freight)
            if not direct:
                offer["url"] = google
            results.setdefault(item_key, offer)
        return results

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
