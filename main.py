import requests
import time
import json
import os

ORBIS_API_KEY = os.environ.get("ORBIS_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = 300
ERROR_COOLDOWN = 3600

ORBIS_HEADERS = {"x-api-key": ORBIS_API_KEY}
STATS_URL = "https://orbisapi.com/api/provider/stats"
SUBSCRIBERS_URL = "https://orbisapi.com/api/provider/subscribers"
APIS_URL = "https://orbisapi.com/api/provider/apis"
SEEN_FILE = "seen_subscribers.json"
MEDIA_FILE = "media_config.json"
OFFSET_FILE = "update_offset.json"

last_error_time = 0


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


def send_with_media(caption):
    media = load_media()
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    keyboard = {"inline_keyboard": [[
        {"text": "\U0001f7e3 ORBIS", "url": "https://orbisapi.com"},
        {"text": "\U0001f3c6 Vote", "url": "https://bags.fm/hackathon/apps"}
    ]]}

    if media["type"] == "photo":
        requests.post(f"{base}/sendPhoto", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": media["file_id"],
            "caption": caption,
            "reply_markup": keyboard
        })
    elif media["type"] == "animation":
        requests.post(f"{base}/sendAnimation", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "animation": media["file_id"],
            "caption": caption,
            "reply_markup": keyboard
        })
    elif media["type"] == "url":
        requests.post(f"{base}/sendPhoto", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": media["url"],
            "caption": caption,
            "reply_markup": keyboard
        })
    else:
        requests.post(f"{base}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "reply_markup": keyboard
        })


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})


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


def format_stats(stats, apis_data, new_sub=None):
    sub_count = stats.get("totalSubscribers", "N/A")
    total_calls = stats.get("totalCalls", "N/A")
    api_count = stats.get("apiCount", "N/A")
    earnings = stats.get("totalEarned", stats.get("earnings", "0.00"))

    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])

    if new_sub:
        name = new_sub.get("name") or new_sub.get("username") or new_sub.get("email") or "Unknown"
        api = new_sub.get("apiName") or new_sub.get("api_name") or "Unknown API"
        plan = new_sub.get("plan") or new_sub.get("tier") or "Free"
        header = f"\U0001f389 New Subscriber!\n{name} subscribed to {api} ({plan})\n"
    else:
        header = "Schlegel Orbis API Tracker\n"

    lines = [
        header,
        f"\U0001f465 Total Subscribers: {sub_count}",
        f"\U0001f4ca Total API Calls:   {total_calls}",
        f"\U0001f517 APIs Listed:       {api_count}",
        "",
        f"\U0001f4b0 Total Earnings: ${earnings} \U0001f4b0",
        "",
        "Top APIs:",
    ]

    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        c = a.get("totalCalls") or a.get("calls") or 0
        lines.append(f"  - {n}: {s} subs, {c} calls")

    return "\n".join(lines)


def handle_admin_commands(seen):
    offset = load_offset()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 1}, timeout=10)
        updates = r.json().get("result", [])
        if not updates:
            return

        for update in updates:
            offset = update.get("update_id") + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if chat_id != TELEGRAM_CHAT_ID:
                continue

            text = msg.get("text", "")

            # Handle direct photo upload
            if msg.get("photo"):
                file_id = msg["photo"][-1]["file_id"]
                save_media({"type": "photo", "file_id": file_id})
                send_message("\u2705 Image saved! Bot will use this photo.")
                continue

            # Handle GIF/animation upload
            if msg.get("animation"):
                file_id = msg["animation"]["file_id"]
                save_media({"type": "animation", "file_id": file_id})
                send_message("\u2705 GIF saved! Bot will use this animation.")
                continue

            # Handle URL command
            if text.startswith("/setimage "):
                new_url = text[len("/setimage "):].strip()
                save_media({"type": "url", "url": new_url})
                send_message(f"\u2705 Image URL updated!")
                continue

            if text == "/clearmedia":
                save_media({"type": "none"})
                send_message("\u2705 Media cleared. Bot will send text only.")
                continue

            if text == "/status":
                media = load_media()
                send_message(
                    f"Bot Status\n\n"
                    f"Poll interval: {POLL_INTERVAL // 60} min\n"
                    f"Seen subscribers: {len(seen)}\n"
                    f"Media type: {media.get('type', 'none')}"
                )
                continue

            if text == "/help":
                send_message(
                    "Admin Commands:\n\n"
                    "Send any photo - set as bot image\n"
                    "Send any GIF - set as bot animation\n"
                    "/setimage [url] - set image from URL\n"
                    "/clearmedia - remove media\n"
                    "/status - show bot status\n"
                    "/help - show this message"
                )
                continue

        save_offset(offset)
    except Exception as e:
        print(f"Admin command error: {e}")


def main():
    print("Orbis Telegram Bot starting...")
    seen = load_seen()
    first_run = len(seen) == 0

    while True:
        try:
            handle_admin_commands(seen)

            stats = fetch(STATS_URL)
            subs_data = fetch(SUBSCRIBERS_URL)
            apis_data = []
            try:
                apis_data = fetch(APIS_URL)
            except Exception as e2:
                print(f"APIs error: {e2}")

            current_ids, subs_list = get_subscriber_ids(subs_data)

            if first_run:
                send_with_media(format_stats(stats, apis_data))
                save_seen(current_ids)
                seen = current_ids
                first_run = False
                print("Startup message sent.")
            else:
                new_ids = current_ids - seen
                if new_ids:
                    for uid in new_ids:
                        sub = next(
                            (s for s in subs_list if str(
                                s.get("id") or s.get("userId") or s.get("subscriberId")
                            ) == uid), {}
                        )
                        send_with_media(format_stats(stats, apis_data, new_sub=sub))
                        print(f"New subscriber: {uid}")
                    save_seen(current_ids)
                    seen = current_ids
                else:
                    print(f"No new subscribers. Next check in {POLL_INTERVAL // 60} min...")

        except Exception as e:
            print(f"Error: {e}")
            send_error(str(e))

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
