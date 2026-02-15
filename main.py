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

VERSION = "9.4 (Actions Restore & Debug Max)"

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

# --- LOGIQUE D'ACTION (HOME / ABS) ---

async def set_heating_mode(mode_type):
    """mode_type: 'HOME' ou 'ABS'"""
    print(f"DEBUG: [ACTION] Tentative de passage en mode {mode_type}...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        executed_count = 0
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                print(f"DEBUG: [ACTION] Envoi commande vers {ROOMS[base_url]}...")
                
                if mode_type == "HOME":
                    # Mode Confort / Auto
                    cmd = Command("setHeatingLevel", ["comfort"])
                else:
                    # Mode Absence (16°C par exemple)
                    cmd = Command("setTargetTemperature", [16])
                
                try:
                    await client.execute_command(d.device_url, cmd)
                    executed_count += 1
                    print(f"DEBUG: [ACTION] Succès pour {ROOMS[base_url]}")
                except Exception as e:
                    print(f"DEBUG: [ACTION ERR] Échec pour {ROOMS[base_url]} : {e}")
        
        return executed_count

# --- LOGIQUE D'ENREGISTREMENT ---

async def perform_record(label="AUTO"):
    print(f"DEBUG: [{label}] Relevé en cours...")
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            
            # Shelly
            shelly_t = None
            try:
                url = f"https://{SHELLY_SERVER}/device/status"
                async with httpx.AsyncClient() as h:
                    r = await h.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
                    shelly_t = r.json()['data']['device_status']['temperature:0']['tC']
            except: pass

            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            for d in devices:
                base_url = d.device_url.split('#')[0]
                if base_url in ROOMS:
                    states = {s.name: s.value for s in d.states}
                    t_rad = states.get("core:TemperatureState")
                    t_set = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    t_sh = shelly_t if ROOMS[base_url] == "Bureau" else None
                    if t_rad:
                        cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                                   (ROOMS[base_url], t_rad, t_sh, t_set))
            conn.commit()
            cur.close(); conn.close()
            print(f"DEBUG: [{label}] Données enregistrées.")
    except Exception as e: print(f"DEBUG: [{label} ERR] {e}")

async def background_logger():
    await asyncio.sleep(5)
    while True:
        await perform_record("AUTO")
        await asyncio.sleep(3600)

# --- TELEGRAM HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        # ... (Logique get_full_status identique à v9.3)
        pass

    elif query.data == "HOME":
        m = await query.edit_message_text("🏠 Passage en mode MAISON...")
        count = await set_heating_mode("HOME")
        await m.edit_text(f"✅ Mode MAISON activé sur {count} appareils.", reply_markup=get_keyboard())

    elif query.data == "ABS_16":
        m = await query.edit_message_text("❄️ Passage en mode ABSENCE (16°C)...")
        count = await set_heating_mode("ABS")
        await m.edit_text(f"✅ Mode ABSENCE activé sur {count} appareils.", reply_markup=get_keyboard())

    elif query.data == "REPORT":
        # ... (Logique Report identique à v9.3)
        pass

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]
    ])

# ... (Main, HealthCheck, etc. comme v9.3)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Thermostat Connecté (v{VERSION})", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
