import os, asyncio, threading, sys, time
import httpx
import psycopg2
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import telegram
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.11 (Fix Action OperatingMode)"

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

# --- INITIALISATION DB ---
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

# --- TON CODE ACTION QUI MARCHE (Restauré) ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            print(f"\n>>> DÉBUT SESSION - ACTION: {target_mode} <<<")
            devices = await client.get_devices()
            
            temps = {}
            for d in devices:
                if "core:TemperatureState" in [s.name for s in d.states]:
                    root_id = d.device_url.split('/')[-1].split('#')[0]
                    state = next((s for s in d.states if s.name == "core:TemperatureState"), None)
                    if state and state.value is not None:
                        temps[root_id] = state.value
                        print(f"DEBUG TEMP: {root_id} mesure {state.value}°C")

            results = []
            for d in devices:
                if d.widget in ["AtlanticElectricalHeaterWithAdjustableTemperatureSetpoint", "AtlanticElectricalTowelDryer"]:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    status = ""
                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            print(f"TENTATIVE: {d.label} ({short_id}) -> {cmd_val}")
                            await client.execute_command(d.device_url, Command("setOperatingMode", [cmd_val]))
                            print(f"RETOUR: Succès pour {short_id}")
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"ERREUR sur {short_id}: {e}")
                            status = " | ❌ Erreur"

                    current_temp = temps.get(root_id, "??")
                    t_str = f"{round(current_temp, 1)}°C" if isinstance(current_temp, (int, float)) else "??"
                    results.append(f"<b>{d.label}</b> ({short_id})\n└ Temp: {t_str}{status}")

            print(f">>> FIN SESSION - {len(results)} appareils traités <<<\n")
            return "\n\n".join(results)
        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
            return f"Erreur : {str(e)}"

# --- ENREGISTREMENT BDD ---
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
            return data_map, shelly_t
    except Exception as e:
        print(f"DEBUG: [{label} ERR] {e}")
        return None, None

# --- TELEGRAM HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        data_map, shelly_t = await perform_record("MANUEL")
        if data_map:
            lines = [f"📍 {r}: <b>{v['temp']}°C</b> (Cible: <b>{v['target']}°C</b>)" for r,v in data_map.items()]
            if shelly_t: lines.append(f"   └ 🌡️ Shelly: <b>{shelly_t}°C</b>")
            txt = "\n".join(lines)
            try: await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{txt}", parse_mode='HTML', reply_markup=get_keyboard())
            except: pass

    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n\n<i>{s[3]} mesures.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

    elif query.data in ["HOME", "ABS_16"]:
        mode_label = "ABSENCE" if query.data == "ABS_16" else "HOME"
        m = await query.edit_message_text(f"⏳ Mode {mode_label}...")
        report = await apply_heating_mode(mode_label)
        await m.edit_text(f"✅ <b>RÉSULTAT {mode_label}</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],[InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]])

async def background_logger():
    await asyncio.sleep(5)
    while True:
        await perform_record("AUTO")
        await asyncio.sleep(3600)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Thermostat (v{VERSION})", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
