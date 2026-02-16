import os, asyncio, threading, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.21 (Valeurs Salon/SS Fixes)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

# Tes réglages de température
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- BDD ---
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

# --- ACTION (D'après la version fonctionnelle) ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            short_id = d.device_url.split('/')[-1]
            if short_id in CONFORT_VALS:
                info = CONFORT_VALS[short_id]
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_manuel = "basic" if "Heater" in d.widget else "external"
                
                target_temp = info["temp"] if target_mode == "HOME" else 16.0
                target_mode_val = "internal" if target_mode == "HOME" else mode_manuel
                
                try:
                    await client.execute_commands(d.device_url, [
                        Command(name="setTargetTemperature", parameters=[target_temp]),
                        Command(name=mode_cmd, parameters=[target_mode_val])
                    ])
                    results.append(f"✅ <b>{info['name']}</b> : {target_temp}°C")
                except: results.append(f"❌ <b>{info['name']}</b> : Erreur")
        return "\n".join(results)

# --- LECTURE (Fix None) ---
async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        
        for d in devices:
            # On identifie la pièce par la racine de l'URL
            base_id = d.device_url.split('#')[0].split('/')[-1]
            # On cherche s'il y a un composant #1 pour avoir le nom
            full_id = f"{base_id}#1"
            if full_id in CONFORT_VALS:
                name = CONFORT_VALS[full_id]["name"]
                if name not in data: data[name] = {"temp": None, "target": None}
                
                states = {s.name: s.value for s in d.states}
                t = states.get("core:TemperatureState")
                c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                
                if t is not None: data[name]["temp"] = t
                if c is not None: data[name]["target"] = c
        return data, shelly_t

async def perform_record():
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        for name, vals in data.items():
            if vals["temp"] is not None:
                cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                           (name, vals["temp"], (shelly_t if name=="Bureau" else None), vals["target"]))
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"RECORD ERR: {e}")

# --- HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        m = await query.edit_message_text(f"⏳ Activation {query.data}...")
        report = await apply_heating_mode(query.data)
        await m.edit_text(f"<b>RÉSULTAT {query.data}</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        data, shelly_t = await get_current_data()
        lines = [f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)" for n, v in data.items()]
        if shelly_t: lines.append(f"\n🌡️ <b>Shelly</b>: {shelly_t}°C")
        await query.edit_message_text("🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

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
