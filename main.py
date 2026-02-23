import os, asyncio, threading, httpx, psycopg2, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

# VERSION 11.15 - Tentative ultime BEC (gduteil + Jeedom Setup)
VERSION = "11.15"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

BEC_EMAIL = os.getenv("BEC_EMAIL", OVERKIZ_EMAIL)
BEC_PASSWORD = os.getenv("BEC_PASSWORD", OVERKIZ_PASSWORD)

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE BEC (LA TENTATIVE ULTIME) ---
async def manage_bec(action="GET"):
    if not BEC_EMAIL or not BEC_PASSWORD: return "⚠️ Identifiants manquants"
    
    # URL cible pour les setups Wi-Fi natifs
    base_url = "https://ha101-1.overkiz.com/externalapi/rest"
    
    # L'ID Android spécifique du projet gduteil
    APP_ID = "cp7He8X6836936S6" 
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Application-Id": APP_ID,
        "User-Agent": "Cozytouch/1.12.1 (com.groupeatlantic.cozytouch; build:1.12.1.2; Android 11)",
        "Accept": "application/json"
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            # Encodage manuel pour les caractères spéciaux du mot de passe
            payload = f"userId={urllib.parse.quote(BEC_EMAIL)}&userPassword={urllib.parse.quote(BEC_PASSWORD)}"
            
            print(f"DEBUG BEC: Tentative Login (v11.15)...", flush=True)
            r = await client.post(f"{base_url}/login", content=payload)
            
            if r.status_code != 200:
                print(f"DEBUG BEC: Échec Login ({r.status_code})", flush=True)
                return f"❌ Erreur Auth ({r.status_code})"

            print("✅ DEBUG BEC: Login RÉUSSI !", flush=True)

            # Utilisation de /setup au lieu de /devices (préconisé pour Wi-Fi direct)
            r_setup = await client.get(f"{base_url}/setup")
            if r_setup.status_code != 200: return "❌ Erreur Setup"
            
            setup_data = r_setup.json()
            target_url = None
            for d in setup_data.get('devices', []):
                # On cherche l'Aqueo dans le setup
                if any(x in d.get('uiWidget', '') for x in ["Water", "DHW"]) or "Aqueo" in d.get('label', ''):
                    target_url = d['deviceURL']
                    states = {s['name'].split(':')[-1]: s['value'] for s in d.get('states', [])}
                    break
            
            if not target_url: return "❓ Ballon non trouvé"

            if action == "GET":
                mode = states.get("OperatingModeState", "Inconnu")
                capa = states.get("RemainingHotWaterCapacityState", "??")
                return f"💧 Mode: {mode}\n🚿 Capacité: {capa}%"

            # Action Absence/Home avec Timestamps (Source : ton .txt Jeedom)
            now = int(time.time())
            if action == "ABSENCE":
                end = now + (21 * 24 * 3600)
                msg = f"[{now},{end}]"
            else: # HOME
                end = now + 20
                msg = f"[{now},{end}]"

            cmd_payload = {
                "actions": [{
                    "deviceURL": target_url,
                    "commands": [{"name": "setAbsenceMode", "parameters": [msg]}]
                }]
            }
            
            print(f"DEBUG BEC: Envoi commande {msg}", flush=True)
            res = await client.post(f"{base_url}/exec/apply", json=cmd_payload)
            return "✅ Commande envoyée" if res.status_code == 200 else f"❌ Erreur Cmd {res.status_code}"

    except Exception as e:
        return f"⚠️ Erreur: {str(e)}"

# --- LE RESTE DU CODE (STABLE) ---
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

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), 
         InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT GÉNÉRAL", callback_data="LIST"), 
         InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"),
         InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Action Chauffage {query.data}...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data.startswith("BEC_"):
        action = query.data.replace("BEC_", "")
        await query.edit_message_text(f"⏳ Action Ballon {action}...")
        res = await manage_bec(action)
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    # ... (Garder la logique LIST et REPORT comme avant) ...

def main():
    # ... (Initialisation DB et Serveur Santé identique) ...
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
