"""Read the prices and terms explicitly displayed on Mercado Livre's public pages."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
MONEY = r"R\$\s*([0-9][0-9.]*,?[0-9]*)"

class Node:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.classes = (self.attrs.get("class") or "").split()
        self.children = []

    def find(self, *classes):
        for node in self.walk():
            if any(name in node.classes for name in classes):
                return node
        return None

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return " ".join(" ".join(self.parts()).split())

    def parts(self):
        for child in self.children:
            if isinstance(child, Node):
                if child.attrs.get("aria-hidden") != "true":
                    yield from child.parts()
            else:
                yield child

class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def price(value):
    s = str(value).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", s):
        return None
    integer, _, fraction = s.partition(".")
    return int(integer) * 100 + int(fraction.ljust(2, "0"))

def amount(node):
    if not node:
        return None
    for part in node.walk():
        label = part.attrs.get("aria-label") or ""
        found = re.search(r"([\d.]+)\s+reais(?:\s+(?:com|e)\s+(\d{1,2})\s+centavos)?", label, re.I)
        if found:
            return price(found[1] + ("," + found[2].zfill(2) if found[2] else ""))
    fraction = node.find("andes-money-amount__fraction")
    if fraction:
        coins = node.find("andes-money-amount__cents")
        return price(fraction.text() + ("," + coins.text().zfill(2) if coins else ""))
    found = re.search(MONEY, node.text())
    return price(found[1]) if found else None

def product_url(href, base):
    try:
        result = urlsplit(urljoin(base, href or ""))
        host = result.hostname or ""
        if (result.scheme == "https" and not result.username and not result.password
            and (host == "mercadolivre.com.br" or host.endswith(".mercadolivre.com.br"))
            and re.search(r"(?:^|[/\-])MLB-?\d+(?:$|[/\-])", result.path, re.I)):
            # Item/variation identifiers can select a different listing on the same catalog page.
            query = "&".join(part for part in result.query.split("&") if part.split("=", 1)[0] in ("item_id", "variation", "attributes"))
            fragment = result.fragment if result.fragment.startswith(("polycard_client=", "item_id=")) and "item_id=" in result.fragment else ""
            if "item_id=" in fragment:
                m = re.search(r"(?:^|&)item_id=(MLB\d+)(?:&|$)", fragment)
                if m and not any(p.startswith("item_id=") for p in query.split("&")):
                    query = "&".join(filter(None, (query, "item_id=" + m[1])))
            return urlunsplit(("https", host, result.path, query, ""))
    except ValueError:
        pass
    return ""

def row(node, page_url, detail=False):
    title = node.find("ui-pdp-title") if detail else node.find("poly-component__title")
    if not title:
        return None
    link = product_url(page_url if detail else title.attrs.get("href"), page_url)
    current = node.find("ui-pdp-price__second-line") if detail else node.find("poly-price__current")
    value = amount(current)
    if not value or not link:
        return None
    previous = amount(node.find("ui-pdp-price__original-value", "poly-price__original"))
    plan = node.find("ui-pdp-price__subtitles", "poly-price__installments")
    terms = current.text().casefold()
    shipping = node.find("poly-component__shipping", "ui-pdp-shipping")
    shipping_text = shipping.text()[:130] if shipping else ""
    cue = node.find("poly-component__coupon", "poly-component__coupons", "poly-coupons", "ui-pdp-coupons")
    cue_text = cue.text()[:160] if cue else ""
    coupon_price = bool(re.search(r"\b(?:com|no|ap[oó]s o) cupom\b", terms))
    code = re.search(r"\b(?:c[oó]digo|use o cupom|cupom:)\s*[:\-]?\s*([A-Z0-9][A-Z0-9_-]{3,39})\b", cue_text)
    code = code or re.search(r"\b(?:use o cupom|c[oó]digo|cupom:)\s*[:\-]?\s*([A-Z0-9][A-Z0-9_-]{3,39})\b", cue_text, re.I)
    coupon_code = code[1].upper() if code and (code[1].isupper() or any(c.isdigit() for c in code[1])) else ""
    result = dict(title=title.text(), url=link, price=value, old=previous, cash=value if "pix" in terms and not coupon_price else None,
                  shipping_text=shipping_text, installments=None, installment_value=None, card_total=None,
                  public_coupon=bool(cue_text or coupon_price), coupon_code=coupon_code, coupon_price=coupon_price,
                  coupon_text=cue_text)
    if plan:
        plan_text = plan.text()
        each = re.search(r"(\d{1,2})\s*[x×]\s*(?:de\s*)?" + MONEY, plan_text, re.I)
        total = re.search(MONEY + r"\s+em\s+(\d{1,2})\s*[x×]", plan_text, re.I)
        if each and 2 <= int(each[1]) <= 48:
            result.update(installments=int(each[1]), installment_value=price(each[2]))
        if total and 2 <= int(total[2]) <= 48:
            result.update(installments=int(total[2]), card_total=price(total[1]))
        result["plan_text"] = plan_text[:160]
    return result

def parse(html, url, detail):
    page = Page(html)
    if detail:
        result = row(page.root, url, detail=True)
        if result:
            return [result]
        for script in page.root.walk():
            if script.tag != "script" or script.attrs.get("type") != "application/ld+json":
                continue
            try:
                data = json.loads("".join(script.parts()))
            except ValueError:
                continue
            roots = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for entry in roots:
                if not isinstance(entry, dict) or entry.get("@type") != "Product":
                    continue
                offer = entry.get("offers")
                if not isinstance(offer, dict) or offer.get("priceCurrency") != "BRL":
                    continue
                if "outofstock" in str(offer.get("availability", "")).casefold():
                    return []
                value = price(offer.get("price"))
                if entry.get("name") and value:
                    return [dict(title=entry["name"], url=url, price=value)]
        return None
    cards = [node for node in page.root.walk() if "poly-card" in node.classes]
    if not cards:
        return [] if page.root.find("ui-search-no-results", "ui-search-rescue") else None
    results = [result for node in cards if (result := row(node, url))]
    return results or None
