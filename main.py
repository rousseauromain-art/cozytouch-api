import os, asyncio, threading, sys, time, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import telegram
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.13 (Code validé hier)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        conn.commit(); cur.close(); conn.close()
        print("DEBUG: [DB] OK")
    except Exception as e: print(f"DEBUG: [DB ERR] {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- LA LOGIQUE QUI A MARCHÉ HIER ---
async def apply_heating_temp(target_temp):
    print(f"DEBUG: [ACTION] Passage à {target_temp}°C")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        count = 0
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                # Hier, on a vu que seuls les #1 acceptent la consigne
                if d.device_url.endswith("#1"):
                    try:
                        print(f"DEBUG: [ACTION] Envoi {target_temp} vers {ROOMS[base_url]}")
                        await client.execute_command(d.device_url, Command("setTargetTemperature", [target_temp]))
                        count += 1
                    except Exception as e:
                        print(f"DEBUG: [ACTION ERR] {ROOMS[base_url]}: {e}")
        return count

# --- SCAN & ENREGISTREMENT ---
async def perform_record(label="AUTO"):
    print(f"DEBUG: [{label}] Scan...")
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            data_map = {name: {"temp": None, "target": None} for name in ROOMS.values()}
            for d in devices:
                base_url = d.device_url.split('#')[0]
                if base_url in ROOMS:
                    room = ROOMS[base_url]
                    states = {s.name: s.value for s in d.states}
                    t = states.get("core:TemperatureState")
                    s = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    if t is not None: data_map[room]["temp"] = t
                    if s is not None: data_map[room]["target"] = s
            
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            for room, vals in data_map.items():
                if vals["temp"] is not None:
                    cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                               (room, vals["temp"], (shelly_t if room=="Bureau" else None), vals["target"]))
            conn.commit(); cur.close(); conn.close()
            print(f"DEBUG: [{label}] Enregistré.")
            return data_map, shelly_t
    except Exception as e:
        print(f"DEBUG: [{label} ERR] {e}")
        return None, None

# --- HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        data_map, shelly_t = await perform_record("MANUEL")
        if data_map:
            lines = [f"📍 {r}: <b>{v['temp']}°C</b> (Cible: <b>{v['target']}°C</b>)" for r,v in data_map.items()]
            if shelly_t: lines.append(f"   └ 🌡️ Shelly: <b>{shelly_t}°C</b>")
            try: await query.edit_message_text(f"🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())
            except: pass

    elif query.data in ["HOME", "ABS_16"]:
        target = 21 if query.data == "HOME" else 16
        m = await query.edit_message_text(f"⏳ Application {target}°C...")
        count = await apply_heating_temp(target)
        await m.edit_text(f"✅ Terminé : {count} appareils mis à {target}°C.", reply_markup=get_keyboard())

    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n\n<i>{s[3]} mesures en BDD.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],[InlineKeyboardButton("🏠 MAISON (21°)", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE (16°)", callback_data="ABS_16")],[InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]])

async def background_logger():
    await asyncio.sleep(10)
    while True:
        await perform_record("AUTO")
        await asyncio.sleep(3600)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("🚀 Thermostat v9.13", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
