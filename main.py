import os
import json
import time
import threading
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, abort

# ── Config ────────────────────────────────────────────────
ORBIS_API_KEY    = os.environ.get("ORBIS_API_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_IDS        = set(os.environ.get("ADMIN_IDS", TELEGRAM_CHAT_ID).split(","))
WEBHOOK_SECRET   = os.environ.get("WEBHOOK_SECRET", "orbissecret123")
PORT             = int(os.environ.get("PORT", 8080))
PUBLIC_URL       = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")

ENV_CHATS        = os.environ.get("REGISTERED_CHATS", TELEGRAM_CHAT_ID)
ENV_MEDIA_TYPE   = os.environ.get("MEDIA_TYPE", "none")
ENV_MEDIA_FILE_ID = os.environ.get("MEDIA_FILE_ID", "")
ENV_MEDIA_URL    = os.environ.get("MEDIA_URL", "")

ERROR_COOLDOWN   = 3600
SCHEDULE_COOLDOWN = 4 * 3600

ORBIS_HEADERS    = {"x-api-key": ORBIS_API_KEY}
STATS_URL        = "https://orbisapi.com/api/provider/stats"
EARNINGS_URL     = "https://orbisapi.com/api/provider/earnings"
SUBSCRIBERS_URL  = "https://orbisapi.com/api/provider/subscribers"
APIS_URL         = "https://orbisapi.com/api/provider/apis"

SEEN_FILE        = "seen_subscribers.json"
MEDIA_FILE       = "media_config.json"
CHATS_FILE       = "registered_chats.json"
PREV_STATS_FILE  = "prev_stats.json"

PST              = ZoneInfo("America/Los_Angeles")
SCHEDULED_HOURS  = [6, 16]

app = Flask(__name__)
last_error_time  = 0
last_scheduled_send = 0


# ── Persistence ───────────────────────────────────────────

def load_chats():
    try:
        if os.path.exists(CHATS_FILE):
            with open(CHATS_FILE) as f:
                data = json.load(f)
                if data:
                    return set(data)
    except Exception:
        pass
    return set(c.strip() for c in ENV_CHATS.split(",") if c.strip())


def save_chats(chats):
    with open(CHATS_FILE, "w") as f:
        json.dump(list(chats), f)


def load_media():
    try:
        if os.path.exists(MEDIA_FILE):
            with open(MEDIA_FILE) as f:
                data = json.load(f)
                if data.get("type", "none") != "none":
                    return data
    except Exception:
        pass
    if ENV_MEDIA_TYPE == "photo" and ENV_MEDIA_FILE_ID:
        return {"type": "photo", "file_id": ENV_MEDIA_FILE_ID}
    elif ENV_MEDIA_TYPE == "animation" and ENV_MEDIA_FILE_ID:
        return {"type": "animation", "file_id": ENV_MEDIA_FILE_ID}
    elif ENV_MEDIA_TYPE == "url" and ENV_MEDIA_URL:
        return {"type": "url", "url": ENV_MEDIA_URL}
    return {"type": "none"}


def save_media(config):
    with open(MEDIA_FILE, "w") as f:
        json.dump(config, f)


def load_prev_stats():
    try:
        if os.path.exists(PREV_STATS_FILE):
            with open(PREV_STATS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_prev_stats(stats, earnings):
    with open(PREV_STATS_FILE, "w") as f:
        json.dump({
            "totalSubscribers": stats.get("totalSubscribers", 0),
            "totalCalls": stats.get("totalCalls", 0),
            "apiCount": stats.get("apiCount", 0),
            "totalEarningsUsdc": earnings.get("totalEarningsUsdc", 0),
            "thisMonthUsdc": earnings.get("thisMonthUsdc", 0),
        }, f)


def load_seen():
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE) as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def init_from_env():
    env_chats = set(c.strip() for c in ENV_CHATS.split(",") if c.strip())
    if env_chats:
        existing = set()
        try:
            if os.path.exists(CHATS_FILE):
                with open(CHATS_FILE) as f:
                    existing = set(json.load(f))
        except Exception:
            pass
        merged = env_chats | existing
        with open(CHATS_FILE, "w") as f:
            json.dump(list(merged), f)

    if ENV_MEDIA_TYPE != "none":
        existing_media = {"type": "none"}
        try:
            if os.path.exists(MEDIA_FILE):
                with open(MEDIA_FILE) as f:
                    existing_media = json.load(f)
        except Exception:
            pass
        if existing_media.get("type", "none") == "none":
            if ENV_MEDIA_TYPE == "animation" and ENV_MEDIA_FILE_ID:
                config = {"type": "animation", "file_id": ENV_MEDIA_FILE_ID}
            elif ENV_MEDIA_TYPE == "photo" and ENV_MEDIA_FILE_ID:
                config = {"type": "photo", "file_id": ENV_MEDIA_FILE_ID}
            elif ENV_MEDIA_TYPE == "url" and ENV_MEDIA_URL:
                config = {"type": "url", "url": ENV_MEDIA_URL}
            else:
                config = {"type": "none"}
            with open(MEDIA_FILE, "w") as f:
                json.dump(config, f)


# ── Telegram senders ──────────────────────────────────────

def get_keyboard():
    return {"inline_keyboard": [[
        {"text": "\U0001f7e3 ORBIS", "url": "https://orbisapi.com"},
        {"text": "\U0001f3c6 Vote", "url": "https://bags.fm/hackathon/apps"}
    ]]}


def send_with_media(caption, chat_id=None):
    target = chat_id or TELEGRAM_CHAT_ID
    media = load_media()
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if media["type"] == "photo":
        requests.post(f"{base}/sendPhoto", json={"chat_id": target, "photo": media["file_id"], "caption": caption, "reply_markup": get_keyboard()})
    elif media["type"] == "animation":
        requests.post(f"{base}/sendAnimation", json={"chat_id": target, "animation": media["file_id"], "caption": caption, "reply_markup": get_keyboard()})
    elif media["type"] == "url":
        requests.post(f"{base}/sendPhoto", json={"chat_id": target, "photo": media["url"], "caption": caption, "reply_markup": get_keyboard()})
    else:
        requests.post(f"{base}/sendMessage", json={"chat_id": target, "text": caption, "reply_markup": get_keyboard()})


def broadcast(caption):
    for chat_id in load_chats():
        send_with_media(caption, chat_id=chat_id)


def send_message(text, chat_id=None):
    target = chat_id or TELEGRAM_CHAT_ID
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": target, "text": text})


def send_error(message):
    global last_error_time
    now = time.time()
    if now - last_error_time > ERROR_COOLDOWN:
        send_message(f"\u26a0\ufe0f Bot error: {message}")
        last_error_time = now


# ── Orbis fetchers ────────────────────────────────────────

def fetch(url):
    r = requests.get(url, headers=ORBIS_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_all():
    urls = {"stats": STATS_URL, "earnings": EARNINGS_URL, "subscribers": SUBSCRIBERS_URL, "apis": APIS_URL}
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch, url): key for key, url in urls.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"Error fetching {key}: {e}")
                results[key] = {}
    return results


def get_subscriber_ids(data):
    ids = set()
    subs = data if isinstance(data, list) else data.get("subscribers", [])
    for s in subs:
        uid = s.get("id") or s.get("userId") or s.get("subscriberId")
        if uid:
            ids.add(str(uid))
    return ids, subs


# ── Formatter ─────────────────────────────────────────────

def delta(current, previous, key, prefix=""):
    curr = current if isinstance(current, (int, float)) else 0
    prev = previous.get(key, 0) or 0
    diff = round(curr - prev, 2)
    if diff > 0:
        return f" (+{prefix}{diff})"
    return ""


def format_stats(stats, earnings, apis_data, new_sub=None, show_delta=False):
    sub_count   = stats.get("totalSubscribers", "N/A")
    total_calls = stats.get("totalCalls", "N/A")
    api_count   = stats.get("apiCount", "N/A")
    total_earned = round(earnings.get("totalEarningsUsdc", 0), 2)
    this_month   = round(earnings.get("thisMonthUsdc", 0), 2)
    now_pst      = datetime.now(PST).strftime("%b %d, %Y %I:%M %p PST")
    prev         = load_prev_stats() if show_delta else {}

    d_subs   = delta(sub_count, prev, "totalSubscribers") if show_delta else ""
    d_calls  = delta(total_calls, prev, "totalCalls") if show_delta else ""
    d_apis   = delta(api_count, prev, "apiCount") if show_delta else ""
    d_earned = delta(total_earned, prev, "totalEarningsUsdc", "$") if show_delta else ""
    d_month  = delta(this_month, prev, "thisMonthUsdc", "$") if show_delta else ""

    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])
    apis = sorted(apis, key=lambda a: a.get("subscriberCount") or a.get("subscribers") or 0, reverse=True)

    if new_sub:
        name = new_sub.get("name") or new_sub.get("username") or new_sub.get("email") or "Unknown"
        api  = new_sub.get("apiName") or new_sub.get("api_name") or "Unknown API"
        plan = new_sub.get("plan") or new_sub.get("tier") or "Free"
        header = f"\U0001f389 New Subscriber!\n{name} joined {api} ({plan})\n"
    else:
        header = "Schlegel Orbis API Tracker\n"

    lines = [
        header,
        f"\U0001f465 Total Subscribers: {sub_count}{d_subs}",
        f"\U0001f4ca Total API Calls:   {total_calls}{d_calls}",
        f"\U0001f517 APIs Listed:       {api_count}{d_apis}",
        "",
        f"\U0001f4b0 Total Earned:  ${total_earned} USDC{d_earned}",
        f"\U0001f4c5 This Month:    ${this_month} USDC{d_month}",
        "",
        "Top APIs:",
    ]
    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        lines.append(f"  - {n}: {s} subs")
    lines += ["", now_pst, "", "*This tracks user 'schlegel' only for fun, this is not a full network tracker"]
    return "\n".join(lines)


# ── Command handler ───────────────────────────────────────

def handle_command(msg):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    user_id = str(msg.get("from", {}).get("id", ""))
    text    = msg.get("text", "").split("@")[0]
    is_admin = user_id in ADMIN_IDS

    print(f"Command: {text} from {user_id} in {chat_id} (admin={is_admin})")

    if text == "/schlegelapi":
        if not is_admin:
            send_message("Not authorized.", chat_id=chat_id)
            return
        data = fetch_all()
        send_with_media(format_stats(data["stats"], data["earnings"], data.get("apis", []), show_delta=True), chat_id=chat_id)
        save_prev_stats(data["stats"], data["earnings"])
        return

    if text == "/help":
        help_text = "Schlegel Orbis Tracker\n\n/help - Show commands\n"
        if is_admin:
            help_text += (
                "\nAdmin Only:\n"
                "/schlegelapi - Get latest stats\n"
                "/addchat - Add this chat to broadcasts\n"
                "/removechat - Remove this chat\n"
                "/listchats - Show registered chats\n"
                "/setimage [url] - Set image from URL\n"
                "/setphoto - Reply to a photo with this\n"
                "/setgif - Reply to a GIF with this\n"
                "/clearmedia - Remove media\n"
                "/status - Bot status\n"
            )
        send_message(help_text, chat_id=chat_id)
        return

    if not is_admin:
        return

    if text == "/addchat":
        chats = load_chats()
        chats.add(chat_id)
        save_chats(chats)
        send_message(f"\u2705 Chat added! Also add {chat_id} to REGISTERED_CHATS in Railway.", chat_id=chat_id)
        return

    if text == "/removechat":
        chats = load_chats()
        chats.discard(chat_id)
        save_chats(chats)
        send_message("\u2705 Chat removed.", chat_id=chat_id)
        return

    if text == "/listchats":
        send_message("Registered chats:\n" + "\n".join(load_chats()), chat_id=chat_id)
        return

    if text.startswith("/setimage "):
        config = {"type": "url", "url": text[len("/setimage "):].strip()}
        save_media(config)
        send_message(f"\u2705 Image URL saved!\nRailway: MEDIA_TYPE=url\nMEDIA_URL={config['url']}", chat_id=chat_id)
        return

    if text == "/setphoto":
        reply = msg.get("reply_to_message", {})
        if reply.get("photo"):
            file_id = reply["photo"][-1]["file_id"]
            save_media({"type": "photo", "file_id": file_id})
            send_message(f"\u2705 Photo saved!\nRailway: MEDIA_TYPE=photo\nMEDIA_FILE_ID={file_id}", chat_id=chat_id)
        else:
            send_message("Reply to a photo with /setphoto.", chat_id=chat_id)
        return

    if text == "/setgif":
        reply = msg.get("reply_to_message", {})
        if reply.get("animation"):
            file_id = reply["animation"]["file_id"]
            save_media({"type": "animation", "file_id": file_id})
            send_message(f"\u2705 GIF saved!\nRailway: MEDIA_TYPE=animation\nMEDIA_FILE_ID={file_id}", chat_id=chat_id)
        else:
            send_message("Reply to a GIF with /setgif.", chat_id=chat_id)
        return

    if text == "/clearmedia":
        save_media({"type": "none"})
        send_message("\u2705 Media cleared.", chat_id=chat_id)
        return

    if text == "/status":
        media  = load_media()
        chats  = load_chats()
        send_message(
            f"Bot Status\n\nMode: Webhook\nScheduled: 6AM + 4PM PST\nMedia: {media.get('type','none')}\nBroadcast chats: {len(chats)}\nAdmin IDs: {ADMIN_IDS}",
            chat_id=chat_id
        )
        return


# ── Webhook endpoint ──────────────────────────────────────

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)
    if not update:
        abort(400)
    msg = update.get("message", {})
    if msg.get("text"):
        threading.Thread(target=handle_command, args=(msg,), daemon=True).start()
    return "ok", 200


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


# ── Scheduler thread ──────────────────────────────────────

def scheduler_loop():
    global last_scheduled_send
    last_scheduled_send = time.time() - 3 * 3600
    seen = load_seen()

    while True:
        try:
            now_pst = datetime.now(PST)
            if now_pst.hour in SCHEDULED_HOURS and time.time() - last_scheduled_send > SCHEDULE_COOLDOWN:
                print("Sending scheduled update...")
                data = fetch_all()
                broadcast(format_stats(data["stats"], data["earnings"], data.get("apis", []), show_delta=True))
                save_prev_stats(data["stats"], data["earnings"])
                last_scheduled_send = time.time()

            # Check for new subscribers silently
            data = fetch_all()
            subs_data = data.get("subscribers", {})
            current_ids, _ = get_subscriber_ids(subs_data)
            new_ids = current_ids - seen
            if new_ids:
                print(f"New subscribers detected: {len(new_ids)} (silent)")
                save_seen(current_ids)
                seen = current_ids

        except Exception as e:
            print(f"Scheduler error: {e}")
            send_error(str(e))

        time.sleep(60)


# ── Startup ───────────────────────────────────────────────

def register_webhook():
    if not PUBLIC_URL:
        print("WARNING: RAILWAY_PUBLIC_DOMAIN not set — webhook not registered")
        return
    webhook_url = f"https://{PUBLIC_URL}/webhook/{WEBHOOK_SECRET}"
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        json={"url": webhook_url, "drop_pending_updates": True}
    )
    print(f"Webhook registered: {r.json()}")


if __name__ == "__main__":
    init_from_env()
    register_webhook()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
