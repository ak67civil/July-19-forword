"""
╔══════════════════════════════════════════════════════════════╗
║           PROFESSIONAL TELEGRAM FORWARD BOT                  ║
║           Built with Pyrogram | Owner + Admin System         ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import logging
import sqlite3
import json
import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatType
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from pyrogram.errors import FloodWait, MessageNotModified, ChatAdminRequired

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_ID    = int(os.environ["TELEGRAM_API_ID"])
API_HASH  = os.environ["TELEGRAM_API_HASH"]
OWNER_ID  = int(os.environ["OWNER_ID"])
LOG_CHANNEL = os.environ.get("LOG_CHANNEL", "")  # Optional

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Client(
    "forward_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════
DB_PATH = "forward_bot.db"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS props (
            key TEXT PRIMARY KEY, value TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            state   TEXT DEFAULT '',
            temp    TEXT DEFAULT '{}'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS forward_jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER,
            source_id   TEXT,
            target_id   TEXT,
            topic_id    INTEGER DEFAULT 0,
            from_msg    INTEGER,
            to_msg      INTEGER,
            forwarded   INTEGER DEFAULT 0,
            failed      INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            created_at  INTEGER
        )""")
        c.commit()

def get_prop(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM props WHERE key=?", (key,)).fetchone()
        if row is None: return default
        try: return json.loads(row["value"])
        except: return row["value"]

def set_prop(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO props(key,value) VALUES(?,?)",
                  (key, json.dumps(value)))
        c.commit()

def get_session(uid):
    with db() as c:
        row = c.execute("SELECT state,temp FROM sessions WHERE user_id=?", (uid,)).fetchone()
        if row: return row["state"] or "", json.loads(row["temp"] or "{}")
        return "", {}

def set_session(uid, state="", temp=None):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO sessions(user_id,state,temp) VALUES(?,?,?)",
                  (uid, state, json.dumps(temp or {})))
        c.commit()

# ═══════════════════════════════════════════════════════════════
# ADMIN HELPERS
# ═══════════════════════════════════════════════════════════════
def get_admins():
    lst = get_prop("admins", [])
    if OWNER_ID not in lst: lst.append(OWNER_ID)
    return lst

def is_admin(uid):
    if uid == OWNER_ID: return True
    admins = get_prop("admins", [])
    if uid not in admins: return False
    exp = get_prop(f"admin_exp_{uid}", 0)
    if exp and exp < time.time(): return False
    return True

def add_admin(uid, days=30):
    admins = get_admins()
    if uid not in admins: admins.append(uid)
    set_prop("admins", admins)
    set_prop(f"admin_exp_{uid}", time.time() + days * 86400)

def remove_admin(uid):
    admins = [a for a in get_admins() if a != uid]
    set_prop("admins", admins)
    set_prop(f"admin_exp_{uid}", 0)

def format_dt(ts):
    if not ts: return "N/A"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")

# ═══════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════
def kb(*rows):
    return InlineKeyboardMarkup(list(rows))

def btn(text, data=None, url=None):
    if url: return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=data)

def home_kb(uid):
    rows = []
    if uid == OWNER_ID:
        rows.append([btn("👑 Owner Panel", "owner_panel")])
    if is_admin(uid):
        rows.append([btn("🚀 Start Forward", "start_forward")])
        rows.append([btn("📋 My Jobs", "my_jobs"), btn("📊 My Stats", "my_stats")])
    return InlineKeyboardMarkup(rows) if rows else None

def owner_kb():
    return kb(
        [btn("👥 Manage Admins", "manage_admins"), btn("📊 Global Stats", "global_stats")],
        [btn("📣 Broadcast", "broadcast"), btn("📝 Set Log Channel", "set_log")],
        [btn("🏠 Home", "home")]
    )

def admin_list_kb():
    admins = get_admins()
    rows = []
    for a in admins:
        if a == OWNER_ID: continue
        exp = get_prop(f"admin_exp_{a}", 0)
        left = max(0, int((exp - time.time()) / 86400)) if exp else 0
        rows.append([btn(f"🛡 {a} ({left}d left)", f"admin_info_{a}")])
    rows.append([btn("➕ Add Admin", "add_admin"), btn("🔙 Back", "owner_panel")])
    return InlineKeyboardMarkup(rows)

def cancel_kb():
    return kb([btn("❌ Cancel", "home")])

def target_type_kb():
    return kb(
        [btn("📢 Channel", "target_channel"), btn("👥 Group Topic", "target_topic")],
        [btn("❌ Cancel", "home")]
    )

def confirm_kb():
    return kb(
        [btn("✅ Confirm & Start", "confirm_forward"), btn("❌ Cancel", "home")]
    )

# ═══════════════════════════════════════════════════════════════
# SEND HELPER
# ═══════════════════════════════════════════════════════════════
async def send(client, chat_id, text, markup=None, edit_msg=None):
    if edit_msg:
        try:
            await edit_msg.edit_text(text, parse_mode=ParseMode.HTML,
                                     reply_markup=markup, disable_web_page_preview=True)
            return edit_msg
        except MessageNotModified:
            return edit_msg
        except Exception:
            pass
    return await client.send_message(
        chat_id, text, parse_mode=ParseMode.HTML,
        reply_markup=markup, disable_web_page_preview=True
    )

async def cleanup(message):
    try: await message.delete()
    except: pass

# ═══════════════════════════════════════════════════════════════
# HOME SCREEN
# ═══════════════════════════════════════════════════════════════
async def show_home(client, uid, edit_msg=None):
    set_session(uid, "", {})
    if not is_admin(uid):
        txt = (
            "🔒 <b>Access Restricted</b>\n\n"
            "You are not authorized to use this bot.\n"
            f"Contact owner: <a href='tg://user?id={OWNER_ID}'>Click Here</a>"
        )
        await send(client, uid, txt, edit_msg=edit_msg)
        return

    role = "👑 Owner" if uid == OWNER_ID else "🛡 Administrator"
    exp  = get_prop(f"admin_exp_{uid}", 0)
    exp_txt = "" if uid == OWNER_ID else f"\n⏳ <b>Plan Expires:</b> {format_dt(exp)}"

    jobs_done = get_prop(f"jobs_done_{uid}", 0)
    msgs_fwd  = get_prop(f"msgs_fwd_{uid}", 0)

    txt = (
        f"╔══ <b>FORWARD BOT</b> ══╗\n\n"
        f"👤 <b>User:</b> <code>{uid}</code>\n"
        f"🎭 <b>Role:</b> {role}{exp_txt}\n\n"
        f"📦 <b>Jobs Done:</b> <code>{jobs_done}</code>\n"
        f"📨 <b>Messages Forwarded:</b> <code>{msgs_fwd}</code>\n\n"
        f"<i>Select an option below to get started.</i>"
    )
    await send(client, uid, txt, home_kb(uid), edit_msg=edit_msg)

# ═══════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message: Message):
    await show_home(client, message.from_user.id)

# ═══════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ═══════════════════════════════════════════════════════════════
@app.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    d   = query.data
    msg = query.message
    await query.answer()

    if not is_admin(uid):
        await query.answer("⛔ Unauthorized!", show_alert=True)
        return

    # ── HOME ──────────────────────────────────────────────────
    if d == "home":
        await show_home(client, uid, msg)
        return

    # ── OWNER PANEL ───────────────────────────────────────────
    if d == "owner_panel" and uid == OWNER_ID:
        await send(client, uid, "👑 <b>Owner Panel</b>\n\nSelect an option:", owner_kb(), msg)
        return

    # ── MANAGE ADMINS ─────────────────────────────────────────
    if d == "manage_admins" and uid == OWNER_ID:
        await send(client, uid, "👥 <b>Admin Management</b>\n\nCurrent administrators:", admin_list_kb(), msg)
        return

    if d == "add_admin" and uid == OWNER_ID:
        set_session(uid, "wait_admin_id")
        await send(client, uid,
            "➕ <b>Add New Admin</b>\n\n"
            "Send the <b>User ID</b> of the new admin:",
            cancel_kb(), msg)
        return

    if d.startswith("admin_info_") and uid == OWNER_ID:
        aid = int(d.split("_")[2])
        exp = get_prop(f"admin_exp_{aid}", 0)
        jobs = get_prop(f"jobs_done_{aid}", 0)
        msgs = get_prop(f"msgs_fwd_{aid}", 0)
        txt = (
            f"🛡 <b>Admin Info</b>\n\n"
            f"🆔 <b>ID:</b> <code>{aid}</code>\n"
            f"⏳ <b>Expires:</b> {format_dt(exp)}\n"
            f"📦 <b>Jobs:</b> <code>{jobs}</code>\n"
            f"📨 <b>Forwarded:</b> <code>{msgs}</code>"
        )
        await send(client, uid, txt,
            kb([btn("🗑 Revoke Access", f"revoke_{aid}"), btn("🔙 Back", "manage_admins")]),
            msg)
        return

    if d.startswith("revoke_") and uid == OWNER_ID:
        aid = int(d.split("_")[1])
        remove_admin(aid)
        await send(client, uid, f"✅ Admin <code>{aid}</code> revoked.", admin_list_kb(), msg)
        return

    # ── GLOBAL STATS ──────────────────────────────────────────
    if d == "global_stats" and uid == OWNER_ID:
        admins = get_admins()
        total_jobs = total_msgs = 0
        txt = "📊 <b>Global Statistics</b>\n\n"
        for a in admins:
            j = get_prop(f"jobs_done_{a}", 0)
            m = get_prop(f"msgs_fwd_{a}", 0)
            total_jobs += j; total_msgs += m
            role = "👑" if a == OWNER_ID else "🛡"
            txt += f"{role} <code>{a}</code> — Jobs: {j} | Msgs: {m}\n"
        txt += f"\n<b>Total Jobs:</b> {total_jobs}\n<b>Total Forwarded:</b> {total_msgs}"
        log_ch = get_prop("log_channel", "Not Set")
        txt += f"\n\n📝 <b>Log Channel:</b> <code>{log_ch}</code>"
        await send(client, uid, txt, kb([btn("🔙 Back", "owner_panel")]), msg)
        return

    # ── SET LOG CHANNEL ───────────────────────────────────────
    if d == "set_log" and uid == OWNER_ID:
        set_session(uid, "wait_log_channel")
        await send(client, uid,
            "📝 <b>Set Log Channel</b>\n\n"
            "Send the <b>Channel ID</b> (starting with -100)\n"
            "All forwarded messages will also be sent here.",
            cancel_kb(), msg)
        return

    # ── BROADCAST ─────────────────────────────────────────────
    if d == "broadcast" and uid == OWNER_ID:
        set_session(uid, "wait_broadcast")
        await send(client, uid,
            "📣 <b>Broadcast</b>\n\nSend the message to broadcast to all admins:",
            cancel_kb(), msg)
        return

    # ── START FORWARD ─────────────────────────────────────────
    if d == "start_forward":
        set_session(uid, "wait_source", {})
        await send(client, uid,
            "🚀 <b>Start Forward — Step 1/4</b>\n\n"
            "📥 Send the <b>Source Channel ID</b>\n"
            "<i>(The channel you want to forward FROM)</i>\n\n"
            "💡 Format: <code>-100xxxxxxxxxx</code>",
            cancel_kb(), msg)
        return

    if d == "target_channel":
        _, temp = get_session(uid)
        temp["target_type"] = "channel"
        set_session(uid, "wait_target", temp)
        await send(client, uid,
            "🚀 <b>Start Forward — Step 2/4</b>\n\n"
            "📤 Send the <b>Target Channel ID</b>\n"
            "<i>(The channel you want to forward TO)</i>\n\n"
            "💡 Format: <code>-100xxxxxxxxxx</code>",
            cancel_kb(), msg)
        return

    if d == "target_topic":
        _, temp = get_session(uid)
        temp["target_type"] = "topic"
        set_session(uid, "wait_target", temp)
        await send(client, uid,
            "🚀 <b>Start Forward — Step 2/4</b>\n\n"
            "👥 Send the <b>Group ID</b>\n"
            "<i>(The group with topics)</i>\n\n"
            "💡 Format: <code>-100xxxxxxxxxx</code>",
            cancel_kb(), msg)
        return

    if d == "wait_topic_id":
        _, temp = get_session(uid)
        set_session(uid, "wait_topic_id", temp)
        await send(client, uid,
            "🚀 <b>Start Forward — Step 2b/4</b>\n\n"
            "💬 Send the <b>Topic ID</b>\n"
            "<i>(Right click on topic → Copy Link → last number is topic ID)</i>",
            cancel_kb(), msg)
        return

    # ── CONFIRM FORWARD ───────────────────────────────────────
    if d == "confirm_forward":
        _, temp = get_session(uid)
        await msg.edit_text(
            "⏳ <b>Forward job queued!</b>\n\nStarting shortly...",
            parse_mode=ParseMode.HTML
        )
        asyncio.create_task(run_forward_job(client, uid, temp))
        return

    # ── MY JOBS ───────────────────────────────────────────────
    if d == "my_jobs":
        with db() as c:
            jobs = c.execute(
                "SELECT * FROM forward_jobs WHERE admin_id=? ORDER BY id DESC LIMIT 10",
                (uid,)
            ).fetchall()

        if not jobs:
            await send(client, uid, "📋 <b>No jobs found.</b>", kb([btn("🔙 Back", "home")]), msg)
            return

        txt = "📋 <b>Your Recent Jobs</b>\n\n"
        for j in jobs:
            status_emoji = {"done": "✅", "running": "🔄", "pending": "⏳", "cancelled": "❌"}.get(j["status"], "❓")
            txt += (
                f"{status_emoji} <b>Job #{j['id']}</b>\n"
                f"   📥 Source: <code>{j['source_id']}</code>\n"
                f"   📤 Target: <code>{j['target_id']}</code>\n"
                f"   📨 Range: {j['from_msg']} → {j['to_msg']}\n"
                f"   ✅ Done: {j['forwarded']} | ❌ Failed: {j['failed']}\n\n"
            )
        await send(client, uid, txt, kb([btn("🔙 Back", "home")]), msg)
        return

    # ── MY STATS ──────────────────────────────────────────────
    if d == "my_stats":
        jobs  = get_prop(f"jobs_done_{uid}", 0)
        msgs  = get_prop(f"msgs_fwd_{uid}", 0)
        exp   = get_prop(f"admin_exp_{uid}", 0)
        txt   = (
            f"📊 <b>My Statistics</b>\n\n"
            f"📦 <b>Total Jobs:</b> <code>{jobs}</code>\n"
            f"📨 <b>Total Forwarded:</b> <code>{msgs}</code>\n"
            f"⏳ <b>Plan Expires:</b> {format_dt(exp) if uid != OWNER_ID else 'Lifetime'}"
        )
        await send(client, uid, txt, kb([btn("🔙 Back", "home")]), msg)
        return

# ═══════════════════════════════════════════════════════════════
# MESSAGE HANDLER (State Machine)
# ═══════════════════════════════════════════════════════════════
@app.on_message(filters.private & ~filters.command(["start"]))
async def msg_handler(client: Client, message: Message):
    uid  = message.from_user.id
    text = (message.text or "").strip()
    st, temp = get_session(uid)

    if not is_admin(uid):
        return

    await cleanup(message)

    # ── ADD ADMIN ─────────────────────────────────────────────
    if st == "wait_admin_id" and uid == OWNER_ID:
        try:
            new_id = int(text)
        except ValueError:
            await send(client, uid, "⚠️ Invalid ID. Send a valid numeric User ID.", cancel_kb())
            return
        set_session(uid, f"wait_admin_days_{new_id}", {"new_admin": new_id})
        await send(client, uid,
            f"➕ <b>Set Duration for <code>{new_id}</code></b>\n\n"
            "Send number of days (e.g. <code>30</code>, <code>365</code>):", cancel_kb())
        return

    if st.startswith("wait_admin_days_") and uid == OWNER_ID:
        try:
            days = int(text)
            assert days > 0
        except:
            await send(client, uid, "⚠️ Invalid. Send a positive number of days.", cancel_kb())
            return
        new_admin = temp.get("new_admin")
        add_admin(new_admin, days)
        set_session(uid, "", {})
        await send(client, uid,
            f"✅ <b>Admin Added!</b>\n\n"
            f"🆔 ID: <code>{new_admin}</code>\n"
            f"⏳ Duration: <code>{days} days</code>",
            admin_list_kb())
        return

    # ── LOG CHANNEL ───────────────────────────────────────────
    if st == "wait_log_channel" and uid == OWNER_ID:
        set_prop("log_channel", text)
        set_session(uid, "", {})
        await send(client, uid,
            f"✅ Log channel set to <code>{text}</code>",
            kb([btn("🔙 Back", "owner_panel")]))
        return

    # ── BROADCAST ─────────────────────────────────────────────
    if st == "wait_broadcast" and uid == OWNER_ID:
        set_session(uid, "", {})
        admins = get_admins()
        sent = failed = 0
        status_msg = await send(client, uid, "📣 Broadcasting...")
        for a in admins:
            try:
                await client.copy_message(a, uid, message.id)
                sent += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)
        await status_msg.edit_text(
            f"✅ <b>Broadcast Done!</b>\n\n✅ Sent: {sent} | ❌ Failed: {failed}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([btn("🔙 Back", "owner_panel")])
        )
        return

    # ── FORWARD FLOW ──────────────────────────────────────────
    if st == "wait_source":
        try:
            src = int(text)
        except ValueError:
            await send(client, uid, "⚠️ Invalid ID. Must be numeric like <code>-100xxxxxxxxxx</code>", cancel_kb())
            return
        temp["source"] = src
        set_session(uid, "wait_target_type", temp)
        await send(client, uid,
            f"✅ Source set: <code>{src}</code>\n\n"
            "🚀 <b>Step 2/4 — Choose Target Type:</b>",
            target_type_kb())
        return

    if st == "wait_target":
        try:
            tgt = int(text)
        except ValueError:
            await send(client, uid, "⚠️ Invalid ID. Must be numeric like <code>-100xxxxxxxxxx</code>", cancel_kb())
            return
        temp["target"] = tgt
        if temp.get("target_type") == "topic":
            set_session(uid, "wait_topic_id", temp)
            await send(client, uid,
                f"✅ Group set: <code>{tgt}</code>\n\n"
                "🚀 <b>Step 3/4 — Send Topic ID</b>\n\n"
                "💡 How to get Topic ID:\n"
                "Right click on topic → Copy Link\n"
                "Link looks like: t.me/c/xxx/<b>123</b>/456\n"
                "The bold number is your Topic ID",
                cancel_kb())
        else:
            set_session(uid, "wait_range", temp)
            await send(client, uid,
                f"✅ Target set: <code>{tgt}</code>\n\n"
                "🚀 <b>Step 3/4 — Send Message Range</b>\n\n"
                "💡 Format: <code>from-to</code>\n"
                "Example: <code>1-500</code> or <code>20-124</code>",
                cancel_kb())
        return

    if st == "wait_topic_id":
        try:
            topic_id = int(text)
        except ValueError:
            await send(client, uid, "⚠️ Invalid Topic ID. Must be a number.", cancel_kb())
            return
        temp["topic_id"] = topic_id
        set_session(uid, "wait_range", temp)
        await send(client, uid,
            f"✅ Topic ID set: <code>{topic_id}</code>\n\n"
            "🚀 <b>Step 4/4 — Send Message Range</b>\n\n"
            "💡 Format: <code>from-to</code>\n"
            "Example: <code>1-500</code> or <code>20-124</code>",
            cancel_kb())
        return

    if st == "wait_range":
        try:
            parts = text.split("-")
            assert len(parts) == 2
            from_msg = int(parts[0].strip())
            to_msg   = int(parts[1].strip())
            assert from_msg > 0 and to_msg >= from_msg
        except:
            await send(client, uid,
                "⚠️ Invalid range. Use format: <code>from-to</code>\n"
                "Example: <code>20-124</code>", cancel_kb())
            return

        temp["from_msg"] = from_msg
        temp["to_msg"]   = to_msg
        set_session(uid, "wait_confirm", temp)

        target_type = temp.get("target_type", "channel")
        topic_txt   = f"\n💬 <b>Topic ID:</b> <code>{temp.get('topic_id', 'N/A')}</code>" if target_type == "topic" else ""
        total       = to_msg - from_msg + 1

        txt = (
            "🚀 <b>Forward Job Summary</b>\n\n"
            f"📥 <b>Source:</b> <code>{temp['source']}</code>\n"
            f"📤 <b>Target:</b> <code>{temp['target']}</code>{topic_txt}\n"
            f"📨 <b>Range:</b> <code>{from_msg}</code> → <code>{to_msg}</code>\n"
            f"📦 <b>Total Messages:</b> <code>{total}</code>\n\n"
            "⚡ Forward tag will be <b>hidden</b> (clean copy)\n"
            "📝 Log channel will receive all forwarded messages\n\n"
            "Confirm to start?"
        )
        await send(client, uid, txt, confirm_kb())
        return

# ═══════════════════════════════════════════════════════════════
# FORWARD JOB RUNNER
# ═══════════════════════════════════════════════════════════════
async def run_forward_job(client: Client, uid: int, temp: dict):
    source   = temp["source"]
    target   = temp["target"]
    topic_id = temp.get("topic_id", 0)
    from_msg = temp["from_msg"]
    to_msg   = temp["to_msg"]
    log_ch   = get_prop("log_channel", "")

    # Save job to DB
    with db() as c:
        c.execute(
            "INSERT INTO forward_jobs(admin_id,source_id,target_id,topic_id,from_msg,to_msg,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uid, str(source), str(target), topic_id, from_msg, to_msg, "running", int(time.time()))
        )
        job_id = c.lastrowid
        c.commit()

    total     = to_msg - from_msg + 1
    forwarded = 0
    failed    = 0
    skipped   = 0

    # Progress message
    progress = await client.send_message(
        uid,
        f"🔄 <b>Job #{job_id} Running...</b>\n\n"
        f"📥 Source: <code>{source}</code>\n"
        f"📤 Target: <code>{target}</code>\n"
        f"📦 Range: {from_msg} → {to_msg}\n\n"
        f"⏳ Progress: 0/{total}",
        parse_mode=ParseMode.HTML
    )

    for msg_id in range(from_msg, to_msg + 1):
        try:
            msg = await client.get_messages(source, msg_id)

            if not msg or msg.empty:
                skipped += 1
                continue

            # Only forward videos and documents (PDFs)
            is_video    = bool(msg.video)
            is_document = bool(msg.document)

            if not (is_video or is_document):
                skipped += 1
                continue

            caption = msg.caption or ""
            send_kwargs = dict(
                chat_id=target,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            if topic_id:
                send_kwargs["reply_to_message_id"] = topic_id

            # Copy without forward tag
            if is_video:
                sent = await client.send_video(
                    video=msg.video.file_id,
                    **send_kwargs
                )
            elif is_document:
                sent = await client.send_document(
                    document=msg.document.file_id,
                    **send_kwargs
                )

            # Log to log channel
            if log_ch:
                try:
                    log_caption = (
                        f"📨 <b>Forwarded by Admin</b> <code>{uid}</code>\n"
                        f"📥 Source: <code>{source}</code> | Msg: <code>{msg_id}</code>\n"
                        f"📤 Target: <code>{target}</code>"
                    )
                    if is_video:
                        await client.send_video(
                            chat_id=int(log_ch),
                            video=msg.video.file_id,
                            caption=log_caption,
                            parse_mode=ParseMode.HTML
                        )
                    elif is_document:
                        await client.send_document(
                            chat_id=int(log_ch),
                            document=msg.document.file_id,
                            caption=log_caption,
                            parse_mode=ParseMode.HTML
                        )
                except Exception as e:
                    logger.warning(f"Log channel error: {e}")

            forwarded += 1

            # Update progress every 10 messages
            if forwarded % 10 == 0:
                percent = int((forwarded / total) * 100)
                bar = "█" * (percent // 10) + "░" * (10 - percent // 10)
                try:
                    await progress.edit_text(
                        f"🔄 <b>Job #{job_id} Running...</b>\n\n"
                        f"[{bar}] {percent}%\n\n"
                        f"✅ Forwarded: {forwarded}\n"
                        f"❌ Failed: {failed}\n"
                        f"⏭ Skipped: {skipped}\n"
                        f"📦 Total: {total}",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass

            await asyncio.sleep(0.5)  # Speed control

        except FloodWait as e:
            logger.warning(f"FloodWait: {e.value}s")
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"Error on msg {msg_id}: {e}")
            failed += 1
            await asyncio.sleep(1)

    # Update DB
    with db() as c:
        c.execute(
            "UPDATE forward_jobs SET forwarded=?,failed=?,status=? WHERE id=?",
            (forwarded, failed, "done", job_id)
        )
        c.commit()

    # Update stats
    set_prop(f"jobs_done_{uid}", get_prop(f"jobs_done_{uid}", 0) + 1)
    set_prop(f"msgs_fwd_{uid}",  get_prop(f"msgs_fwd_{uid}",  0) + forwarded)

    # Final report
    try:
        await progress.edit_text(
            f"✅ <b>Job #{job_id} Complete!</b>\n\n"
            f"📥 Source: <code>{source}</code>\n"
            f"📤 Target: <code>{target}</code>\n"
            f"📨 Range: {from_msg} → {to_msg}\n\n"
            f"✅ <b>Forwarded:</b> <code>{forwarded}</code>\n"
            f"❌ <b>Failed:</b> <code>{failed}</code>\n"
            f"⏭ <b>Skipped:</b> <code>{skipped}</code>\n\n"
            f"⏱ Job finished at {format_dt(time.time())}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([btn("🏠 Home", "home")])
        )
    except:
        pass

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    db_init()
    logger.info("🚀 Forward Bot Starting...")
    app.run()
