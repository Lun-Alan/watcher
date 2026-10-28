"""三種偵測：stock（有冇貨／飛）、price（價錢）、change（頁面內容變化）。"""

import difflib
import hashlib
import json
import re

from bs4 import BeautifulSoup

IN_STOCK_WORDS = [
    "add to cart", "add to bag", "add to basket", "buy now", "buy tickets", "book now", "in stock",
    "加入購物車", "加入購物籃", "立即購買", "立即購票", "立即訂購", "馬上購買", "有現貨",
]
OUT_OF_STOCK_WORDS = [
    "sold out", "out of stock", "currently unavailable", "notify me when available",
    "售罄", "已售完", "已售罄", "缺貨", "暫無存貨", "沒有存貨", "冇貨", "完售",
]

SNAPSHOT_CHARS = 5000


def parse(html):
    return BeautifulSoup(html, "html.parser")


def page_text(soup, selector=None):
    if selector:
        nodes = soup.select(selector)
        if not nodes:
            return ""
        return "\n".join(_clean_text(n) for n in nodes)
    body = soup.body or soup
    return _clean_text(body)


def _clean_text(node):
    node = BeautifulSoup(str(node), "html.parser")
    for tag in node(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = (line.strip() for line in node.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def _json_ld_products(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                if "@graph" in obj:
                    stack.extend(obj["@graph"])
                if "offers" in obj:
                    yield obj


def _offers(product):
    offers = product.get("offers")
    if isinstance(offers, dict):
        offers = [offers]
    return [o for o in offers or [] if isinstance(o, dict)]


def to_number(text):
    if text is None:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def detect_stock(soup, item):
    """返回 True（有貨）、False（冇貨）或者 None（睇唔出）。"""
    if item.get("stock_selector"):
        return bool(soup.select(item["stock_selector"]))

    for product in _json_ld_products(soup):
        for offer in _offers(product):
            avail = str(offer.get("availability", "")).lower()
            if "instock" in avail or "limitedavailability" in avail or "preorder" in avail:
                return True
            if "outofstock" in avail or "soldout" in avail or "discontinued" in avail:
                return False

    text = page_text(soup, item.get("selector")).lower()
    out_words = item.get("out_of_stock_text") or OUT_OF_STOCK_WORDS
    in_words = item.get("in_stock_text") or IN_STOCK_WORDS
    if any(w.lower() in text for w in _as_list(out_words)):
        return False
    if any(w.lower() in text for w in _as_list(in_words)):
        return True
    return None


def detect_price(soup, item):
    if item.get("price_selector"):
        node = soup.select_one(item["price_selector"])
        return to_number(node.get("content") or node.get_text()) if node else None

    for product in _json_ld_products(soup):
        for offer in _offers(product):
            price = to_number(offer.get("price") or offer.get("lowPrice"))
            if price is not None:
                return price

    for attrs in (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
    ):
        node = soup.find(attrs=attrs)
        if node:
            price = to_number(node.get("content") or node.get_text())
            if price is not None:
                return price

    # 最後一招：搵 class／id 有 "price" 字眼、又似價錢嘅元素
    for node in soup.select('[class*="price" i], [id*="price" i]'):
        text = node.get_text(" ", strip=True)
        if len(text) <= 30 and re.search(r"[$£€¥]|HK|NT|USD|元", text):
            price = to_number(text)
            if price is not None:
                return price
    return None


def _as_list(value):
    return [value] if isinstance(value, str) else list(value)


def snapshot(soup, item):
    return page_text(soup, item.get("selector"))[:SNAPSHOT_CHARS]


def evaluate(item, soup, prev):
    """比較今次同上次，返回 (新狀態, 通知訊息 list)。"""
    checks = item.get("checks", [])
    state = {}
    alerts = []

    if "stock" in checks:
        in_stock = detect_stock(soup, item)
        state["in_stock"] = in_stock
        if in_stock is True and prev.get("in_stock") is False:
            alerts.append("✅ 有貨／有飛喇！")

    if "price" in checks:
        price = detect_price(soup, item)
        state["price"] = price
        old = prev.get("price")
        target = item.get("target_price")
        if price is not None:
            crossed = target is not None and price <= target and not (old is not None and old <= target)
            dropped = old is not None and price < old and (target is None or price <= target)
            if crossed or dropped:
                line = f"💰 價錢 {fmt_price(old)} → {fmt_price(price)}"
                if target is not None:
                    line += f"（目標 {fmt_price(target)}）"
                alerts.append(line)

    if "change" in checks:
        text = snapshot(soup, item)
        digest = hashlib.sha256(text.encode()).hexdigest()
        state["hash"] = digest
        state["snapshot"] = text
        if prev.get("hash") and prev["hash"] != digest:
            alerts.append("🔄 頁面有變：\n" + short_diff(prev.get("snapshot", ""), text))

    return state, alerts


def short_diff(old, new, max_lines=6):
    added = [
        line[1:].strip()
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    ]
    if not added:
        return "（有內容被刪走）"
    lines = [f"+ {line[:80]}" for line in added[:max_lines]]
    if len(added) > max_lines:
        lines.append(f"…仲有 {len(added) - max_lines} 行")
    return "\n".join(lines)


def fmt_price(value):
    if value is None:
        return "?"
    return f"{value:,.2f}".rstrip("0").rstrip(".")
