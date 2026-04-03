import requests
import time
import json
import os

# Config from Railway environment variables
ORBIS_API_KEY    = os.environ.get("ORBIS_API_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL    = 300  # 5 minutes
ERROR_COOLDOWN   = 3600  # 1 hour between error alerts

ORBIS_HEADERS = {"x-api-key": ORBIS_API_KEY}

EARNINGS_URL    = "https://orbisapi.com/api/provider/earnings"
SUBSCRIBERS_URL = "https://orbisapi.com/api/provider/subscribers"
APIS_URL        = "https://orbisapi.com/api/provider/apis"

SEEN_FILE = "seen_subscribers.json"
last_error_time = 0


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })


def send_error(message):
    global last_error_time
    now = time.time()
    if now - last_error_time > ERROR_COOLDOWN:
        send_telegram(f"⚠️ Orbis Bot error: {message}")
        last_error_time = now
    else:
        print(f"Error suppressed (cooldown active): {message}")


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
    """Try multiple possible field names and return the first match."""
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return "N/A"


def format_new_sub(sub, earnings):
    total     = get_val(earnings, "totalEarned", "total_earned", "totalRevenue", "total_revenue", "revenue")
    monthly   = get_val(earnings, "thisMonthRevenue", "monthly_revenue", "monthlyRevenue", "this_month")
    pending   = get_val(earnings, "pendingPayouts", "pending_payouts", "pendingPayout", "pending")
    sub_count = get_val(earnings, "subscriberCount", "subscriber_count", "totalSubscribers", "total_subscribers")
    name      = sub.get("name") or sub.get("username") or sub.get("email") or "Unknown"
    api       = sub.get("apiName") or sub.get("api_name") or sub.get("apiId") or "Unknown API"
    plan      = sub.get("plan") or sub.get("tier") or ""

    lines = [
        "🎉 <b>New Subscriber!</b>",
        "",
        f"👤 <b>User:</b> {name}",
        f"📦 <b>API:</b> {api}",
    ]
    if plan:
        lines.append(f"📋 <b>Plan:</b> {plan}")
    lines += [
        "",
        "💰 <b>Earnings Summary</b>",
        f"  Total Earned:      ${total}",
        f"  This Month:        ${monthly}",
        f"  Pending Payout:    ${pending}",
        f"  Total Subscribers: {sub_count}",
    ]
    return "\n".join(lines)


def format_startup(earnings, apis_data):
    # Debug: print all keys so we can see exact field names in logs
    print("EARNINGS KEYS:", list(earnings.keys()) if isinstance(earnings, dict) else earnings)
    print("EARNINGS DATA:", json.dumps(earnings))

    total     = get_val(earnings, "totalEarned", "total_earned", "totalRevenue", "total_revenue", "revenue")
    monthly   = get_val(earnings, "thisMonthRevenue", "monthly_revenue", "monthlyRevenue", "this_month")
    sub_count = get_val(earnings, "subscriberCount", "subscriber_count", "totalSubscribers", "total_subscribers")
    apis      = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])

    api_lines = ""
    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        api_lines += f"\n  • {n} — {s} subscribers"

    return (
        "🤖 <b>Orbis Bot Online</b>\n\n"
        f"💰 Total Earned: ${total}\n"
        f"📅 This Month:   ${monthly}\n"
        f"👥 Subscribers:  {sub_count}\n"
        f"\n📦 <b>Your APIs (top 5):</b>{api_lines}\n\n"
        f"🔄 Polling every 5 minutes"
    )


def main():
    print("Orbis Telegram Bot starting...")
    seen = load_seen()
    first_run = len(seen) == 0

    while True:
        try:
            earnings  = fetch(EARNINGS_URL)
            subs_data = fetch(SUBSCRIBERS_URL)

            try:
                apis_data = fetch(APIS_URL)
            except Exception as e:
                print(f"APIs endpoint error (non-fatal): {e}")
                apis_data = []

            current_ids, subs_list = get_subscriber_ids(subs_data)

            if first_run:
                send_telegram(format_startup(earnings, apis_data))
                save_seen(current_ids)
                seen = current_ids
                first_run = False
                print("Startup message sent.")
            else:
                new_ids = current_ids - seen
                if new_ids:
                    for uid in new_ids:
                        sub = next(
                            (s for s in subs_list if str(s.get("id") or s.get("userId") or s.get("subscriberId")) == uid),
                            {}
                        )
                        send_telegram(format_new_sub(sub, earnings))
                        print(f"New subscriber alert sent: {uid}")
                    save_seen(current_ids)
                    seen = current_ids
                else:
                    print(f"No new subscribers. Checking again in {POLL_INTERVAL // 60} minutes...")

        except Exception as e:
            print(f"Error: {e}")
            send_error(str(e))

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
