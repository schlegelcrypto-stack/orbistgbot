import requests
import time
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

ORBIS_API_KEY = os.environ.get("ORBIS_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_IDS = set(os.environ.get("ADMIN_IDS", TELEGRAM_CHAT_ID).split(","))
ERROR_COOLDOWN = 3600

ORBIS_HEADERS = {"x-api-key": ORBIS_API_KEY}
STATS_URL = "https://orbisapi.com/api/provider/stats"
EARNINGS_URL = "https://orbisapi.com/api/provider/earnings"
SUBSCRIBERS_URL = "https://orbisapi.com/api/provider/subscribers"
APIS_URL = "https://orbisapi.com/api/provider/apis"

SEEN_FILE = "seen_subscribers.json"
MEDIA_FILE = "media_config.json"
OFFSET_FILE = "update_offset.json"
SCHEDULE_FILE = "schedule_state.json"
CHATS_FILE = "registered_chats.json"

PST = ZoneInfo("America/Los_Angeles")
SCHEDULED_HOURS = [6, 16]
last_error_time = 0


def load_chats():
    if os.path.exists(CHATS_FILE):
        with open(CHATS_FILE) as f:
            return set(json.load(f))
    return {TELEGRAM_CHAT_ID}


def save_chats(chats):
    with open(CHATS_FILE, "w") as f:
        json.dump(list(chats), f)


def load_media():
    if os.path.exists(MEDIA_FILE):
        with open(MEDIA_FILE) as f:
            return json.load(f)
    return {"type": "none"}


def save_media(config):
    with open(MEDIA_FILE, "w") as f:
        json.dump(config, f)


def load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


def load_schedule_state():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    return {"last_sent_hour": -1, "last_sent_date": ""}


def save_schedule_state(hour, date_str):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump({"last_sent_hour": hour, "last_sent_date": date_str}, f)


def should_send_scheduled():
    now_pst = datetime.now(PST)
    current_hour = now_pst.hour
    current_date = now_pst.strftime("%Y-%m-%d")
    state = load_schedule_state()
    if current_hour in SCHEDULED_HOURS:
        if not (state["last_sent_hour"] == current_hour and state["last_sent_date"] == current_date):
            save_schedule_state(current_hour, current_date)
            return True
    return False


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


def fetch(url):
    r = requests.get(url, headers=ORBIS_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_all():
    urls = {
        "stats": STATS_URL,
        "earnings": EARNINGS_URL,
        "subscribers": SUBSCRIBERS_URL,
        "apis": APIS_URL,
    }
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


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def get_subscriber_ids(data):
    ids = set()
    subs = data if isinstance(data, list) else data.get("subscribers", [])
    for s in subs:
        uid = s.get("id") or s.get("userId") or s.get("subscriberId")
        if uid:
            ids.add(str(uid))
    return ids, subs


def format_stats(stats, earnings, apis_data, new_sub=None):
    sub_count = stats.get("totalSubscribers", "N/A")
    total_calls = stats.get("totalCalls", "N/A")
    api_count = stats.get("apiCount", "N/A")
    total_earned = round(earnings.get("totalEarningsUsdc", 0), 2)
    this_month = round(earnings.get("thisMonthUsdc", 0), 2)
    now_pst = datetime.now(PST).strftime("%b %d, %Y %I:%M %p PST")

    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])
    apis = sorted(apis, key=lambda a: a.get("subscriberCount") or a.get("subscribers") or 0, reverse=True)

    if new_sub:
        name = new_sub.get("name") or new_sub.get("username") or new_sub.get("email") or "Unknown"
        api = new_sub.get("apiName") or new_sub.get("api_name") or "Unknown API"
        plan = new_sub.get("plan") or new_sub.get("tier") or "Free"
        header = f"\U0001f389 New Subscriber!\n{name} joined {api} ({plan})\n"
    else:
        header = "Schlegel Orbis API Tracker\n"

    lines = [
        header,
        f"\U0001f465 Total Subscribers: {sub_count}",
        f"\U0001f4ca Total API Calls:   {total_calls}",
        f"\U0001f517 APIs Listed:       {api_count}",
        "",
        f"\U0001f4b0 Total Earned:  ${total_earned} USDC",
        f"\U0001f4c5 This Month:    ${this_month} USDC",
        "",
        "Top APIs:",
    ]

    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        lines.append(f"  - {n}: {s} subs")

    lines += ["", now_pst]
    return "\n".join(lines)


def handle_admin_commands(seen):
    offset = load_offset()
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 1},
            timeout=10
        )
        updates = r.json().get("result", [])
        if not updates:
            return

        new_offset = updates[-1]["update_id"] + 1
        save_offset(new_offset)

        for update in updates:
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            user_id = str(msg.get("from", {}).get("id", ""))
            text = msg.get("text", "").split("@")[0]
            is_admin = user_id in ADMIN_IDS

            if text == "/schlegelapi":
                if not is_admin:
                    continue
                try:
                    data = fetch_all()
                    broadcast(format_stats(data["stats"], data["earnings"], data.get("apis", [])))
                except Exception as e:
                    send_message(f"Error: {e}", chat_id=chat_id)
                continue

            if text == "/help":
                help_text = "Schlegel Orbis Tracker\n\n/help - Show commands\n"
                if is_admin:
                    help_text += (
                        "\nAdmin Only:\n"
                        "/schlegelapi - Trigger stats to all chats\n"
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
                continue

            if not is_admin:
                continue

            if text == "/addchat":
                chats = load_chats()
                chats.add(chat_id)
                save_chats(chats)
                send_message("\u2705 Chat added to broadcasts!", chat_id=chat_id)
                continue

            if text == "/removechat":
                chats = load_chats()
                chats.discard(chat_id)
                save_chats(chats)
                send_message("\u2705 Chat removed from broadcasts.", chat_id=chat_id)
                continue

            if text == "/listchats":
                chats = load_chats()
                send_message("Registered chats:\n" + "\n".join(chats), chat_id=chat_id)
                continue

            if text.startswith("/setimage "):
                save_media({"type": "url", "url": text[len("/setimage "):].strip()})
                send_message("\u2705 Image URL saved!", chat_id=chat_id)
                continue

            if text == "/setphoto":
                reply = msg.get("reply_to_message", {})
                if reply.get("photo"):
                    save_media({"type": "photo", "file_id": reply["photo"][-1]["file_id"]})
                    send_message("\u2705 Photo saved!", chat_id=chat_id)
                else:
                    send_message("Reply to a photo with /setphoto to set it.", chat_id=chat_id)
                continue

            if text == "/setgif":
                reply = msg.get("reply_to_message", {})
                if reply.get("animation"):
                    save_media({"type": "animation", "file_id": reply["animation"]["file_id"]})
                    send_message("\u2705 GIF saved!", chat_id=chat_id)
                else:
                    send_message("Reply to a GIF with /setgif to set it.", chat_id=chat_id)
                continue

            if text == "/clearmedia":
                save_media({"type": "none"})
                send_message("\u2705 Media cleared.", chat_id=chat_id)
                continue

            if text == "/status":
                media = load_media()
                state = load_schedule_state()
                chats = load_chats()
                send_message(
                    f"Bot Status\n\nScheduled: 6AM + 4PM PST\nSubscribers seen: {len(seen)}\nMedia: {media.get('type', 'none')}\nBroadcast chats: {len(chats)}\nLast scheduled: {state.get('last_sent_date')} hr {state.get('last_sent_hour')}",
                    chat_id=chat_id
                )
                continue

    except Exception as e:
        print(f"Admin command error: {e}")



def flush_update_queue():
    """Discard any pending updates so old commands don't fire on startup."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"timeout": 1},
            timeout=10
        )
        updates = r.json().get("result", [])
        if updates:
            latest = updates[-1]["update_id"] + 1
            save_offset(latest)
            print(f"Flushed {len(updates)} pending updates.")
    except Exception as e:
        print(f"Flush error: {e}")

def main():
    print("Orbis Telegram Bot starting...")
    flush_update_queue()
    seen = load_seen()
    first_run = True

    while True:
        try:
            handle_admin_commands(seen)
            data = fetch_all()
            stats = data.get("stats", {})
            earnings = data.get("earnings", {})
            subs_data = data.get("subscribers", {})
            apis_data = data.get("apis", [])

            current_ids, subs_list = get_subscriber_ids(subs_data)

            if first_run:
                broadcast(format_stats(stats, earnings, apis_data))
                save_seen(current_ids)
                seen = current_ids
                first_run = False
                print("Startup message sent.")
            else:
                new_ids = current_ids - seen
                if new_ids:
                    for uid in new_ids:
                        sub = next((s for s in subs_list if str(s.get("id") or s.get("userId") or s.get("subscriberId")) == uid), {})
                        broadcast(format_stats(stats, earnings, apis_data, new_sub=sub))
                        print(f"New subscriber: {uid}")
                    save_seen(current_ids)
                    seen = current_ids

                if should_send_scheduled():
                    print("Sending scheduled update...")
                    broadcast(format_stats(stats, earnings, apis_data))

        except Exception as e:
            print(f"Error: {e}")
            send_error(str(e))

        time.sleep(60)


if __name__ == "__main__":
    main()
