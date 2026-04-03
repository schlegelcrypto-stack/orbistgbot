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
MEDIA_FILE = "media_url.txt"
last_error_time = 0

DEFAULT_IMAGE = "https://i.imgur.com/tlMpKo1.png"

INLINE_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "ORBIS", "url": "https://orbisapi.com"},
        {"text": "Vote", "url": "https://bags.fm/hackathon/apps"}
    ]]
}


def get_media_url():
    if os.path.exists(MEDIA_FILE):
        with open(MEDIA_FILE) as f:
            url = f.read().strip()
            if url:
                return url
    return DEFAULT_IMAGE


def set_media_url(url):
    with open(MEDIA_FILE, "w") as f:
        f.write(url.strip())


def send_photo(caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": get_media_url(),
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": INLINE_KEYBOARD
    })


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })


def send_error(message):
    global last_error_time
    now = time.time()
    if now - last_error_time > ERROR_COOLDOWN:
        send_message(f"Warning: {message}")
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
    earnings = stats.get("totalEarned", stats.get("earnings", "$0.00"))
    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])

    if new_sub:
        name = new_sub.get("name") or new_sub.get("username") or new_sub.get("email") or "Unknown"
        api = new_sub.get("apiName") or new_sub.get("api_name") or "Unknown API"
        plan = new_sub.get("plan") or new_sub.get("tier") or "Free"
        header = f"New Subscriber!\n{name} - {api} ({plan})\n\n"
    else:
        header = "Schlegel Orbis API Tracker\n\n"

    lines = [
        header,
        f"Total Subscribers: {sub_count}",
        f"Total API Calls:   {total_calls}",
        f"APIs Listed:       {api_count}",
        "",
        f"Total Earnings: ${earnings}",
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, timeout=10)
        updates = r.json().get("result", [])
        if not updates:
            return

        last_update_id = None
        for update in updates:
            last_update_id = update.get("update_id")
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if chat_id != TELEGRAM_CHAT_ID:
                continue

            if text.startswith("/setimage "):
                new_url = text[len("/setimage "):].strip()
                set_media_url(new_url)
                send_message(f"Image updated!")

            elif text == "/status":
                send_message(
                    f"Bot is running\n"
                    f"Poll interval: {POLL_INTERVAL // 60} min\n"
                    f"Seen subscribers: {len(seen)}\n"
                    f"Media: {get_media_url()}"
                )

            elif text == "/help":
                send_message(
                    "Admin Commands:\n\n"
                    "/setimage [url] - Change the bot image\n"
                    "/status - Show bot status\n"
                    "/help - Show this message"
                )

        if last_update_id is not None:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": last_update_id + 1},
                timeout=10
            )
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
                send_photo(format_stats(stats, apis_data))
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
                        send_photo(format_stats(stats, apis_data, new_sub=sub))
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
