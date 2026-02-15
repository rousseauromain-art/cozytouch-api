import os, asyncio, threading, sys, time
import httpx
import psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "8.3 (Listing & Logs Max)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-61-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

# --- LOGIQUE SHELLY & DB ---

async def get_shelly_temp():
    if not SHELLY_TOKEN or not SHELLY_ID:
        print("DEBUG: Paramètres Shelly manquants dans l'env.")
        return None
    url = f"https://{SHELLY_SERVER}/device/status"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            res = r.json()
            # On affiche le JSON brut en debug pour être sûr du chemin
            t = res['data']['device_status']['temperature:0']['tC']
            print(f"DEBUG: Shelly GT3 récupéré = {t}°C")
            return t
    except Exception as e:
        print(f"DEBUG ERREUR Shelly: {e}")
        return None

# --- CORE FUNCTION : LE LISTING ---

async def get_detailed_listing():
    print(f"\n--- SCAN INTEGRAL v{VERSION} ---")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        # Structure pour stocker les infos triées
        results = {name: {"temp": None, "target": None} for name in ROOMS.values()}
        
        for d in all_devices:
            # LOG CONSOLE SYSTEMATIQUE (Debug Max)
            states = {s.name: s.value for s in d.states}
            print(f"LOG: {d.label} | {d.device_url} | Etats: {states}")
            
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room_name = ROOMS[base_url]
                # Récupération température ambiante
                t_val = states.get("core:TemperatureState")
                if t_val and t_val > 0: results[room_name]["temp"] = t_val
                # Récupération consigne
                if "io:EffectiveTemperatureSetpointState" in states:
                    results[room_name]["target"] = states["io:EffectiveTemperatureSetpointState"]

        # Récupération Shelly
        shelly_t = await get_shelly_temp()
        
        # Construction du message Telegram
        lines = []
        for room, data in results.items():
            t_amb = f"<b>{data['temp']}°C</b>" if data['temp'] else "--"
            t_set = f"<b>{data['target']}°C</b>" if data['target'] else "--"
            
            line = f"📍 {room}: {t_amb} (Consigne: {t_set})"
            
            # Affichage spécifique pour le Bureau avec le Shelly juste en dessous
            if room == "Bureau" and shelly_t is not None:
                diff_str = ""
                if data['temp']:
                    diff = shelly_t - data['temp']
                    diff_str = f" (Δ {diff:+.1f}°C)"
                line += f"\n   └ 🌡️ <b>Shelly GT3: {shelly_t}°C</b>{diff_str}"
            
            lines.append(line)
        
        return "\n".join(lines)

# --- TELEGRAM HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message de transition
    m = await query.edit_message_text("🔍 Récupération des données en cours...")
    
    if query.data == "LIST":
        report = await get_detailed_listing()
        await m.edit_text(f"🌡️ <b>ÉTAT DES RADIATEURS</b>\n\n{report}", 
                          parse_mode='HTML', 
                          reply_markup=get_keyboard())
    
    # ... (les autres conditions HOME / ABS / REPORT 7J)

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]
    ])

# --- SERVEUR WEB & DEMARRAGE ---

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

def main():
    # Initialisation de la DB au démarrage si présente
    if DB_URL:
        try:
            conn = psycopg2.connect(DB_URL)
            conn.close()
            print("LOG: Connexion cozyDB OK")
        except: print("LOG: cozyDB non joignable")

    # Serveur HTTP pour Koyeb
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), HealthCheck).serve_forever(), daemon=True).start()
    
    # Bot Telegram
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Bonjour ! Voici l'état de votre chauffage :", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Démarrage v{VERSION} - Logs activés")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
