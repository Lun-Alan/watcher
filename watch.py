"""Product／飛 監察器。

用法：
  python watch.py add <url> [--name 名] [--stock] [--price [目標價]] [--change] [--selector CSS] [--js]
  python watch.py list
  python watch.py remove <id>
  python watch.py check
  python watch.py test-notify
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

import checks
import notify
from fetch import fetch

HERE = Path(__file__).resolve().parent
WATCHLIST = HERE / "watchlist.yaml"
STATE = HERE / "state.json"
FAIL_ALERT_AFTER = 3

WATCHLIST_HEADER = """\
# 監察清單。可以用 `python watch.py add <link>` 加，亦可以直接喺度改（例如用 GitHub 手機 app）。
# 每個項目：
#   id / name / url
#   checks: [stock, price, change]  揀一樣或者幾樣
#   target_price: 跌到呢個價或者以下先通知（唔填就係一跌價就通知）
#   selector: 只睇頁面呢部分（CSS selector），change 同 stock 都會用
#   js: true  頁面要行 JavaScript 先出內容就開呢個
#   in_stock_text / out_of_stock_text: 自訂「有貨」「冇貨」字眼
"""


# ---------- 讀寫檔案 ----------

def load_items():
    if not WATCHLIST.exists():
        return []
    data = yaml.safe_load(WATCHLIST.read_text()) or {}
    return data.get("items") or []


def save_items(items):
    body = yaml.safe_dump({"items": items}, allow_unicode=True, sort_keys=False)
    WATCHLIST.write_text(WATCHLIST_HEADER + "\n" + body)


def load_state():
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text() or "{}")


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


# ---------- git 同步（本機 add／remove 之後自動 push 上 GitHub） ----------

def git(*args):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)


def git_sync(message):
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print("（呢個 folder 唔係 git repo，冇 push。）")
        return
    git("add", WATCHLIST.name, STATE.name)
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-m", message)
    pull = git("pull", "--rebase", "--autostash")
    push = git("push")
    if pull.returncode or push.returncode:
        print("⚠️ push 唔到上 GitHub，請自己行 `git pull --rebase && git push`：")
        print((pull.stderr + push.stderr).strip())
    else:
        print("☁️ 已經 push 上 GitHub。")


# ---------- 指令 ----------

def make_id(name, url, existing):
    base = re.sub(r"[^a-z0-9]+", "-", (name or urlparse(url).netloc).lower()).strip("-") or "item"
    base = base[:30]
    item_id = f"{base}-{hashlib.sha1(url.encode()).hexdigest()[:4]}"
    while item_id in existing:
        item_id += "x"
    return item_id


def cmd_add(args):
    items = load_items()
    if any(i["url"] == args.url for i in items):
        sys.exit("呢條 link 已經喺清單入面。")

    print("🔍 試緊抓頁面…")
    try:
        soup = checks.parse(fetch(args.url, js=args.js))
    except Exception as e:
        sys.exit(f"❌ 抓唔到頁面：{e}\n如果個網站要行 JavaScript，可以試吓加 --js。")

    title = soup.title.get_text(strip=True) if soup.title else ""
    item = {"name": args.name or title[:60] or urlparse(args.url).netloc, "url": args.url}
    item["id"] = make_id(args.name, args.url, {i["id"] for i in items})
    if args.selector:
        item["selector"] = args.selector
    if args.js:
        item["js"] = True

    stock = checks.detect_stock(soup, item)
    price = checks.detect_price(soup, item)

    chosen = []
    if args.stock:
        chosen.append("stock")
    if args.price is not None:
        chosen.append("price")
        if args.price > 0:
            item["target_price"] = args.price
    if args.change:
        chosen.append("change")
    if not chosen:
        # 冇指定就自動揀：睇得出有冇貨就用 stock，搵到價錢就用 price，兩樣都冇就用 change
        if stock is not None:
            chosen.append("stock")
        if price is not None:
            chosen.append("price")
        if not chosen:
            chosen.append("change")
    item["checks"] = chosen
    item = {k: item[k] for k in ("id", "name", "url", "checks", "target_price", "selector", "js") if k in item}

    print(f"\n📦 {item['name']}")
    print(f"   id：{item['id']}")
    print(f"   有冇貨：{ {True: '有貨', False: '冇貨', None: '睇唔出'}[stock] }")
    print(f"   價錢：{checks.fmt_price(price) if price is not None else '搵唔到'}")
    print(f"   會監察：{', '.join(chosen)}")
    if "stock" in chosen and stock is None:
        print("   ⚠️ 睇唔出有冇貨，可能要用 --selector、--js，或者喺 watchlist.yaml 設 in_stock_text／out_of_stock_text。")
    if "price" in chosen and price is None:
        print("   ⚠️ 搵唔到價錢，可能要喺 watchlist.yaml 加 price_selector。")

    # 記低而家嘅狀態做基準，下次有變先通知
    state = load_state()
    new_state, _ = checks.evaluate(item, soup, {})
    state[item["id"]] = {**new_state, "fail_count": 0, "checked_at": now()}
    items.append(item)
    save_items(items)
    save_state(state)
    print("\n✅ 已經加入監察清單。")
    if not args.no_push:
        git_sync(f"watch: add {item['id']}")


def cmd_list(args):
    items = load_items()
    if not items:
        print("清單係空嘅。用 `python watch.py add <link>` 加。")
        return
    state = load_state()
    for item in items:
        s = state.get(item["id"], {})
        bits = []
        if "stock" in item.get("checks", []):
            bits.append({True: "有貨", False: "冇貨"}.get(s.get("in_stock"), "未知"))
        if "price" in item.get("checks", []):
            bits.append(f"${checks.fmt_price(s.get('price'))}")
        if s.get("fail_count"):
            bits.append(f"連續失敗 {s['fail_count']} 次")
        print(f"- {item['id']}  {item['name']}")
        print(f"    {item['url']}")
        print(f"    {', '.join(item.get('checks', []))} | {' | '.join(bits) or '未檢查'} | 上次：{s.get('checked_at', '-')}")


def cmd_remove(args):
    items = load_items()
    left = [i for i in items if i["id"] != args.id]
    if len(left) == len(items):
        sys.exit(f"搵唔到 id「{args.id}」。用 `python watch.py list` 睇吓。")
    state = load_state()
    state.pop(args.id, None)
    save_items(left)
    save_state(state)
    print(f"🗑️ 已經移除 {args.id}。")
    if not args.no_push:
        git_sync(f"watch: remove {args.id}")


def cmd_check(args):
    items = load_items()
    state = load_state()
    messages = []

    for item in items:
        prev = state.get(item["id"], {})
        try:
            soup = checks.parse(fetch(item["url"], js=item.get("js", False)))
        except Exception as e:
            fails = prev.get("fail_count", 0) + 1
            state[item["id"]] = {**prev, "fail_count": fails, "last_error": str(e)[:200], "checked_at": now()}
            print(f"❌ {item['id']}：{e}")
            if fails == FAIL_ALERT_AFTER:
                messages.append(f"⚠️ {item['name']}\n連續 {fails} 次抓唔到頁面（{str(e)[:100]}）\n{item['url']}")
            continue

        new_state, alerts = checks.evaluate(item, soup, prev)
        state[item["id"]] = {**new_state, "fail_count": 0, "checked_at": now()}
        print(f"✔ {item['id']}：{'有變化' if alerts else '冇變'}")
        if alerts:
            messages.append(f"🔔 {item['name']}\n" + "\n".join(alerts) + f"\n{item['url']}")

    # 刪走已經唔喺清單嘅舊狀態
    ids = {i["id"] for i in items}
    state = {k: v for k, v in state.items() if k in ids}
    save_state(state)

    if messages:
        notify.send("\n\n".join(messages))


def cmd_test_notify(args):
    if notify.send("👋 監察器測試訊息：收到呢個即係 WhatsApp 通知設定好咗。"):
        print("✅ 已經 send 出，留意吓 WhatsApp。")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main():
    parser = argparse.ArgumentParser(description="Product／飛 監察器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="加一條 link")
    p.add_argument("url")
    p.add_argument("--name", help="自己改個名")
    p.add_argument("--stock", action="store_true", help="監察有冇貨／飛")
    p.add_argument("--price", type=float, nargs="?", const=0, help="監察價錢，可以跟目標價")
    p.add_argument("--change", action="store_true", help="頁面有任何變化就通知")
    p.add_argument("--selector", help="只睇頁面某部分（CSS selector）")
    p.add_argument("--js", action="store_true", help="用瀏覽器開（要行 JavaScript 嘅頁）")
    p.add_argument("--no-push", action="store_true", help="唔好自動 push 上 GitHub")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="睇清單")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="移除一個項目")
    p.add_argument("id")
    p.add_argument("--no-push", action="store_true")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("check", help="即刻檢查一次")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("test-notify", help="send 測試 WhatsApp")
    p.set_defaults(func=cmd_test_notify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
