import os
import asyncio
import time
import json
import re
import logging
import urllib.parse
import urllib.request
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import SessionPasswordNeededError
from deep_translator import GoogleTranslator

# ── BETTER LOGGING SYSTEM ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ── PYTHON 3.14 ASYNCIO LOOP FIX ──────────────────────────────────────────────
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
API_ID       = int(os.environ.get("API_ID", 0))
API_HASH     = os.environ.get("API_HASH", "")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", 0))
RAW_SESSIONS = os.environ.get("STRING_SESSIONS", "")
DB_FILE      = "manager_data.json"
PORT         = int(os.environ.get("PORT", 8080))

start_time   = time.time()
SESSIONS     = {}   
LOGIN_STATES = {}  # সেশন তৈরির স্টেট ট্র্যাকিং করার জন্য
AFK_COOLDOWN = 3600  

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f: return json.load(f)
        except: pass
    return {"settings": {}}

def save_db():
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

db = load_db()

def uptime():
    s = int(time.time() - start_time)
    h, s = divmod(s, 3600); m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"

bot = TelegramClient('manager_bot', API_ID, API_HASH)

# ── RENDER PORT BINDING FIX (DUMMY HTTP SERVER) ────────────────────────────────
async def handle_render_ping(reader, writer):
    await reader.read(100)
    response = "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
    writer.write(response.encode())
    await writer.drain()
    writer.close()

async def start_dummy_server():
    try:
        server = await asyncio.start_server(handle_render_ping, '0.0.0.0', PORT)
        logger.info(f"🟢 Dummy Web Server started successfully on port {PORT}")
    except Exception as e:
        logger.error(f"🔴 Failed to start dummy server: {e}")

# ── BOT INLINE MENUS ──────────────────────────────────────────────────────────
def main_menu_buttons():
    buttons = []
    for uid, data in SESSIONS.items():
        name = data["me"].first_name
        afk_icon = "💤" if data["afk"] else "🟢"
        buttons.append([Button.inline(f"{afk_icon} {name}", data=f"acc_{uid}")])
    
    # নতুন অ্যাকাউন্ট অ্যাড করার বাটন
    buttons.append([Button.inline("➕ Add New Account", data="add_acc")])
    buttons.append([Button.inline("📊 Stats", data="stats"), Button.inline("🔄 Refresh", data="refresh")])
    return buttons

def account_menu_buttons(uid):
    data = SESSIONS[uid]
    afk_status = "ON ✅" if data["afk"] else "OFF ❌"
    return [
        [Button.inline(f"💤 AFK: {afk_status}", data=f"toggle_afk_{uid}")],
        [Button.inline("📝 Change Bio", data=f"set_bio_{uid}"),
         Button.inline("✏️ Change Name", data=f"set_name_{uid}")],
        [Button.inline("🗑 Remove Photo", data=f"delpfp_{uid}"),
         Button.inline("📋 My Info", data=f"myinfo_{uid}")],
        [Button.inline("⏱ Auto-Delete Settings", data=f"autodel_{uid}")],
        [Button.inline("◀️ Back", data="main")]
    ]

def autodel_menu_buttons(uid):
    s = db["settings"].get(str(uid), {})
    delay = s.get("autodel_delay", 60)
    enabled = s.get("autodel_enabled", False)
    status = f"ON ({delay}s)" if enabled else "OFF"
    return [
        [Button.inline(f"Auto-Delete: {status}", data=f"autodel_toggle_{uid}")],
        [Button.inline("Set 30s",  data=f"autodel_set_{uid}_30"),
         Button.inline("Set 60s",  data=f"autodel_set_{uid}_60"),
         Button.inline("Set 300s", data=f"autodel_set_{uid}_300")],
        [Button.inline("◀️ Back", data=f"acc_{uid}")]
    ]

# ── MASTER BOT HANDLERS ───────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern='/start'))
async def _(e):
    if e.sender_id != OWNER_ID: return
    LOGIN_STATES.pop(OWNER_ID, None)
    await e.reply("**Session Manager**\n\nSelect an account to manage or add a new one:", buttons=main_menu_buttons())

@bot.on(events.CallbackQuery)
async def _(e):
    if e.sender_id != OWNER_ID: return await e.answer("Access denied.", alert=True)
    data = e.data.decode()

    if data == "main":
        LOGIN_STATES.pop(OWNER_ID, None)
        await e.edit("**Session Manager**\n\nSelect an account to manage:", buttons=main_menu_buttons())
    elif data == "refresh":
        await e.edit("**Session Manager** _(refreshed)_\n\nSelect an account:", buttons=main_menu_buttons())
    elif data == "add_acc":
        LOGIN_STATES[OWNER_ID] = {"step": "phone"}
        await e.edit("📱 তোমার টেলিগ্রাম নাম্বারটি আন্তর্জাতিক ফরম্যাটে দাও (যেমন: `+88017XXXXXXXX`):", buttons=[[Button.inline("◀️ Cancel", data="main")]])
    elif data == "stats":
        total  = len(SESSIONS)
        online = sum(1 for d in SESSIONS.values() if not d["afk"])
        afk    = total - online
        await e.edit(f"**Stats**\n\nTotal Sessions: `{total}`\nOnline: `{online}`\nAFK: `{afk}`\nUptime: `{uptime()}`", buttons=[[Button.inline("◀️ Back", data="main")]])
    elif data.startswith("acc_"):
        uid = int(data[4:])
        if uid not in SESSIONS: return await e.answer("Session not found.", alert=True)
        d = SESSIONS[uid]; me = d["me"]
        await e.edit(f"**Managing: {me.first_name}**\nID: `{uid}`\nUsername: @{me.username or '—'}\nAFK: {'ON' if d['afk'] else 'OFF'}\nAFK Message: `{d['afk_msg']}`", buttons=account_menu_buttons(uid))
    elif data.startswith("toggle_afk_"):
        uid = int(data[11:])
        if uid not in SESSIONS: return await e.answer("Session not found.", alert=True)
        SESSIONS[uid]["afk"] = not SESSIONS[uid]["afk"]
        await e.answer(f"AFK {'enabled' if SESSIONS[uid]['afk'] else 'disabled'}.")
        d = SESSIONS[uid]; me = d["me"]
        await e.edit(f"**Managing: {me.first_name}**\nID: `{uid}`\nAFK: {'ON ✅' if d['afk'] else 'OFF ❌'}", buttons=account_menu_buttons(uid))
    elif data.startswith("delpfp_"):
        uid = int(data[7:])
        if uid not in SESSIONS: return await e.answer("Session not found.", alert=True)
        try:
            p = await SESSIONS[uid]["client"].get_profile_photos("me")
            if p:
                await SESSIONS[uid]["client"].delete_profile_photos(p[0])
                await e.answer("Profile photo removed.")
            else: await e.answer("No photo to remove.", alert=True)
        except Exception as ex: 
            logger.error(f"Error removing photo: {ex}")
            await e.answer(f"Error: {ex}", alert=True)
    elif data.startswith("myinfo_"):
        uid = int(data[7:])
        if uid not in SESSIONS: return await e.answer("Session not found.", alert=True)
        me = SESSIONS[uid]["me"]
        await e.edit(f"**Info: {me.first_name}**\n\nID: `{uid}`\nUsername: @{me.username or '—'}\nFirst Name: {me.first_name}\nLast Name: {me.last_name or '—'}", buttons=[[Button.inline("◀️ Back", data=f"acc_{uid}")]])
    elif data.startswith("autodel_") and not data.startswith("autodel_set_") and not data.startswith("autodel_toggle_"):
        uid = int(data[8:])
        await e.edit("**Auto-Delete Settings**\nChoose delay for public command responses:", buttons=autodel_menu_buttons(uid))
    elif data.startswith("autodel_toggle_"):
        uid = int(data[15:])
        s = db["settings"].setdefault(str(uid), {})
        s["autodel_enabled"] = not s.get("autodel_enabled", False)
        save_db()
        await e.answer(f"Auto-delete {'enabled' if s['autodel_enabled'] else 'disabled'}.")
        await e.edit("**Auto-Delete Settings:**", buttons=autodel_menu_buttons(uid))
    elif data.startswith("autodel_set_"):
        parts = data.split("_")
        uid, delay = int(parts[2]), int(parts[3])
        s = db["settings"].setdefault(str(uid), {})
        s["autodel_delay"]   = delay
        s["autodel_enabled"] = True
        save_db()
        await e.answer(f"Auto-delete set to {delay}s.")
        await e.edit("**Auto-Delete Settings:**", buttons=autodel_menu_buttons(uid))
    elif data.startswith("set_bio_"):
        SESSIONS[int(data[8:])]["_waiting"] = "bio"
        await e.edit("Send the new bio as a message now:", buttons=[[Button.inline("◀️ Cancel", data=f"acc_{int(data[8:])}")]])
    elif data.startswith("set_name_"):
        SESSIONS[int(data[9:])]["_waiting"] = "name"
        await e.edit("Send the new first name as a message now:", buttons=[[Button.inline("◀️ Cancel", data=f"acc_{int(data[9:])}")]])

# ── LOGIN AND PROFILE HANDLER ─────────────────────────────────────────────────
@bot.on(events.NewMessage(func=lambda e: e.sender_id == OWNER_ID and e.text and not e.text.startswith('/')))
async def handle_owner_input(e):
    state = LOGIN_STATES.get(OWNER_ID)
    
    # সেশন ক্রিয়েশন উইজার্ড লজিক
    if state:
        step = state.get("step")
        if step == "phone":
            phone = e.text.strip().replace(" ", "")
            m = await e.reply("`OTP কোড পাঠানো হচ্ছে...`")
            try:
                cl = TelegramClient(StringSession(), API_ID, API_HASH)
                await cl.connect()
                res = await cl.send_code_request(phone)
                LOGIN_STATES[OWNER_ID] = {
                    "step": "otp",
                    "phone": phone,
                    "phone_code_hash": res.phone_code_hash,
                    "client": cl
                }
                await m.edit("📩 তোমার টেলিগ্রাম অ্যাপে বা সিমে পাঠানো ওটিপি (OTP) কোডটি দাও।\n\nযদি কোডের মাঝে স্পেস থাকে, তবে স্পেস যেভাবে আছে সেভাবেই লিখে দাও (যেমন: `1 2 3 4 5`):")
            except Exception as ex:
                await m.edit(f"❌ এরর এসেছে: `{ex}`")
                LOGIN_STATES.pop(OWNER_ID, None)
            return

        elif step == "otp":
            otp = e.text.strip().replace(" ", "")
            cl = state["client"]
            phone = state["phone"]
            hsh = state["phone_code_hash"]
            m = await e.reply("`OTP ভেরিফাই করা হচ্ছে...`")
            try:
                await cl.sign_in(phone, otp, phone_code_hash=hsh)
                str_session = cl.session.save()
                await m.edit(f"✅ **লগইন সফল হয়েছে!**\n\nনিচের সেশন কোডটি সম্পূর্ণ কপি করে তোমার Render-এর `STRING_SESSIONS` এনভায়রনমেন্ট ভেরিয়েবলে যোগ করো (একাধিক সেশন হলে কমা `,` দিয়ে আলাদা করবে):\n\n`{str_session}`\n\n_রেন্ডার ড্যাশবোর্ডে ভেরিয়েবল সেভ করার পর অবশ্যই একবার Manual Deploy/Redeploy দিবে।_")
                LOGIN_STATES.pop(OWNER_ID, None)
                await cl.disconnect()
            except SessionPasswordNeededError:
                LOGIN_STATES[OWNER_ID]["step"] = "2fa"
                await m.edit("🔒 তোমার অ্যাকাউন্টে Two-Step Verification অন আছে। দয়া করে 2FA পাসওয়ার্ডটি লিখে পাঠাও:")
            except Exception as ex:
                await m.edit(f"❌ লগইন ব্যর্থ হয়েছে: `{ex}`")
                LOGIN_STATES.pop(OWNER_ID, None)
            return

        elif step == "2fa":
            pwd = e.text.strip()
            cl = state["client"]
            m = await e.reply("`2FA পাসওয়ার্ড ভেরিফাই করা হচ্ছে...`")
            try:
                await cl.sign_in(password=pwd)
                str_session = cl.session.save()
                await m.edit(f"✅ **লগইন সফল হয়েছে (2FA)!**\n\nনিচের সেশন কোডটি কপি করে তোমার Render-এর `STRING_SESSIONS` এ যোগ করো:\n\n`{str_session}`\n\n_রেন্ডারে সেভ করার পর রিডিপ্লয় দাও।_")
                LOGIN_STATES.pop(OWNER_ID, None)
                await cl.disconnect()
            except Exception as ex:
                await m.edit(f"❌ পাসওয়ার্ড ভুল বা অন্য সমস্যা: `{ex}`")
                LOGIN_STATES.pop(OWNER_ID, None)
            return

    # বায়ো এবং নাম পরিবর্তনের এক্সিস্টিং লজিক
    for uid, data in SESSIONS.items():
        waiting = data.get("_waiting")
        if not waiting: continue
        try:
            if waiting == "bio":
                await data["client"](UpdateProfileRequest(about=e.text.strip()))
                await e.reply(f"Bio updated for **{data['me'].first_name}**.")
            elif waiting == "name":
                await data["client"](UpdateProfileRequest(first_name=e.text.strip()))
                await e.reply(f"Name updated for **{data['me'].first_name}**.")
        except Exception as ex: 
            logger.error(f"Profile update error: {ex}")
            await e.reply(f"Error: {ex}")
        data.pop("_waiting", None); break

# ── USERBOTS HANDLER ENGINE ───────────────────────────────────────────────────
def register_userbot_handlers(client, uid):
    
    def is_owner(e):
        return e.sender_id == OWNER_ID or e.sender_id == uid

    def allowed(e, cmd):
        if is_owner(e): return True
        s = db["settings"].get(str(uid), {})
        public_cmds = s.get("public_cmds", ["ping", "alive", "id", "help", "qr", "tts", "tr", "stickify", "weather", "wiki", "urban", "calc"])
        return cmd in public_cmds

    async def respond(e, text):
        if e.sender_id == uid: return await e.edit(text)
        return await e.reply(text)

    async def auto_del(msg, delay_sec=None):
        if not msg: return
        if delay_sec is None:
            s = db["settings"].get(str(uid), {})
            if not s.get("autodel_enabled", False) and is_owner(msg): return
            delay_sec = s.get("autodel_delay", 60)
        await asyncio.sleep(delay_sec)
        try: await msg.delete()
        except: pass

    # AFK Logic
    @client.on(events.NewMessage(incoming=True))
    async def afk_in(e):
        d = SESSIONS.get(uid)
        if not d or not d["afk"] or e.sender_id == uid: return
        if not (e.is_private or e.mentioned): return
        sender = await e.get_sender()
        if sender and getattr(sender, 'bot', False): return
        
        now = time.time()
        sid = e.sender_id
        if now - d["afk_sent"].get(sid, 0) < AFK_COOLDOWN: return
        d["afk_sent"][sid] = now
        await e.reply(d["afk_msg"])

    @client.on(events.NewMessage(outgoing=True))
    async def afk_out(e):
        d = SESSIONS.get(uid)
        if d and d["afk"] and e.text and not e.text.startswith(('.afk', '!setafk', '.ping', '!ping')):
            d["afk"] = False
            d["afk_sent"] = {}
            m = await e.respond("`I am back! AFK mode automatically disabled.`")
            asyncio.create_task(auto_del(m, 10))

    # ── COMMANDS ──
    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?ping$'))
    async def _(e):
        if not allowed(e, "ping"): return
        start = time.time()
        m = await e.reply("`Processing...`") if e.sender_id != uid else await e.edit("`Processing...`")
        latency = int((time.time() - start) * 1000)
        status = "🟢 Excellent" if latency < 150 else ("🟡 Average" if latency < 400 else "🔴 Poor")
        out = f"🏓 **Pong!**\n\n🧭 **Ping:** `{latency} ms`\n📶 **Status:** {status}\n🗑 *Deleted in 6s.*"
        msg = await m.edit(out)
        asyncio.create_task(auto_del(msg, 6))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?alive$'))
    async def _(e):
        if not allowed(e, "alive"): return
        m = await respond(e, f"⚡ **System Status:** Online\n⏱ **Uptime:** `{uptime()}`")
        asyncio.create_task(auto_del(m))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?id$'))
    async def _(e):
        if not allowed(e, "id"): return
        m = await respond(e, f"🆔 **User ID:** `{e.sender_id}`\n💬 **Chat ID:** `{e.chat_id}`")
        asyncio.create_task(auto_del(m))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?help$'))
    async def _(e):
        if not allowed(e, "help"): return
        if is_owner(e):
            out = "⚙️ **Owner Control Panel**\n\nCommands: `ping`, `alive`, `id`, `help`, `qr`, `tts`, `tr`, `stickify`, `weather`, `wiki`, `urban`, `calc`"
        else:
            out = "🌐 **Available Public Commands:**\n`ping`, `alive`, `id`, `help`, `qr`, `tts`, `tr`, `stickify`, `weather`, `wiki`, `urban`, `calc`"
        m = await respond(e, out)
        asyncio.create_task(auto_del(m))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?qr (.+)'))
    async def _(e):
        if not allowed(e, "qr"): return
        txt = e.pattern_match.group(1)
        m = await e.reply("`Generating QR...`") if e.sender_id != uid else await e.edit("`Generating QR...`")
        try:
            import qrcode
            img = qrcode.make(txt)
            path = "/tmp/qr.png"
            img.save(path)
            await m.delete()
            sent = await client.send_file(e.chat_id, path, caption=f"`{txt}`", reply_to=e.id)
            asyncio.create_task(auto_del(sent))
        except Exception as ex: 
            logger.error(f"QR Error: {ex}")
            await m.edit(f"Error: {ex}")

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?tts (.+)'))
    async def _(e):
        if not allowed(e, "tts"): return
        txt = e.pattern_match.group(1)
        m = await e.reply("`Generating audio...`") if e.sender_id != uid else await e.edit("`Generating audio...`")
        try:
            from gtts import gTTS
            lang = 'bn' if re.search(r'[\u0980-\u09FF]', txt) else 'en'
            gTTS(text=txt, lang=lang).save("/tmp/tts.mp3")
            await m.delete()
            sent = await client.send_file(e.chat_id, "/tmp/tts.mp3", voice_note=True, reply_to=e.id)
            asyncio.create_task(auto_del(sent))
        except Exception as ex: 
            logger.error(f"TTS Error: {ex}")
            await m.edit(f"Error: {ex}")

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?tr (\S+)$'))
    async def _(e):
        if not allowed(e, "tr"): return
        if not e.is_reply:
            m = await e.reply("Reply to a message."); asyncio.create_task(auto_del(m, 10)); return
        lang = e.pattern_match.group(1)
        r_msg = await e.get_reply_message()
        txt = r_msg.text or ""
        if not txt:
            m = await e.reply("No text found."); asyncio.create_task(auto_del(m, 10)); return
        try:
            res = GoogleTranslator(source='auto', target=lang).translate(txt)
            m = await respond(e, f"**Translation ({lang}):**\n{res}")
            asyncio.create_task(auto_del(m))
        except Exception as ex: 
            logger.error(f"Translation Error: {ex}")
            m = await e.reply(f"Error: {ex}"); asyncio.create_task(auto_del(m, 10))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?stickify$'))
    async def _(e):
        if not allowed(e, "stickify"): return
        if not e.is_reply:
            m = await e.reply("Reply to a photo."); asyncio.create_task(auto_del(m, 10)); return
        r = await e.get_reply_message()
        if not r.photo:
            m = await e.reply("Not a photo."); asyncio.create_task(auto_del(m, 10)); return
        m = await e.reply("`Converting...`") if e.sender_id != uid else await e.edit("`Converting...`")
        try:
            from PIL import Image
            path = await r.download_media()
            img = Image.open(path).convert("RGBA")
            img.thumbnail((512, 512))
            out = "/tmp/sticker.webp"
            img.save(out, "WEBP")
            await m.delete()
            sent = await client.send_file(e.chat_id, out, reply_to=e.id)
            asyncio.create_task(auto_del(sent))
        except Exception as ex: 
            logger.error(f"Stickify Error: {ex}")
            await m.edit(f"Error: {ex}")

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?weather (.+)'))
    async def _(e):
        if not allowed(e, "weather"): return
        city = e.pattern_match.group(1).strip()
        try:
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as res:
                data = res.read().decode()
            m = await respond(e, f"🌤 {data}")
            asyncio.create_task(auto_del(m))
        except Exception as ex: 
            logger.error(f"Weather Error: {ex}")
            m = await e.reply(f"Error: {ex}"); asyncio.create_task(auto_del(m, 10))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?wiki (.+)'))
    async def _(e):
        if not allowed(e, "wiki"): return
        query = e.pattern_match.group(1).strip()
        try:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as res:
                data = json.loads(res.read())
            m = await respond(e, f"**{data.get('title','')}**\n\n{data.get('extract','No result.')[:500]}")
            asyncio.create_task(auto_del(m))
        except Exception as ex: 
            logger.error(f"Wiki Error: {ex}")
            m = await e.reply(f"Error: {ex}"); asyncio.create_task(auto_del(m, 10))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?urban (.+)'))
    async def _(e):
        if not allowed(e, "urban"): return
        query = e.pattern_match.group(1).strip()
        try:
            url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as res:
                data = json.loads(res.read())
            items = data.get("list", [])
            if not items:
                m = await e.reply("No definition found."); asyncio.create_task(auto_del(m, 10)); return
            defn = items[0]["definition"][:400].replace("[","").replace("]","")
            m = await respond(e, f"**{query}**\n\n{defn}")
            asyncio.create_task(auto_del(m))
        except Exception as ex: 
            logger.error(f"Urban Error: {ex}")
            m = await e.reply(f"Error: {ex}"); asyncio.create_task(auto_del(m, 10))

    @client.on(events.NewMessage(pattern=r'(?i)^[.!]?calc (.+)'))
    async def _(e):
        if not allowed(e, "calc"): return
        expr = e.pattern_match.group(1)
        if not re.fullmatch(r'[\d\s\.\+\-\*\/\(\)]+', expr):
            m = await e.reply("Only numbers and basic math allowed."); asyncio.create_task(auto_del(m, 10)); return
        try:
            result = eval(expr, {'__builtins__': {}})
            m = await respond(e, f"`{expr} = {result}`")
            asyncio.create_task(auto_del(m))
        except: 
            m = await e.reply("Invalid expression."); asyncio.create_task(auto_del(m, 10))

# ── BOOTSTRAP ─────────────────────────────────────────────────────────────────
async def main():
    await start_dummy_server()
    
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("🟢 Manager bot online.")

    if RAW_SESSIONS:
        for s in [x.strip() for x in RAW_SESSIONS.split(",") if x.strip()]:
            try:
                cl = TelegramClient(StringSession(s), API_ID, API_HASH)
                await cl.connect()
                if await cl.is_user_authorized():
                    me = await cl.get_me()
                    uid = me.id
                    SESSIONS[uid] = {
                        "client":   cl,
                        "me":       me,
                        "afk":      True,
                        "afk_msg":  "I'm away right now. I'll get back to you soon.",
                        "afk_sent": {}
                    }
                    register_userbot_handlers(cl, uid)
                    logger.info(f"✅ Active Session Loaded: {me.first_name} (ID: {uid})")
            except Exception as ex: 
                logger.error(f"❌ Session Authorization Error: {ex}")

    await bot.run_until_disconnected()

if __name__ == '__main__':
    loop.run_until_complete(main())
