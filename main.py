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
last_error_time = 0


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})


def send_error(message):
    global last_error_time
    now = time.time()
    if now - last_error_time > ERROR_COOLDOWN:
        send_telegram(f"Warning Orbis Bot error: {message}")
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


def get_val(d, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return "N/A"


def format_new_sub(sub, stats):
    total = get_val(stats, "totalEarned", "total_earned", "totalRevenue", "earnings")
    monthly = get_val(stats, "thisMonthRevenue", "monthly_revenue", "monthlyRevenue", "monthEarnings")
    pending = get_val(stats, "pendingPayouts", "pending_payouts", "pendingPayout", "pending")
    sub_count = get_val(stats, "subscriberCount", "subscriber_count", "totalSubscribers", "subscribers")
    name = sub.get("name") or sub.get("username") or sub.get("email") or "Unknown"
    api = sub.get("apiName") or sub.get("api_name") or sub.get("apiId") or "Unknown API"
    plan = sub.get("plan") or sub.get("tier") or ""
    lines = [
        "New Subscriber!",
        "",
        f"User: {name}",
        f"API: {api}",
    ]
    if plan:
        lines.append(f"Plan: {plan}")
    lines += [
        "",
        "Earnings Summary",
        f"  Total Earned:      ${total}",
        f"  This Month:        ${monthly}",
        f"  Pending Payout:    ${pending}",
        f"  Total Subscribers: {sub_count}",
    ]
    return "\n".join(lines)


def format_startup(stats, apis_data):
    total = get_val(stats, "totalEarned", "total_earned", "totalRevenue", "earnings")
    monthly = get_val(stats, "thisMonthRevenue", "monthly_revenue", "monthlyRevenue", "monthEarnings")
    sub_count = get_val(stats, "subscriberCount", "subscriber_count", "totalSubscribers", "subscribers")
    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])
    api_lines = ""
    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        api_lines += f"\n  - {n}: {s} subscribers"
    return (
        "Orbis Bot Online\n\n"
        f"Total Earned: ${total}\n"
        f"This Month:   ${monthly}\n"
        f"Subscribers:  {sub_count}\n"
        f"\nYour APIs (top 5):{api_lines}\n\n"
        f"Polling every 5 minutes\n\n"
        f"DEBUG STATS: {json.dumps(stats)}"
    )


def main():
    print("Orbis Telegram Bot starting...")
    seen = load_seen()
    first_run = len(seen) == 0

    while True:
        try:
            stats = fetch(STATS_URL)
            subs_data = fetch(SUBSCRIBERS_URL)
            apis_data = []
            try:
                apis_data = fetch(APIS_URL)
            except Exception as e2:
                print(f"APIs error: {e2}")
            current_ids, subs_list = get_subscriber_ids(subs_data)
            if first_run:
                send_telegram(format_startup(stats, apis_data))
                save_seen(current_ids)
                seen = current_ids
                first_run = False
            else:
                new_ids = current_ids - seen
                if new_ids:
                    for uid in new_ids:
                        sub = next(
                            (s for s in subs_list if str(
                                s.get("id") or s.get("userId") or s.get("subscriberId")
                            ) == uid), {}
                        )
                        send_telegram(format_new_sub(sub, stats))
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
