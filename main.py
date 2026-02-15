import os, asyncio, threading, sys, time
import httpx
import psycopg2
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "9.0 (Full Display Restore)"

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

# --- DB & API HELPERS ---

def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT,
                temp_radiateur FLOAT,
                temp_shelly FLOAT,
                consigne FLOAT
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("DEBUG: DB Ready")
    except Exception as e: print(f"DEBUG DB ERR: {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    url = f"https://{SHELLY_SERVER}/device/status"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- CORE LOGIC ---

async def get_full_status():
    print(f"DEBUG: Scan v{VERSION}...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        
        # Initialisation des résultats
        results = {name: {"temp": "--", "target": "--"} for name in ROOMS.values()}
        
        for d in devices:
            # On nettoie l'URL pour la correspondance
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room = ROOMS[base_url]
                states = {s.name: s.value for s in d.states}
                
                # Extraction Température Ambiante
                t_amb = states.get("core:TemperatureState")
                if t_amb: results[room]["temp"] = t_amb
                
                # Extraction Consigne (on check plusieurs clés possibles chez Atlantic)
                t_set = states.get("io:EffectiveTemperatureSetpointState") or \
                        states.get("core:TargetTemperatureState")
                if t_set: results[room]["target"] = t_set
                
                print(f"DEBUG DEVICE: {room} | {t_amb}°C | Cible: {t_set}°C")

        # Construction du message
        lines = []
        for room, data in results.items():
            t_display = f"<b>{data['temp']}°C</b>" if data['temp'] != "--" else "--"
            s_display = f"<b>{data['target']}°C</b>" if data['target'] != "--" else "--"
            
            line = f"📍 {room}: {t_display} (Cible: {s_display})"
            
            if room == "Bureau" and shelly_t:
                diff = ""
                if data['temp'] != "--":
                    diff = f" (Δ {shelly_t - data['temp']:+.1f}°C)"
                line += f"\n   └ 🌡️ Shelly: <b>{shelly_t}°C</b>{diff}"
            lines.append(line)

        # Enregistrement DB
        if DB_URL:
            try:
                conn = psycopg2.connect(DB_URL)
                cur = conn.cursor()
                for room, data in results.items():
                    t_rad = data['temp'] if data['temp'] != "--" else None
                    t_set = data['target'] if data['target'] != "--" else None
                    t_sh = shelly_t if room == "Bureau" else None
                    if t_rad:
                        cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                                   (room, t_rad, t_sh, t_set))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e: print(f"DEBUG DB SAVE ERR: {e}")

        return "\n".join(lines)

# --- HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        status_text = await get_full_status()
        await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{status_text}", 
                                      parse_mode='HTML', 
                                      reply_markup=get_keyboard())
    
    elif query.data == "REPORT":
        # (Logique rapport identique v8.9)
        pass 

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]
    ])

# --- SERVER & MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Thermostat Connecté", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
