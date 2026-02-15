import os, asyncio, threading, sys, time, socket
import httpx
import psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "8.6 (Ultra Debug Shelly)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-61-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

# --- DEBUG AU DÉMARRAGE ---
print(f"--- INITIALISATION v{VERSION} ---")
print(f"DEBUG ENV: SHELLY_SERVER configuré sur -> '{SHELLY_SERVER}'")
try:
    # Test de résolution DNS pour voir si l'adresse est connue du système
    ip = socket.gethostbyname(SHELLY_SERVER)
    print(f"DEBUG ENV: DNS OK! Le serveur {SHELLY_SERVER} répond à l'IP {ip}")
except Exception as e:
    print(f"DEBUG ENV ERREUR: Impossible de résoudre le nom '{SHELLY_SERVER}'. Erreur: {e}")
    print("CONSEIL: Vérifiez qu'il n'y a pas d'espace, de 'https://' ou de '/' dans votre variable SHELLY_SERVER sur Koyeb.")

# --- LOGIQUE SHELLY ---

async def get_shelly_temp():
    if not SHELLY_TOKEN or not SHELLY_ID:
        print("DEBUG API: Shelly Token ou ID manquant dans les variables.")
        return None
        
    url = f"https://{SHELLY_SERVER}/device/status"
    print(f"DEBUG API: Tentative d'appel Shelly sur {url}...")
    
    try:
        async with httpx.AsyncClient() as client:
            # On utilise un dictionnaire pour les data
            payload = {"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}
            r = await client.post(url, data=payload, timeout=15)
            
            print(f"DEBUG API: Status Code = {r.status_code}")
            
            if r.status_code != 200:
                print(f"DEBUG API: Erreur Réponse = {r.text}")
                return None
                
            res = r.json()
            # Navigation prudente dans le JSON
            if 'data' in res and 'device_status' in res['data']:
                status = res['data']['device_status']
                # On cherche la température (peut varier selon les modèles Gen3)
                temp = status.get('temperature:0', {}).get('tC')
                print(f"DEBUG API: Température trouvée = {temp}°C")
                return temp
            else:
                print(f"DEBUG API: Structure JSON inattendue = {res}")
                return None
    except Exception as e:
        print(f"DEBUG API CRASH: {type(e).__name__}: {e}")
        return None

# --- LE RESTE DU CODE (Listing & Telegram) ---

async def get_detailed_listing():
    print("DEBUG: Lancement du listing complet...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        # Shelly en premier pour ne pas ralentir le reste si ça timeout
        shelly_t = await get_shelly_temp()
        
        results = ""
        # On définit ici les ROOMS comme avant
        ROOMS = {
            "io://2091-1547-6688/14253355": "Salon",
            "io://2091-1547-6688/1640746": "Chambre",
            "io://2091-1547-6688/190387": "Bureau",
            "io://2091-1547-6688/4326513": "Sèche-Serviette"
        }
        
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
                        line += f"\n   └ ⚠️ <i>Shelly injoignable (voir logs)</i>"
                status_lines.append(line)
        
        return "\n".join(status_lines)

# --- HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        m = await query.edit_message_text("🔍 Récupération...")
        report = await get_detailed_listing()
        await m.edit_text(f"🌡️ <b>ÉTAT ACTUEL</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")]
    ])

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Thermostat Actif", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Lancement du logger en arrière-plan
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
