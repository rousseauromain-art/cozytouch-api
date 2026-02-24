import os, asyncio, threading, httpx, psycopg2, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

# VERSION 11.16 - Tentative BEC + Rapports SQL 7j
VERSION = "11.16"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

BEC_EMAIL = os.getenv("BEC_EMAIL", OVERKIZ_EMAIL)
BEC_PASSWORD = os.getenv("BEC_PASSWORD", OVERKIZ_PASSWORD)

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE BEC (SCAN LARGE + ENCODAGE) ---
async def manage_bec(action="GET"):
    if not BEC_EMAIL or not BEC_PASSWORD: return "⚠️ Config manquante"
    base_url = "https://ha101-1.overkiz.com/externalapi/rest"
    APP_ID = "cp7He8X6836936S6" 
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Application-Id": APP_ID,
        "User-Agent": "Cozytouch/1.12.1 (com.groupeatlantic.cozytouch; build:1.12.1.2; Android 11)",
        "Accept": "application/json"
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
            login_payload = f"userId={urllib.parse.quote(BEC_EMAIL)}&userPassword={urllib.parse.quote(BEC_PASSWORD)}"
            print(f"DEBUG BEC: Tentative Login (v11.16)...", flush=True)
            r = await client.post(f"{base_url}/login", content=login_payload)
            
            if r.status_code != 200:
                print(f"DEBUG BEC: Échec Login {r.status_code}", flush=True)
                return f"❌ Erreur Auth {r.status_code}"

            r_setup = await client.get(f"{base_url}/setup")
            devices = r_setup.json().get('devices', [])
            target_url, states = None, {}

            for d in devices:
                widget, label = d.get('uiWidget', ''), d.get('label', '')
                if any(x in widget for x in ["Water", "DHW", "Aqueo"]) or "Aqueo" in label:
                    target_url = d['deviceURL']
                    states = {s['name'].split(':')[-1]: s['value'] for s in d.get('states', [])}
                    break
            
            if not target_url: return "❓ Ballon non trouvé"

            if action == "GET":
                mode = states.get("OperatingModeState", "??")
                capa = states.get("RemainingHotWaterCapacityState", "??")
                return f"💧 Mode: {mode}\n🚿 Capacité: {capa}%"

            now = int(time.time())
            end = now + (21 * 24 * 3600) if action == "ABSENCE" else now + 20
            msg = f"[{now},{end}]"
            cmd_payload = {"actions": [{"deviceURL": target_url, "commands": [{"name": "setAbsenceMode", "parameters": [msg]}]}]}
            res = await client.post(f"{base_url}/exec/apply", json=cmd_payload)
            return "✅ Succès" if res.status_code == 200 else f"❌ Erreur Cmd {res.status_code}"
    except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- FONCTIONS RADIATEURS & SHELLY ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

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
                t_val = info["temp"] if target_mode == "HOME" else 16.0
                m_val = "internal" if target_mode == "HOME" else mode_manuel
                try:
                    await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(mode_cmd, [m_val])])
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except: results.append(f"❌ <b>{info['name']}</b> : Erreur")
        return "\n".join(results)

# --- INTERFACE TELEGRAM ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), 
         InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT GÉNÉRAL", callback_data="LIST"), 
         InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"),
         InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Chauffage en cours...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data.startswith("BEC_"):
        action = query.data.replace("BEC_", "")
        await query.edit_message_text(f"⏳ Action Ballon {action}...")
        res = await manage_bec(action)
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            lines = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_VALS:
                    states = {s.name: s.value for s in d.states}
                    t, name = states.get("core:TemperatureState"), CONFORT_VALS[short_id]["name"]
                    c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    lines.append(f"📍 <b>{name}</b>: {t}°C (Cible: {c}°C)")
                    if name == "Bureau" and shelly_t: lines.append(f"    └ 🌡️ <i>Sonde Shelly : {shelly_t}°C</i>")
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        try:
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
            s = cur.fetchone(); cur.close(); conn.close()
            msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>" if s and s[3]>0 else "⚠️ Pas de données."
            await query.message.reply_text(msg, parse_mode='HTML')
        except Exception as e: await query.message.reply_text(f"❌ Erreur SQL : {e}")

# --- PROGRAMME ---
def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"DB ERR: {e}")

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"Lancement v{VERSION}...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
