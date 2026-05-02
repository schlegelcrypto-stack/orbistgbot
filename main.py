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
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_IDS         = set(os.environ.get("ADMIN_IDS", TELEGRAM_CHAT_ID).split(","))
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET", "orbissecret123")
PORT              = int(os.environ.get("PORT", 8080))
PUBLIC_URL        = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
BOT_USERNAME      = os.environ.get("BOT_USERNAME", "")

OWNER_ORBIS_KEY   = os.environ.get("ORBIS_API_KEY", "")
ENV_CHATS         = os.environ.get("REGISTERED_CHATS", TELEGRAM_CHAT_ID)
ENV_MEDIA_TYPE    = os.environ.get("MEDIA_TYPE", "none")
ENV_MEDIA_FILE_ID = os.environ.get("MEDIA_FILE_ID", "")
ENV_USERS_CONFIG  = os.environ.get("USERS_CONFIG", "")

ERROR_COOLDOWN    = 3600
SCHEDULE_COOLDOWN = 4 * 3600

STATS_URL         = "https://orbisapi.com/api/provider/stats"
EARNINGS_URL      = "https://orbisapi.com/api/provider/earnings"
APIS_URL          = "https://orbisapi.com/api/provider/apis"
X402_URL          = "https://orbisapi.com/api/provider/x402-payments"

PST               = ZoneInfo("America/Los_Angeles")
SCHEDULED_HOURS   = [6, 16]

USERS_FILE        = "users.json"
CHATS_FILE        = "registered_chats.json"
PREV_STATS_FILE   = "prev_stats.json"

app = Flask(__name__)
last_error_time     = 0
last_scheduled_send = 0


# ── User management ───────────────────────────────────────

def load_users():
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE) as f:
                data = json.load(f)
                if data:
                    return data
    except Exception:
        pass
    # Fall back to USERS_CONFIG env var
    if ENV_USERS_CONFIG:
        try:
            return json.loads(ENV_USERS_CONFIG)
        except Exception as e:
            print(f"Error parsing USERS_CONFIG: {e}")
    return {}


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def get_user(user_id):
    return load_users().get(str(user_id))


def save_user(user_id, data):
    users = load_users()
    users[str(user_id)] = data
    save_users(users)


def delete_user(user_id):
    users = load_users()
    users.pop(str(user_id), None)
    save_users(users)


# ── Chats ─────────────────────────────────────────────────

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


# ── Prev stats ────────────────────────────────────────────

def load_prev_stats(user_id):
    try:
        if os.path.exists(PREV_STATS_FILE):
            with open(PREV_STATS_FILE) as f:
                return json.load(f).get(str(user_id), {})
    except Exception:
        pass
    return {}


def save_prev_stats(user_id, stats, earnings, x402={}):
    data = {}
    try:
        if os.path.exists(PREV_STATS_FILE):
            with open(PREV_STATS_FILE) as f:
                data = json.load(f)
    except Exception:
        pass
    subs_earned = earnings.get("totalEarningsUsdc", 0)
    x402_earned = x402.get("summary", {}).get("totalOwedUsdc", 0)
    data[str(user_id)] = {
        "totalSubscribers": stats.get("totalSubscribers", 0),
        "totalCalls": stats.get("totalCalls", 0),
        "apiCount": stats.get("apiCount", 0),
        "subsEarned": subs_earned,
        "x402Earned": x402_earned,
        "totalEarned": round(subs_earned + x402_earned, 2),
    }
    with open(PREV_STATS_FILE, "w") as f:
        json.dump(data, f)


# ── Init ──────────────────────────────────────────────────

def init_from_env():
    # Restore users from env var if file is missing/empty
    users = {}
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                users = json.load(f)
        except Exception:
            pass

    if not users and ENV_USERS_CONFIG:
        try:
            users = json.loads(ENV_USERS_CONFIG)
            save_users(users)
            print(f"Restored {len(users)} users from USERS_CONFIG env var")
        except Exception as e:
            print(f"Error restoring users: {e}")

    # Ensure owner exists
    if "owner" not in users and OWNER_ORBIS_KEY:
        users["owner"] = {
            "user_id": TELEGRAM_CHAT_ID,
            "orbis_key": OWNER_ORBIS_KEY,
            "name": "schlegel",
            "media_type": ENV_MEDIA_TYPE,
            "media_file_id": ENV_MEDIA_FILE_ID,
            "media_url": "",
            "is_owner": True
        }
        save_users(users)

    # Init chats
    env_chats = set(c.strip() for c in ENV_CHATS.split(",") if c.strip())
    if env_chats:
        existing = set()
        try:
            if os.path.exists(CHATS_FILE):
                with open(CHATS_FILE) as f:
                    existing = set(json.load(f))
        except Exception:
            pass
        with open(CHATS_FILE, "w") as f:
            json.dump(list(env_chats | existing), f)


# ── Orbis fetch ───────────────────────────────────────────

def fetch(url, api_key):
    r = requests.get(url, headers={"x-api-key": api_key}, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_user_data(orbis_key):
    urls = {
        "stats": STATS_URL,
        "earnings": EARNINGS_URL,
        "apis": APIS_URL,
        "x402": X402_URL,
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch, url, orbis_key): key for key, url in urls.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"Error fetching {key}: {e}")
                results[key] = {}
    return results


def validate_orbis_key(orbis_key):
    try:
        r = requests.get(STATS_URL, headers={"x-api-key": orbis_key}, timeout=10)
        return r.status_code == 200, r.json() if r.status_code == 200 else {}
    except Exception:
        return False, {}


# ── Telegram senders ──────────────────────────────────────

def get_keyboard():
    return {"inline_keyboard": [[
        {"text": "\U0001f7e3 ORBIS", "url": "https://orbisapi.com"},
        {"text": "\U0001f3c6 Vote", "url": "https://bags.fm/hackathon/apps"}
    ]]}


def send_with_media(caption, chat_id, media_type="none", media_file_id="", media_url=""):
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if media_type == "animation" and media_file_id:
        requests.post(f"{base}/sendAnimation", json={"chat_id": chat_id, "animation": media_file_id, "caption": caption, "reply_markup": get_keyboard()})
    elif media_type == "photo" and media_file_id:
        requests.post(f"{base}/sendPhoto", json={"chat_id": chat_id, "photo": media_file_id, "caption": caption, "reply_markup": get_keyboard()})
    elif media_type == "url" and media_url:
        requests.post(f"{base}/sendPhoto", json={"chat_id": chat_id, "photo": media_url, "caption": caption, "reply_markup": get_keyboard()})
    else:
        requests.post(f"{base}/sendMessage", json={"chat_id": chat_id, "text": caption, "reply_markup": get_keyboard()})


def broadcast(caption, user):
    for chat_id in load_chats():
        send_with_media(caption, chat_id, media_type=user.get("media_type","none"), media_file_id=user.get("media_file_id",""), media_url=user.get("media_url",""))


def send_message(text, chat_id):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})


def send_error(message):
    global last_error_time
    now = time.time()
    if now - last_error_time > ERROR_COOLDOWN:
        send_message(f"\u26a0\ufe0f Bot error: {message}", TELEGRAM_CHAT_ID)
        last_error_time = now


# ── Formatter ─────────────────────────────────────────────

def delta(current, previous, key, prefix=""):
    curr = current if isinstance(current, (int, float)) else 0
    prev = previous.get(key, 0) or 0
    diff = round(curr - prev, 2)
    if diff > 0:
        return f" (+{prefix}{diff})"
    return ""


def format_user_stats(user_id, user, data, show_delta=False):
    stats    = data.get("stats", {})
    earnings = data.get("earnings", {})
    apis_raw = data.get("apis", [])
    x402     = data.get("x402", {})

    sub_count    = stats.get("totalSubscribers", "N/A")
    total_calls  = stats.get("totalCalls", "N/A")
    api_count    = stats.get("apiCount", "N/A")
    subs_earned  = round(earnings.get("totalEarningsUsdc", 0), 2)
    x402_earned  = round(x402.get("summary", {}).get("totalOwedUsdc", 0), 2)
    total_earned = round(subs_earned + x402_earned, 2)
    now_pst      = datetime.now(PST).strftime("%b %d, %Y %I:%M %p PST")
    name         = user.get("name", "Unknown")

    prev        = load_prev_stats(user_id) if show_delta else {}
    d_subs      = delta(sub_count, prev, "totalSubscribers") if show_delta else ""
    d_calls     = delta(total_calls, prev, "totalCalls") if show_delta else ""
    d_apis      = delta(api_count, prev, "apiCount") if show_delta else ""
    d_sub_earn  = delta(subs_earned, prev, "subsEarned", "$") if show_delta else ""
    d_x402      = delta(x402_earned, prev, "x402Earned", "$") if show_delta else ""
    prev_total  = round((prev.get("subsEarned", 0) or 0) + (prev.get("x402Earned", 0) or 0), 2)
    d_total     = f" (+${round(total_earned - prev_total, 2)})" if show_delta and total_earned > prev_total else ""

    apis = apis_raw if isinstance(apis_raw, list) else apis_raw.get("apis", [])
    apis = sorted(apis, key=lambda a: a.get("subscriberCount") or a.get("subscribers") or 0, reverse=True)

    lines = [
        f"{name} Orbis API Tracker\n",
        f"\U0001f465 Total Subscribers: {sub_count}{d_subs}",
        f"\U0001f4ca Total API Calls:   {total_calls}{d_calls}",
        f"\U0001f517 APIs Listed:       {api_count}{d_apis}",
        "",
        f"\U0001f4b0 Subs Earned:   ${subs_earned} USDC{d_sub_earn}",
        f"\u26a1 x402 Earned:   ${x402_earned} USDC{d_x402}",
        f"\U0001f4ca Total Earned:  ${total_earned} USDC{d_total}",
        "",
        "Top APIs:",
    ]
    for a in apis[:5]:
        n = a.get("name") or a.get("apiName") or "Unnamed"
        s = a.get("subscriberCount") or a.get("subscribers") or 0
        lines.append(f"  - {n}: {s} subs")

    lines += ["", now_pst, "", f"*This tracks '{name}' only for fun, not a full network tracker"]
    return "\n".join(lines)


def broadcast_user(user_id, user, show_delta=False):
    try:
        data    = fetch_user_data(user.get("orbis_key", ""))
        caption = format_user_stats(user_id, user, data, show_delta=show_delta)
        broadcast(caption, user)
        if show_delta:
            save_prev_stats(user_id, data.get("stats",{}), data.get("earnings",{}), data.get("x402",{}))
    except Exception as e:
        print(f"Error broadcasting user {user_id}: {e}")


# ── Command handler ───────────────────────────────────────

def handle_command(msg):
    chat_id    = str(msg.get("chat", {}).get("id", ""))
    user_id    = str(msg.get("from", {}).get("id", ""))
    username   = msg.get("from", {}).get("username", "") or msg.get("from", {}).get("first_name", "User")
    text       = msg.get("text", "").split("@")[0].strip()
    is_admin   = user_id in ADMIN_IDS
    is_private = msg.get("chat", {}).get("type") == "private"

    print(f"Command: {text} from {user_id} ({username}) in {chat_id}")

    if text.startswith("/start"):
        if is_private:
            send_message(
                f"👋 Welcome to the Orbis API Tracker!\n\n"
                f"1\ufe0f\u20e3 Get your API key from orbisapi.com\n"
                f"   \u2192 Provider Dashboard \u2192 Generate API Key\n\n"
                f"2\ufe0f\u20e3 Send: /register YOUR_API_KEY\n\n"
                f"3\ufe0f\u20e3 Set your name: /setname Your Name\n\n"
                f"4\ufe0f\u20e3 Set your GIF: send a GIF then reply /mygif\n\n"
                f"Type /help to see all commands.",
                chat_id=chat_id
            )
        return

    if text == "/addme":
        bot_link = f"https://t.me/{BOT_USERNAME}?start=register" if BOT_USERNAME else "the bot directly"
        send_message(
            f"👋 Want to add your Orbis stats to this tracker?\n\n"
            f"Message the bot privately:\n👉 {bot_link}\n\n"
            f"Your API key stays private!",
            chat_id=chat_id
        )
        return

    if text.startswith("/register "):
        if not is_private:
            send_message("\u26a0\ufe0f Please register privately to keep your API key safe!", chat_id=chat_id)
            return
        orbis_key = text[len("/register "):].strip()
        send_message("\u23f3 Validating your API key...", chat_id=chat_id)
        valid, stats = validate_orbis_key(orbis_key)
        if not valid:
            send_message("\u274c Invalid API key. Please check and try again.", chat_id=chat_id)
            return
        existing  = get_user(user_id) or {}
        user_data = {
            "user_id": user_id,
            "orbis_key": orbis_key,
            "name": existing.get("name", username),
            "media_type": existing.get("media_type", "none"),
            "media_file_id": existing.get("media_file_id", ""),
            "media_url": existing.get("media_url", ""),
        }
        save_user(user_id, user_data)
        send_message(
            f"\u2705 Registered!\n\n"
            f"Subscribers: {stats.get('totalSubscribers','?')} | APIs: {stats.get('apiCount','?')}\n\n"
            f"/setname Your Name\n/mygif — reply to a GIF\n/mystats — preview your card",
            chat_id=chat_id
        )
        return

    if text.startswith("/setname "):
        user = get_user(user_id)
        if not user:
            send_message("Not registered. Use /register first.", chat_id=chat_id)
            return
        user["name"] = text[len("/setname "):].strip()
        save_user(user_id, user)
        send_message(f"\u2705 Name set to: {user['name']}", chat_id=chat_id)
        return

    if text == "/mygif":
        user = get_user(user_id)
        if not user:
            send_message("Not registered. Use /register first.", chat_id=chat_id)
            return
        reply = msg.get("reply_to_message", {})
        if reply.get("animation"):
            user["media_type"]    = "animation"
            user["media_file_id"] = reply["animation"]["file_id"]
            save_user(user_id, user)
            send_message("\u2705 GIF saved!", chat_id=chat_id)
        else:
            send_message("Reply to a GIF with /mygif to set it.", chat_id=chat_id)
        return

    if text == "/myphoto":
        user = get_user(user_id)
        if not user:
            send_message("Not registered. Use /register first.", chat_id=chat_id)
            return
        reply = msg.get("reply_to_message", {})
        if reply.get("photo"):
            user["media_type"]    = "photo"
            user["media_file_id"] = reply["photo"][-1]["file_id"]
            save_user(user_id, user)
            send_message("\u2705 Photo saved!", chat_id=chat_id)
        else:
            send_message("Reply to a photo with /myphoto to set it.", chat_id=chat_id)
        return

    if text == "/mystats":
        user = get_user(user_id)
        if not user:
            send_message("Not registered. Use /register first.", chat_id=chat_id)
            return
        send_message("\u23f3 Fetching your stats...", chat_id=chat_id)
        data    = fetch_user_data(user["orbis_key"])
        caption = format_user_stats(user_id, user, data)
        send_with_media(caption, chat_id, media_type=user.get("media_type","none"), media_file_id=user.get("media_file_id",""))
        return

    if text == "/unregister":
        delete_user(user_id)
        send_message("\u2705 Removed from tracker.", chat_id=chat_id)
        return

    if text == "/help":
        if is_private:
            help_text = (
                "Orbis Tracker Commands\n\n"
                "/register [key] - Register your Orbis API key\n"
                "/setname [name] - Set your display name\n"
                "/mygif - Reply to a GIF to set your image\n"
                "/myphoto - Reply to a photo to set your image\n"
                "/mystats - Preview your tracker card\n"
                "/unregister - Remove yourself\n"
                "/help - Show commands\n"
            )
        else:
            help_text = (
                "Orbis Tracker Commands\n\n"
                "/addme - Join the tracker privately\n"
                "/help - Show commands\n"
            )
        if is_admin:
            help_text += (
                "\nAdmin Only:\n"
                "/schlegelapi - Post your stats here\n"
                "/broadcastall - Post all users to community\n"
                "/exportusers - Export all user config (save before deploying!)\n"
                "/addchat - Add this chat to broadcasts\n"
                "/removechat - Remove this chat\n"
                "/listchats - Show chats\n"
                "/listusers - Show registered users\n"
                "/setgif - Reply to GIF (owner media)\n"
                "/setphoto - Reply to photo (owner media)\n"
                "/status - Bot status\n"
            )
        send_message(help_text, chat_id=chat_id)
        return

    if not is_admin:
        return

    if text == "/schlegelapi":
        user = get_user("owner") or {"orbis_key": OWNER_ORBIS_KEY, "name": "schlegel", "media_type": ENV_MEDIA_TYPE, "media_file_id": ENV_MEDIA_FILE_ID}
        data    = fetch_user_data(user["orbis_key"])
        caption = format_user_stats("owner", user, data, show_delta=True)
        send_with_media(caption, chat_id, media_type=user.get("media_type","none"), media_file_id=user.get("media_file_id",""))
        save_prev_stats("owner", data.get("stats",{}), data.get("earnings",{}), data.get("x402",{}))
        return

    if text == "/broadcastall":
        users = load_users()
        if not users:
            send_message("No users registered.", chat_id=chat_id)
            return
        send_message(f"\U0001f4e1 Broadcasting {len(users)} users...", chat_id=chat_id)
        for uid, user in users.items():
            broadcast_user(uid, user, show_delta=True)
            time.sleep(1)
        return

    if text == "/exportusers":
        users = load_users()
        if not users:
            send_message("No users registered.", chat_id=chat_id)
            return
        export = json.dumps(users, indent=2)
        # Send as a text message (Telegram caps at 4096 chars, split if needed)
        if len(export) <= 4000:
            send_message(
                f"Current USERS_CONFIG value — paste this into Railway Variables before deploying:\n\n"
                f"<code>{export}</code>",
                chat_id=chat_id
            )
            # Send raw version too for easy copying
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": export}
            )
        else:
            # Send as a document if too long
            import io
            file_bytes = export.encode("utf-8")
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={"chat_id": chat_id, "caption": "USERS_CONFIG — paste into Railway Variables before deploying"},
                files={"document": ("users_config.json", io.BytesIO(file_bytes), "application/json")}
            )
        return

    if text == "/listusers":
        users = load_users()
        if not users:
            send_message("No users registered.", chat_id=chat_id)
            return
        lines = [f"Registered users ({len(users)}):"]
        for uid, u in users.items():
            media = u.get("media_type", "none")
            lines.append(f"  - {u.get('name','Unknown')} (id: {uid}, media: {media})")
        send_message("\n".join(lines), chat_id=chat_id)
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

    if text == "/setgif":
        reply = msg.get("reply_to_message", {})
        if reply.get("animation"):
            file_id = reply["animation"]["file_id"]
            users = load_users()
            if "owner" in users:
                users["owner"]["media_type"]    = "animation"
                users["owner"]["media_file_id"] = file_id
                save_users(users)
            send_message(f"\u2705 Owner GIF saved!\nRailway: MEDIA_TYPE=animation\nMEDIA_FILE_ID={file_id}", chat_id=chat_id)
        else:
            send_message("Reply to a GIF with /setgif.", chat_id=chat_id)
        return

    if text == "/setphoto":
        reply = msg.get("reply_to_message", {})
        if reply.get("photo"):
            file_id = reply["photo"][-1]["file_id"]
            users = load_users()
            if "owner" in users:
                users["owner"]["media_type"]    = "photo"
                users["owner"]["media_file_id"] = file_id
                save_users(users)
            send_message(f"\u2705 Owner photo saved!\nRailway: MEDIA_TYPE=photo\nMEDIA_FILE_ID={file_id}", chat_id=chat_id)
        else:
            send_message("Reply to a photo with /setphoto.", chat_id=chat_id)
        return

    if text == "/status":
        users = load_users()
        chats = load_chats()
        send_message(
            f"Bot Status\n\nMode: Webhook\nScheduled: 6AM + 4PM PST\nUsers: {len(users)}\nChats: {len(chats)}\nAdmin IDs: {ADMIN_IDS}\nBot: @{BOT_USERNAME}",
            chat_id=chat_id
        )
        return


# ── Webhook ───────────────────────────────────────────────

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


# ── Scheduler ─────────────────────────────────────────────

def scheduler_loop():
    global last_scheduled_send
    last_scheduled_send = time.time() - 3 * 3600

    while True:
        try:
            now_pst = datetime.now(PST)
            if now_pst.hour in SCHEDULED_HOURS and time.time() - last_scheduled_send > SCHEDULE_COOLDOWN:
                print("Sending scheduled updates...")
                users = load_users()
                for uid, user in users.items():
                    broadcast_user(uid, user, show_delta=True)
                    time.sleep(1)
                last_scheduled_send = time.time()
        except Exception as e:
            print(f"Scheduler error: {e}")
            send_error(str(e))
        time.sleep(60)


# ── Startup ───────────────────────────────────────────────

def register_webhook():
    if not PUBLIC_URL:
        print("WARNING: RAILWAY_PUBLIC_DOMAIN not set")
        return
    webhook_url = f"https://{PUBLIC_URL}/webhook/{WEBHOOK_SECRET}"
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        json={"url": webhook_url, "drop_pending_updates": True}
    )
    print(f"Webhook: {r.json()}")


if __name__ == "__main__":
    init_from_env()
    register_webhook()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
