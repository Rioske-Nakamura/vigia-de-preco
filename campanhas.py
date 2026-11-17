import json
import re
from datetime import datetime, time, timezone
from urllib.parse import urlsplit

from lojas import ProviderError, STORES, cents, json_request, matches, request, store_link, words

AWIN_ADVERTISER = 17729

def date_value(value, end=False):
    if not value:
        return None
    value = str(value)
    if len(value) == 10:
        day = datetime.fromisoformat(value).date()
        return datetime.combine(day, time.max if end else time.min, tzinfo=timezone.utc).timestamp()
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()

def code_value(value):
    return str(value or "").strip().upper()[:80]

def campaign(store, row):
    products = row.get("products", [])
    if not isinstance(products, list):
        raise ValueError("Campo products inválido.")
    products = [
        dict(title=str(p.get("title") or "")[:260], sku=str(p.get("sku") or "")[:80],
             url=store_link(store, p.get("url")), price=cents(p.get("price")),
             coupon_price=cents(p.get("coupon_price")))
        for p in products if isinstance(p, dict)
    ]
    return dict(store=store, code=code_value(row["code"]), key=str(row.get("id") or row["code"])[:170],
                title=str(row.get("title") or "")[:260], description=str(row.get("description") or "")[:800],
                terms=str(row.get("terms") or "")[:800], url=store_link(store, row.get("url")),
                starts=date_value(row.get("starts")), expires=date_value(row.get("expires"), end=True),
                products=products, minimum=cents(row.get("minimum")), source=row.get("source", "Feed de cupons"))

def awin_campaigns(cfg):
    pages = []
    page_size = 100
    for page in range(1, 101):
        payload = {"filters": {"advertiserIds": [AWIN_ADVERTISER], "membership": "joined", "type": "voucher",
                               "status": "active", "regionCodes": ["BR"]},
                   "pagination": {"page": page, "pageSize": page_size}}
        base = f"https://api.awin.com/publisher/{cfg['AWIN_PUBLISHER_ID']}/promotions"
        raw = request(base, headers={"Authorization": "Bearer " + cfg["AWIN_API_TOKEN"],
                                     "Content-Type": "application/json"}, data=json.dumps(payload).encode())
        payload = json.loads(raw)
        rows = payload.get("data", payload.get("promotions")) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderError("A Awin não devolveu a lista de cupons esperada.")
        if any(not isinstance(row, dict) for row in rows):
            raise ProviderError("Registro de cupom inválido na Awin.")
        pages.extend(rows)
        if len(rows) < page_size:
            break
    else:
        raise ProviderError("A consulta da Awin excedeu o limite de páginas, tente novamente mais tarde.")
    for row in pages:
        voucher = row.get("voucher") or {}
        advertiser = row.get("advertiser") or {}
        if not voucher.get("code") or str(advertiser.get("id")) != str(AWIN_ADVERTISER):
            continue
        yield campaign("kabum", {"code": voucher["code"], "id": row.get("promotionId"), "title": row.get("title"),
                    "description": row.get("description"), "terms": row.get("terms"), "url": row.get("url"),
                    "starts": row.get("startDate"), "expires": row.get("endDate"), "source": "Awin"})

def sources(cfg):
    feeds = cfg.get("COUPON_FEEDS") or {}
    if not isinstance(feeds, dict):
        feeds = {}
    result = {store: (store, address) for store, address in feeds.items() if store in STORES and isinstance(address, str) and address}
    if re.fullmatch(r"\d+", str(cfg.get("AWIN_PUBLISHER_ID", ""))) and cfg.get("AWIN_API_TOKEN"):
        result["awin"] = ("kabum", None)
    return result

def fetch(cfg, source, store, address):
    if source == "awin":
        return list(awin_campaigns(cfg))
    p = urlsplit(address)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ProviderError("O feed de cupons precisa de um endereço HTTPS.")
    data = json_request(address)
    if not isinstance(data.get("coupons"), list):
        raise ProviderError("O feed precisa conter uma lista de cupons em coupons.")
    return [campaign(store, row) for row in data["coupons"]
            if isinstance(row, dict) and code_value(row.get("code"))]

def product_key(store, url):
    url = store_link(store, url)
    if not url:
        return ""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    patterns = {"meli": r"(?:^|[/\-])(MLB-?\d+)(?:[/\-]|$)", "amazon": r"/(?:dp|gp/product)/([a-z0-9]{10})(?:/|$)",
                "kabum": r"/produto/(\d+)(?:/|$)"}
    exact = re.search(patterns[store], path, re.I)
    return exact.group(1).lower().replace("-", "") if exact else ""

def matched_products(c, watch, offers):
    store = c["store"]
    by_url = {product_key(store, offer["url"]): offer for offer in offers if offer["store"] == store}
    by_url.pop("", None)
    listed = []
    for p in c["products"]:
        key = product_key(store, p["url"])
        sku_match = bool(p["sku"] and words(p["sku"]) == words(watch["query"]))
        if sku_match or key in by_url or matches(p["title"], watch):
            listed.append({**p, "title": p["title"] or watch["name"]})
    if listed or c["products"]:
        return listed, "listed"
    key = product_key(store, c["url"])
    if key in by_url:
        o = by_url[key]
        return [dict(title=o["title"], url=o["url"], price=o["price"], coupon_price=None)], "related"
    if matches(c["title"] + " " + c["description"], watch):
        return [dict(title=watch["name"], url=c["url"], price=None, coupon_price=None)], "related"
    return [], "related"
