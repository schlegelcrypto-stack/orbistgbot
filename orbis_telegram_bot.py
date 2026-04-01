import requests
import time
import json
import os

# ─────────────────────────────────────────
#  CONFIG — fill these in before running
# ─────────────────────────────────────────
ORBIS_API_KEY    = os.environ["orbis_87ce4d3027a694fbeb570c098d317d44a186739c5acaf8a4141fabcaa0546966"]
TELEGRAM_TOKEN   = os.environ["8642905195:AAHpIFNV4I-fOQ-3S3BJYP_zlbW9nAor00I"]
TELEGRAM_CHAT_ID = os.environ["874482516"]
POLL_INTERVAL   = 60  # seconds between checks
# ─────────────────────────────────────────

ORBIS_HEADERS = {"x-api-key": ORBIS_API_KEY}

EARNINGS_URL    = "https://orbisapi.com/api/provider/earnings"
SUBSCRIBERS_URL = "https://orbisapi.com/api/provider/subscribers"
APIS_URL        = "https://orbisapi.com/api/provider/apis"

SEEN_FILE = "seen_subscribers.json"


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })


def fetch(url):
    r = requests.get(url, headers=ORBIS_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def get_subscriber_ids(subscribers_data):
    """Extract a unique ID for each subscriber. Adjust field names if needed."""
    ids = set()
    subs = subscribers_data if isinstance(subscribers_data, list) else subscribers_data.get("subscribers", [])
    for s in subs:
        uid = s.get("id") or s.get("userId") or s.get("subscriberId")
        if uid:
            ids.add(str(uid))
    return ids, subs


def format_new_subscriber_msg(sub, earnings):
    total     = earnings.get("totalEarned") or earnings.get("total_earned", "N/A")
    monthly   = earnings.get("thisMonthRevenue") or earnings.get("monthly_revenue", "N/A")
    pending   = earnings.get("pendingPayouts") or earnings.get("pending_payouts", "N/A")
    sub_count = earnings.get("subscriberCount") or earnings.get("subscriber_count", "N/A")

    name    = sub.get("name") or sub.get("username") or sub.get("email") or "Unknown"
    api     = sub.get("apiName") or sub.get("api_name") or sub.get("apiId") or "Unknown API"
    plan    = sub.get("plan") or sub.get("tier") or ""

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
        f"  Total Earned:     ${total}",
        f"  This Month:       ${monthly}",
        f"  Pending Payout:   ${pending}",
        f"  Total Subscribers: {sub_count}",
    ]
    return "\n".join(lines)


def format_startup_msg(earnings, apis_data):
    total     = earnings.get("totalEarned") or earnings.get("total_earned", "N/A")
    monthly   = earnings.get("thisMonthRevenue") or earnings.get("monthly_revenue", "N/A")
    sub_count = earnings.get("subscriberCount") or earnings.get("subscriber_count", "N/A")

    apis = apis_data if isinstance(apis_data, list) else apis_data.get("apis", [])
    api_lines = ""
    for a in apis[:5]:  # show up to 5
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        api_lines += f"\n  • {n} — {s} subscribers"

    return (
        "🤖 <b>Orbis Bot Online</b>\n\n"
        f"💰 Total Earned: ${total}\n"
        f"📅 This Month:   ${monthly}\n"
        f"👥 Subscribers:  {sub_count}\n"
        f"\n📦 <b>Your APIs:</b>{api_lines}"
    )


def main():
    print("Orbis Telegram Bot starting...")
    seen = load_seen()
    first_run = len(seen) == 0

    while True:
        try:
            earnings    = fetch(EARNINGS_URL)
            subs_data   = fetch(SUBSCRIBERS_URL)
            apis_data   = fetch(APIS_URL)

            current_ids, subs_list = get_subscriber_ids(subs_data)

            if first_run:
                # On first run, send a status message and save current state
                msg = format_startup_msg(earnings, apis_data)
                send_telegram(msg)
                save_seen(current_ids)
                seen = current_ids
                first_run = False
                print("Startup message sent.")
            else:
                new_ids = current_ids - seen
                if new_ids:
                    for uid in new_ids:
                        # Find subscriber details
                        sub = next(
                            (s for s in subs_list if str(s.get("id") or s.get("userId") or s.get("subscriberId")) == uid),
                            {}
                        )
                        msg = format_new_subscriber_msg(sub, earnings)
                        send_telegram(msg)
                        print(f"New subscriber alert sent: {uid}")
                    save_seen(current_ids)
                    seen = current_ids
                else:
                    print(f"No new subscribers. Checking again in {POLL_INTERVAL}s...")

        except Exception as e:
            print(f"Error: {e}")
            send_telegram(f"⚠️ Orbis Bot error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
