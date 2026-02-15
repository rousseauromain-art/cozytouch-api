import os, asyncio, threading, sys, time, socket
import httpx
import psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "8.7 (Shelly DNS OK - Fix NameError)"

# --- CONFIGURATION ---
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

# --- INITIALISATION ---
print(f"--- START v{VERSION} ---")
print(f"DEBUG ENV: Serveur Shelly cible -> '{SHELLY_SERVER}'")

# --- BASE DE DONNÉES ---
def init_db():
    if not DB_URL:
        print("DEBUG DB: Pas de DATABASE_URL, stockage désactivé.")
        return
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
        print("DEBUG DB: Table temp_logs opérationnelle.")
    except Exception as e:
        print(f"DEBUG DB ERROR: {e}")

# --- LOGIQUE SHELLY ---
async def get_shelly_temp():
    if not SHELLY_TOKEN or not SHELLY_ID:
        print("DEBUG API: Shelly non configuré.")
        return None
    url = f"https://{SHELLY_SERVER}/device/status"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=15)
            if r.status_code == 200:
                res = r.json()
                # On tente de récupérer la température (chemin standard Gen3)
                t = res['data']['device_status']['temperature:0']['tC']
                print(f"DEBUG API: Shelly {SHELLY_ID} = {t}°C")
                return t
            else:
                print(f"DEBUG API: Erreur HTTP {r.status_code}")
                return None
    except Exception as e:
        print(f"DEBUG API ERROR: {e}")
        return None

# --- LISTING ---
async def get_detailed_listing():
    print("DEBUG: Scan Overkiz en cours...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        
        status_lines = []
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                states = {s.name: s.value for s in d.states}
                room = ROOMS[base_url]
                t_amb = states.get("core:TemperatureState", "--")
                t_set = states.get("io:EffectiveTemperatureSetpointState", "--")
                
                line = f"📍 {room}: <b>{t_amb}°C</b> (Consigne: <b>{t_set}°C</b>)"
                if room == "Bureau":
                    if shelly_t is not None:
                        diff = shelly_t - t_amb if isinstance(t_amb, (int, float)) else 0
                        line += f"\n   └ 🌡️ <b>Shelly GT3: {shelly_t}°C</b> (Δ {diff:+.1f}°C)"
                    else:
                        line += f"\n   └ ⚠️ <i>Shelly injoignable</i>"
                status_lines.append(line)
        return "\n".join(status_lines)

# --- HANDLERS TELEGRAM ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "LIST":
        m = await query.edit_message_text("🔍 Récupération des données...")
        try:
            report = await get_detailed_listing()
            await m.edit_text(f"🌡️ <b>ÉTAT ACTUEL</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
        except Exception as e:
            await m.edit_text(f"Erreur : {e}")

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")]
    ])

# --- SERVEUR DE SANTÉ ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

# --- MAIN ---
def main():
    init_db() # Cette fois elle est bien définie au dessus !
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Bonjour ! État du chauffage :", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Bot v{VERSION} démarré avec succès.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
