"""攞頁面 HTML：預設用 requests，項目設咗 js: true 就用 Playwright（處理要行 JavaScript 先出內容嘅頁）。"""

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}


def fetch(url, js=False, timeout=30):
    if js:
        return _fetch_js(url, timeout)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def _fetch_js(url, timeout):
    # 遲啲先 import，等冇裝 Playwright 都可以用普通模式
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="zh-HK")
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            return page.content()
        finally:
            browser.close()
