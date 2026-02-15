import os, asyncio, threading, sys, time, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.16 (Restauration Stricte v4.3)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

# Ton dictionnaire de températures de confort
CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

# Mapping pour la BDD
ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

# --- LOGIQUE BDD & SHELLY ---
def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"DEBUG DB: {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- TA FONCTION QUI FONCTIONNE (Inchangée) ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n--- EXECUTION v{VERSION} : {target_mode} ---")
            
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_TEMPS:
                    confort_val = CONFORT_TEMPS[short_id]
                    mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    mode_manuel = "basic" if "Heater" in d.widget else "external"
                    
                    try:
                        if target_mode == "HOME":
                            print(f"ENVOI {short_id} -> {confort_val}°C puis PROG")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[confort_val]),
                                Command(name=mode_cmd, parameters=["internal"])
                            ])
                        elif target_mode == "ABSENCE":
                            print(f"ENVOI {short_id} -> 16.0°C puis MANUEL")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[16.0]),
                                Command(name=mode_cmd, parameters=[mode_manuel])
                            ])
                    except Exception as e:
                        print(f"ERREUR ENVOI {short_id}: {e}")

            print("Pause de 10s pour synchronisation serveur...")
            await asyncio.sleep(10)

            results = []
            for attempt in range(2):
                print(f"Tentative de lecture #{attempt + 1}")
                updated_devices = await client.get_devices()
                results = []
                all_synced = True

                for d in devices:
                    short_id = d.device_url.split('/')[-1]
                    if short_id in CONFORT_TEMPS:
                        current_target = "??"
                        for ud in updated_devices:
                            if ud.device_url == d.device_url:
                                state = ud.states.get("core:TargetTemperatureState")
                                if state: current_target = state.value
                        
                        expected = CONFORT_TEMPS[short_id] if target_mode == "HOME" else 16.0
                        if current_target != expected:
                            all_synced = False
                        results.append(f"<b>{d.label}</b>\n└ Cible: {expected}°C | Reçu: {current_target}°C")
                
                if all_synced: break
                if attempt == 0: await asyncio.sleep(10)

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur critique : {e}"

# --- SCAN AUTO POUR BDD ---
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
            print("DEBUG: Scan auto enregistré.")
    except Exception as e: print(f"DEBUG RECORD: {e}")

# --- HANDLERS TELEGRAM ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(text="⏳ Commandes envoyées. Attente de confirmation (10-20s)...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(text=f"<b>RAPPORT FINAL</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        # On réutilise ta logique de lecture rapide pour l'état actuel
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

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],[InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]])

async def background_logger():
    await asyncio.sleep(15)
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
    
    print(f"=== DEMARRAGE v{VERSION} ===")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
