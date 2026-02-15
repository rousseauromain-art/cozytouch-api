import os, asyncio, threading, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.17 (Direct Command + Stats)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

CONFORT_TEMPS = {"14253355#1": 19.5, "1640746#1": 19.0, "190387#1": 19.0, "4326513#1": 19.5}
ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

# --- BASE DE DONNÉES ---
def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"DB ERR: {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- COMMANDES (SANS TIMER) ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            short_id = d.device_url.split('/')[-1]
            if short_id in CONFORT_TEMPS:
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_val = ("internal" if target_mode == "HOME" else ("basic" if "Heater" in d.widget else "external"))
                temp_val = CONFORT_TEMPS[short_id] if target_mode == "HOME" else 16.0
                try:
                    await client.execute_commands(d.device_url, [
                        Command(name="setTargetTemperature", parameters=[temp_val]),
                        Command(name=mode_cmd, parameters=[mode_val])
                    ])
                    results.append(f"✅ {d.label} -> {temp_val}°C")
                except: results.append(f"❌ {d.label} (Erreur)")
        return "\n".join(results)

# --- SCAN AUTO & STATS ---
async def perform_record():
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
                    data_map[room]["temp"] = states.get("core:TemperatureState")
                    data_map[room]["target"] = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
            
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            for room, vals in data_map.items():
                if vals["temp"] is not None:
                    cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                               (room, vals["temp"], (shelly_t if room=="Bureau" else None), vals["target"]))
            conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"SCAN ERR: {e}")

# --- HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        m = await query.edit_message_text(f"⏳ Envoi des commandes {query.data}...")
        report = await apply_heating_mode(query.data)
        await m.edit_text(f"<b>RÉSULTAT</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture en cours...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            lines = []
            for d in devices:
                base_url = d.device_url.split('#')[0]
                if base_url in ROOMS:
                    states = {s.name: s.value for s in d.states}
                    lines.append(f"📍 {ROOMS[base_url]}: <b>{states.get('core:TemperatureState')}°C</b> (Cible: {states.get('core:TargetTemperatureState')}°C)")
            if shelly_t: lines.append(f"🌡️ Shelly: <b>{shelly_t}°C</b>")
            await query.edit_message_text("\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],[InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")]])

async def background_logger():
    while True:
        await perform_record()
        await asyncio.sleep(3600)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
