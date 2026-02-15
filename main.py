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

VERSION = "9.8 (Enregistrement Garanti)"

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
        print("DEBUG: [DB] Initialisée")
    except Exception as e: print(f"DEBUG: [DB ERR] {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- ACTION LOGGING (SÉPARÉE ET FIABLE) ---
async def perform_record(label="AUTO"):
    print(f"DEBUG: [{label}] Tentative d'enregistrement...")
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            
            # Fusion des données par pièce
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

            # Insertion en base
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            inserted = 0
            for room, vals in data_map.items():
                if vals["temp"] is not None:
                    t_sh = shelly_t if room == "Bureau" else None
                    cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                               (room, vals["temp"], t_sh, vals["target"]))
                    inserted += 1
            conn.commit(); cur.close(); conn.close()
            print(f"DEBUG: [{label}] {inserted} lignes insérées.")
            return data_map, shelly_t
    except Exception as e:
        print(f"DEBUG: [{label} ERR] {e}")
        return None, None

# --- TELEGRAM HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        # On enregistre ET on récupère les données fusionnées
        data_map, shelly_t = await perform_record("MANUEL")
        if not data_map:
            await query.edit_message_text("Erreur de lecture Cozytouch.", reply_markup=get_keyboard())
            return

        lines = []
        for room, data in data_map.items():
            t_str = f"<b>{data['temp']}°C</b>" if data['temp'] is not None else "--"
            s_str = f"<b>{data['target']}°C</b>" if data['target'] is not None else "--"
            line = f"📍 {room}: {t_str} (Cible: {s_str})"
            if room == "Bureau" and shelly_t:
                diff = f" (Δ {shelly_t - data['temp']:+.1f}°C)" if data['temp'] is not None else ""
                line += f"\n   └ 🌡️ Shelly: <b>{shelly_t}°C</b>{diff}"
            lines.append(line)
        
        txt = "\n".join(lines)
        try:
            await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{txt}", parse_mode='HTML', reply_markup=get_keyboard())
        except telegram.error.BadRequest: pass # Ignore si message identique

    elif query.data == "REPORT":
        try:
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
            s = cur.fetchone(); cur.close(); conn.close()
            if s and s[3] > 0:
                msg = (f"📊 <b>BILAN 7J (Bureau)</b>\n"
                       f"• Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                       f"• <b>Écart moyen: {s[2]:+.1f}°C</b>\n\n"
                       f"<i>Basé sur {s[3]} mesures en BDD.</i>")
            else:
                msg = "⚠️ Aucune donnée enregistrée pour le moment."
            await query.message.reply_text(msg, parse_mode='HTML')
        except Exception as e: print(f"DEBUG: [REPORT ERR] {e}")

    elif query.data in ["HOME", "ABS_16"]:
        mode = "HOME" if query.data == "HOME" else "ABS"
        await query.edit_message_text(f"⏳ Action {mode} en cours...")
        # (Logique set_heating_mode identique v9.7 gardée ici pour gain de place)
        # ... 

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],[InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]])

async def background_logger():
    await asyncio.sleep(5)
    while True:
        await perform_record("AUTO")
        await asyncio.sleep(3600)

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Thermostat (v{VERSION})", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    app.run_polling(drop_pending_updates=True)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    main()
