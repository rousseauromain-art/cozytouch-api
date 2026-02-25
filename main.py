import os, asyncio, threading, httpx, psycopg2, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

# VERSION 13.1 - Basée sur l'analyse de ton APK (v2.15.1)
VERSION = "13.1"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

# Variables pour le BEC
BEC_EMAIL = OVERKIZ_EMAIL
BEC_PASSWORD = OVERKIZ_PASSWORD

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE BEC (LOGIQUE APK) ---
async def manage_bec(action="GET"):
    # ID extrait de ton APK Cozytouch
    APP_ID = "cp7He8X6836936S6"
    BASE_URL = "https://ha101-1.overkiz.com/externalapi/rest"
    
    headers = {
        "X-Application-Id": APP_ID,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/2.15.1 (Android; 11)",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
        try:
            # 1. AUTHENTIFICATION (Point d'entrée identifié dans le code Java)
            # On utilise 'authenticate/user' au lieu de 'login'
            payload = {
                "userId": BEC_EMAIL,
                "userPassword": BEC_PASSWORD
            }
            
            print(f"DEBUG BEC: Authentification via l'ID de l'APK...", flush=True)
            # Important: data=payload pour envoyer en x-www-form-urlencoded
            r_auth = await client.post(f"{BASE_URL}/authenticate/user", data=payload)
            
            if r_auth.status_code != 200:
                print(f"❌ DEBUG BEC: Échec Auth ({r_auth.status_code})", flush=True)
                return f"❌ Auth KO ({r_auth.status_code})"

            print("✅ DEBUG BEC: Connexion réussie !", flush=True)

            # 2. RÉCUPÉRATION DU SETUP COMPLET
            r_setup = await client.get(f"{BASE_URL}/setup")
            devices = r_setup.json().get('devices', [])
            
            target_url = None
            for d in devices:
                widget = d.get('uiWidget', '')
                if any(x in widget for x in ["Water", "DHW", "Aqueo"]):
                    target_url = d['deviceURL']
                    states = {s['name'].split(':')[-1]: s['value'] for s in d.get('states', [])}
                    break
            
            if not target_url: return "❓ Aquéo non trouvé"

            if action == "GET":
                mode = states.get("OperatingModeState", "??")
                capa = states.get("RemainingHotWaterCapacityState", "??")
                return f"💧 Mode: {mode}\n🚿 Eau chaude: {capa}%"

            # 3. COMMANDE (setAbsenceMode)
            now = int(time.time())
            # Fin dans 21 jours pour l'absence, ou 20 sec pour le retour (mode présence)
            end = now + (21 * 24 * 3600) if action == "ABSENCE" else now + 20
            
            cmd_payload = {
                "actions": [{
                    "deviceURL": target_url,
                    "commands": [{"name": "setAbsenceMode", "parameters": [f"[{now},{end}]"]}]
                }]
            }
            
            res = await client.post(f"{BASE_URL}/exec/apply", json=cmd_payload)
            return "✅ Commande envoyée" if res.status_code == 200 else f"❌ Erreur Cmd {res.status_code}"

        except Exception as e:
            return f"⚠️ Erreur: {str(e)}"

# --- FONCTIONS RADIATEURS & SHELLY (INCHANGÉES) ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

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
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), 
         InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT GÉNÉRAL", callback_data="LIST"), 
         InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"),
         InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Chauffage {query.data}...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data.startswith("BEC_"):
        action = query.data.replace("BEC_", "")
        await query.edit_message_text(f"⏳ Ballon {action}...")
        res = await manage_bec(action)
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            lines = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_VALS:
                    states = {s.name: s.value for s in d.states}
                    t = states.get("core:TemperatureState")
                    c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    name = CONFORT_VALS[short_id]["name"]
                    lines.append(f"📍 <b>{name}</b>: {t}°C (Cible: {c}°C)")
                    if name == "Bureau" and shelly_t: lines.append(f"    └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

# --- PROGRAMME ---
def main():
    # Serveur de santé pour Koyeb
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), type('H', (BaseHTTPRequestHandler,), {'do_GET': lambda s: (s.send_response(200), s.end_headers(), s.wfile.write(b"OK"))})).serve_forever(), daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"Lancement v{VERSION}...")
    app.run_polling()

if __name__ == "__main__":
    main()
