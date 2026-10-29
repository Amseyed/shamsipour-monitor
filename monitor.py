import json
import os
import re
import html
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

RSS_URL = os.getenv(
    "RSS_URL",
    "https://shamsipour.tvu.ac.ir/?a=content.q.rss&lang=fa"
)
BALE_TOKEN = os.getenv("BALE_TOKEN", "").strip()
BALE_CHAT_ID = os.getenv("BALE_CHAT_ID", "").strip()
STATE_FILE = Path("state.json")
BALE_API = "https://tapi.bale.ai/bot" + BALE_TOKEN


def http_get(url, timeout=25):
    req = Request(
        url,
        headers={"User-Agent": "ShamsipourNoticeMonitor/1.0"}
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def clean_text(value):
    if not value:
        return ""
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def read_feed():
    raw = http_get(RSS_URL)
    root = ET.fromstring(raw)
    entries = []

    # Standard RSS 2.0
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            title = clean_text(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            guid = clean_text(item.findtext("guid")) or link or title
            description = clean_text(item.findtext("description"))
            pub_date = clean_text(item.findtext("pubDate"))
            if title or link:
                entries.append({
                    "id": guid,
                    "title": title or "اطلاعیه جدید",
                    "link": link,
                    "description": description,
                    "pub_date": pub_date,
                })

    # Atom fallback
    if not entries:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for item in root.findall("a:entry", ns):
            title = clean_text(item.findtext("a:title", default="", namespaces=ns))
            ident = clean_text(item.findtext("a:id", default="", namespaces=ns))
            link_el = item.find("a:link", ns)
            link = link_el.attrib.get("href", "") if link_el is not None else ""
            summary = clean_text(item.findtext("a:summary", default="", namespaces=ns))
            updated = clean_text(item.findtext("a:updated", default="", namespaces=ns))
            if title or link:
                entries.append({
                    "id": ident or link or title,
                    "title": title or "اطلاعیه جدید",
                    "link": link,
                    "description": summary,
                    "pub_date": updated,
                })

    if not entries:
        raise RuntimeError("فید دریافت شد، اما هیچ آیتم RSS/Atom قابل‌شناسایی در آن نبود.")
    return entries


def bale_request(method, payload=None):
    if not BALE_TOKEN:
        raise RuntimeError("متغیر محرمانه BALE_TOKEN تنظیم نشده است.")
    import urllib.parse
    import urllib.request

    url = f"{BALE_API}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"خطا در ارتباط با API بله: {exc}") from exc
    if not result.get("ok", False):
        raise RuntimeError(f"API بله خطا برگرداند: {result}")
    return result


def discover_private_chat():
    result = bale_request("getUpdates")
    updates = result.get("result", [])
    private_chats = []
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            private_chats.append(str(chat["id"]))
    if private_chats:
        print("شناسهٔ گفت‌وگوی خصوصی پیدا شد: " + private_chats[-1])
        print("این مقدار را در GitHub Secrets با نام BALE_CHAT_ID ذخیره کن.")
        return
    print("هنوز پیام خصوصی از تو دریافت نشده است.")
    print("در بله گفت‌وگوی بازو را باز کن، /start بفرست، سپس اجرای دستی GitHub Actions را دوباره انجام بده.")


def send_message(text):
    bale_request("sendMessage", {
        "chat_id": BALE_CHAT_ID,
        "text": text[:4000],
        "disable_web_page_preview": False,
    })


def load_state():
    if not STATE_FILE.exists():
        return {"last_id": ""}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_id": ""}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )


def main():
    if not BALE_TOKEN:
        raise RuntimeError("ابتدا Secret با نام BALE_TOKEN را در GitHub تنظیم کن.")

    if not BALE_CHAT_ID:
        discover_private_chat()
        return

    entries = read_feed()
    state = load_state()
    last_id = state.get("last_id", "")

    # First run: record the newest item, do not send old notices.
    if not last_id:
        state["last_id"] = entries[0]["id"]
        save_state(state)
        send_message(
            "✅ پایشگر اطلاعیه‌های شمسی‌پور راه‌اندازی شد.\n"
            "فید RSS با موفقیت دریافت شد. از این پس اطلاعیه‌های جدید ارسال می‌شوند."
        )
        print(f"راه‌اندازی اولیه موفق بود؛ {len(entries)} آیتم در فید دیده شد.")
        return

    ids = [entry["id"] for entry in entries]
    if last_id not in ids:
        # Avoid sending a flood if the previous item has rolled off the RSS feed.
        state["last_id"] = entries[0]["id"]
        save_state(state)
        print("شناسهٔ قبلی در فید فعلی نیست؛ برای جلوگیری از ارسال انبوه، فقط آخرین شناسه به‌روز شد.")
        return

    stop_index = ids.index(last_id)
    new_entries = list(reversed(entries[:stop_index]))
    for entry in new_entries:
        lines = ["🎓 اطلاعیهٔ جدید شمسی‌پور", "", entry["title"]]
        if entry.get("pub_date"):
            lines.extend(["", "📅 " + entry["pub_date"]])
        if entry.get("description"):
            desc = entry["description"]
            if len(desc) > 900:
                desc = desc[:897] + "..."
            lines.extend(["", desc])
        if entry.get("link"):
            lines.extend(["", "🔗 " + entry["link"]])
        send_message("\n".join(lines))
        print("ارسال شد: " + entry["title"])

    state["last_id"] = entries[0]["id"]
    save_state(state)
    print(f"پایان بررسی؛ تعداد اطلاعیه‌های جدید: {len(new_entries)}")


if __name__ == "__main__":
    main()
