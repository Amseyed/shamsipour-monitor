import html
import json
import os
import re
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

RSS_URL = os.getenv("RSS_URL", "https://shamsipour.tvu.ac.ir/?a=content.q.rss&lang=fa").strip()
BALE_TOKEN = os.getenv("BALE_TOKEN", "").strip()
BALE_CHAT_ID = os.getenv("BALE_CHAT_ID", "").strip()
CHECK_INTERVAL = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "600")))
STATE_FILE = Path(os.getenv("STATE_FILE", "/tmp/shamsipour_state.json"))
BALE_API = f"https://tapi.bale.ai/bot{BALE_TOKEN}"


def fetch(url, data=None, headers=None, timeout=30):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean(value):
    if not value:
        return ""
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def read_feed():
    raw = fetch(RSS_URL, headers={"User-Agent": "ShamsipourNoticeMonitor/2.0"})
    root = ET.fromstring(raw)
    entries = []
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            title = clean(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            guid = clean(item.findtext("guid")) or link or title
            if title or link:
                entries.append({"id": guid, "title": title or "اطلاعیه جدید", "link": link,
                                "description": clean(item.findtext("description")),
                                "pub_date": clean(item.findtext("pubDate"))})
    if not entries:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for item in root.findall("a:entry", ns):
            title = clean(item.findtext("a:title", default="", namespaces=ns))
            ident = clean(item.findtext("a:id", default="", namespaces=ns))
            link_el = item.find("a:link", ns)
            link = link_el.attrib.get("href", "") if link_el is not None else ""
            if title or link:
                entries.append({"id": ident or link or title, "title": title or "اطلاعیه جدید", "link": link,
                                "description": clean(item.findtext("a:summary", default="", namespaces=ns)),
                                "pub_date": clean(item.findtext("a:updated", default="", namespaces=ns))})
    if not entries:
        raise RuntimeError("فید دریافت شد، اما آیتم RSS/Atom قابل‌شناسایی نداشت.")
    return entries


def send_message(message):
    if not BALE_TOKEN or not BALE_CHAT_ID:
        raise RuntimeError("متغیرهای BALE_TOKEN یا BALE_CHAT_ID تنظیم نشده‌اند.")
    url = f"{BALE_API}/sendMessage"
    payload = json.dumps({"chat_id": BALE_CHAT_ID, "text": message[:4000], "disable_web_page_preview": False}).encode("utf-8")
    raw = fetch(url, data=payload, headers={"Content-Type": "application/json"}, timeout=30)
    result = json.loads(raw.decode("utf-8"))
    if not result.get("ok", False):
        raise RuntimeError(f"خطای API بله: {result}")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"last_id": ""}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(STATE_FILE)


def check_once():
    entries = read_feed()
    state = load_state()
    last_id = state.get("last_id", "")

    if not last_id:
        # Initial run establishes a baseline and sends a health-check message.
        send_message("✅ پایشگر اطلاعیه‌های شمسی‌پور فعال شد.\nفید RSS با موفقیت دریافت شد؛ از این پس اطلاعیه‌های جدید ارسال می‌شوند.")
        save_state({"last_id": entries[0]["id"]})
        print(f"راه‌اندازی اولیه موفق؛ {len(entries)} آیتم در فید دیده شد.", flush=True)
        return

    ids = [entry["id"] for entry in entries]
    if last_id not in ids:
        # Do not send a flood if the old item has dropped out of the RSS window.
        save_state({"last_id": entries[0]["id"]})
        print("شناسه قبلی دیگر در فید نیست؛ برای جلوگیری از ارسال انبوه، فقط وضعیت به‌روز شد.", flush=True)
        return

    new_entries = list(reversed(entries[:ids.index(last_id)]))
    for entry in new_entries:
        parts = ["🎓 اطلاعیهٔ جدید شمسی‌پور", "", entry["title"]]
        if entry.get("pub_date"):
            parts += ["", "📅 " + entry["pub_date"]]
        if entry.get("description"):
            desc = entry["description"]
            parts += ["", desc[:900] + ("..." if len(desc) > 900 else "")]
        if entry.get("link"):
            parts += ["", "🔗 " + entry["link"]]
        send_message("\n".join(parts))
        print("ارسال شد: " + entry["title"], flush=True)
    save_state({"last_id": entries[0]["id"]})
    print(f"بررسی تمام شد؛ اطلاعیه جدید: {len(new_entries)}", flush=True)


def main():
    if not BALE_TOKEN or not BALE_CHAT_ID:
        raise RuntimeError("لطفاً متغیرهای BALE_TOKEN و BALE_CHAT_ID را در Weblines تنظیم کنید.")
    print("پایشگر شروع شد.", flush=True)
    print(f"فاصله بررسی: {CHECK_INTERVAL} ثانیه", flush=True)
    while True:
        try:
            check_once()
        except Exception as exc:
            print(f"خطا در بررسی: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
