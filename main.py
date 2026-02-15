import os, asyncio, threading, sys, time
import httpx
import psycopg2
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "9.6 (Fix Radiateurs & Robustesse)"

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
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
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

async def set_heating_mode(mode_type):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        count = 0
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                cmd = Command("setHeatingLevel", ["comfort"]) if mode_type == "HOME" else Command("setTargetTemperature", [16])
                try:
                    await client.execute_command(d.device_url, cmd)
                    count += 1
                except: pass
        return count

async def get_full_status():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        results = {name: {"temp": "--", "target": "--"} for name in ROOMS.values()}
        
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room = ROOMS[base_url]
                # On crée un dictionnaire propre de tous les états disponibles
                states = {s.name: s.value for s in d.states}
                
                # RECHERCHE TEMP AMBIANTE (plusieurs clés possibles)
                t_val = states.get("core:TemperatureState") or states.get("core:LuminanceState") # Parfois inversé sur certains modèles
                if t_val is not None: results[room]["temp"] = t_val
                
                # RECHERCHE CONSIGNE
                s_val = states.get("io:EffectiveTemperatureSetpointState") or \
                        states.get("core:TargetTemperatureState") or \
                        states.get("io:TargetHeatingLevelState")
                if s_val is not None: results[room]["target"] = s_val
                
                print(f"DEBUG: [SCAN] {room} -> Temp:{t_val} | Cible:{s_val}")

        lines = []
        for room, data in results.items():
            t_str = f"<b>{data['temp']}°C</b>" if data['temp'] != "--" else "--"
            s_str = f"<b>{data['target']}°C</b>" if data['target'] != "--" else "--"
            line = f"📍 {room}: {t_str} (Cible: {s_str})"
            if room == "Bureau" and shelly_t:
                diff = f" (Δ {shelly_t - data['temp']:+.1f}°C)" if data['temp'] != "--" else ""
                line += f"\n   └ 🌡️ Shelly: <b>{shelly_t}°C</b>{diff}"
            lines.append(line)
        return "\n".join(lines)

async def perform_record(label="AUTO"):
    # (Logique d'enregistrement simplifiée pour éviter les crashs)
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            for d in devices:
                base_url = d.device_url.split('#')[0]
                if base_url in ROOMS:
                    states = {s.name: s.value for s in d.states}
                    t_rad = states.get("core:TemperatureState")
                    t_set = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    if t_rad:
                        cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                                   (ROOMS[base_url], t_rad, (shelly_t if ROOMS[base_url]=="Bureau" else None), t_set))
            conn.commit(); cur.close(); conn.close()
            print(f"DEBUG: [{label}] OK")
    except Exception as e: print(f"DEBUG: [{label} ERR] {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "LIST":
        txt = await get_full_status()
        await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{txt}", parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data == "HOME":
        await query.edit_message_text("🏠 Mode MAISON...")
        c = await set_heating_mode("HOME")
        await query.edit_message_text(f"✅ MAISON activé ({c} app.)", reply_markup=get_keyboard())
    elif query.data == "ABS_16":
        await query.edit_message_text("❄️ Mode ABSENCE...")
        c = await set_heating_mode("ABS")
        await query.edit_message_text(f"✅ ABSENCE activé ({c} app.)", reply_markup=get_keyboard())
    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>" if s and s[3]>0 else "Données insuffisantes."
        await query.message.reply_text(msg, parse_mode='HTML')

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
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
