import os, asyncio, threading, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command
import time

VERSION = "11.2 (Shelly + BEC Aquéo Séparé)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

# Infos BEC (Sauter Aquéo Wi-Fi)
BEC_EMAIL = os.getenv("BEC_EMAIL")
BEC_PASSWORD = os.getenv("BEC_PASSWORD")

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- DATABASE ---
def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        cur.execute("CREATE TABLE IF NOT EXISTS bec_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, energy FLOAT, capacity FLOAT);")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"DB ERR: {e}")

# --- MODULE BEC AQUÉO (ISOLÉ) ---
async def manage_bec(action="GET"):
    if not BEC_EMAIL or not BEC_PASSWORD: return None
    
    # On teste d'abord Kiz-API (le plus probable pour l'Aquéo Wi-Fi)
    # Puis ha110 en secours
    endpoints = [
        "https://kiz-api.overkiz.com/externalapi/rest",
        "https://ha110-1.overkiz.com/externalapi/rest"
    ]
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Application-Id": "cp7He8X6836936S6",
        "User-Agent": "Cozytouch/2.10.0"
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
            logged_in = False
            active_url = ""

            for base_url in endpoints:
                print(f"DEBUG BEC: Tentative Login sur {base_url.split('//')[1].split('.')[0]}...", flush=True)
                try:
                    r = await client.post(f"{base_url}/login", data={"userId": BEC_EMAIL, "userPassword": BEC_PASSWORD})
                    if r.status_code == 200:
                        print(f"✅ DEBUG BEC: Login RÉUSSI sur {base_url}", flush=True)
                        active_url = base_url
                        logged_in = True
                        break
                    else:
                        print(f"❌ DEBUG BEC: Rejet {base_url} (Status: {r.status_code})", flush=True)
                except Exception as e:
                    print(f"⚠️ DEBUG BEC: Erreur connexion {base_url}", flush=True)

            if not logged_in:
                return "❌ Erreur d'authentification (404/401)"

            # 2. DISCOVERY & ACTION (Identique à ton scénario Jeedom)
            r_dev = await client.get(f"{active_url}/setup/devices")
            # ... (on garde la même logique de découverte et d'envoi de commande que la 11.8) ...
            target_url = None
            for d in r_dev.json():
                if any(x in d.get('widget', '') for x in ["Water", "Aqueo", "DHW"]):
                    target_url = d['deviceURL']
                    states = {s['name'].split(':'[-1]): s['value'] for s in d.get('states', [])}
                    break
            
            if not target_url: return "❌ Appareil non trouvé"

            if action == "GET":
                return {
                    "label": "Ballon Aquéo",
                    "capacity": states.get("RemainingHotWaterCapacityState", "??"),
                    "mode": states.get("OperatingModeState", "??")
                }

            now = int(time.time())
            # [start, end] comme dans ton .txt Jeedom
            end = now + (21 * 24 * 3600) if action == "ABSENCE" else now + 20
            msg = f"[{now},{end}]"

            payload = {
                "actions": [{
                    "deviceURL": target_url,
                    "commands": [{"name": "setAbsenceMode", "parameters": [msg]}]
                }]
            }
            
            res = await client.post(f"{active_url}/exec/apply", json=payload)
            return "✅ Succès" if res.status_code == 200 else f"❌ Erreur {res.status_code}"

    except Exception as e:
        print(f"DEBUG BEC ERROR: {str(e)}", flush=True)
    return None
# --- MODULE SHELLY ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- DATA AGGREGATION & LOGGING ---
async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            base_id = d.device_url.split('#')[0].split('/')[-1]
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
        
        bec = await manage_bec("GET")
        if bec and bec.get("energy") is not None:
            cur.execute("INSERT INTO bec_logs (energy, capacity) VALUES (%s, %s)", (bec["energy"], bec["capacity"]))
            
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"RECORD ERR: {e}")

# --- CHAUFFAGE LOGIC ---
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

# --- BOT INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUF. MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ CHAUF. ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("💧 BALLON (BEC)", callback_data="BEC_MENU")],
        [InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")]
    ])

def get_bec_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ BEC AUTO/ACTIF", callback_data="BEC_HOME"), InlineKeyboardButton("💤 BEC ABSENCE", callback_data="BEC_AWAY")],
        [InlineKeyboardButton("⬅️ RETOUR", callback_data="BACK")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Activation Chauffage {query.data}...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "BEC_MENU":
        await query.edit_message_text("💧 Lecture du Ballon...")
        bec = await manage_bec("GET")
        if bec:
            msg = f"💧 <b>{bec['label']}</b>\n🔋 Capacité : {bec['capacity']}%\n⚡ Mode : <code>{bec['mode']}</code>"
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_bec_keyboard())
        else:
            await query.edit_message_text("❌ Erreur connexion BEC.", reply_markup=get_keyboard())

    elif query.data == "BEC_HOME":
        res = await manage_bec("HOME")
        await query.edit_message_text(res or "❌ Erreur", reply_markup=get_keyboard())

    elif query.data == "BEC_AWAY":
        res = await manage_bec("ABSENCE")
        await query.edit_message_text(res or "❌ Erreur", reply_markup=get_keyboard())

    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        data, shelly_t = await get_current_data()
        lines = []
        for n, v in data.items():
            lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
            if n == "Bureau" and shelly_t:
                lines.append(f"    └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
        await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures en base.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')
    
    elif query.data == "BACK":
        await query.edit_message_text("🏠 Menu Principal", reply_markup=get_keyboard())

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
