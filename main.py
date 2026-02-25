import os, asyncio, threading, httpx, psycopg2, time, base64, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "12.7 (Smarter & Faster)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "czduc0RZZXdWbjVGbVV4UmlYN1pVSUM3ZFI4YTphSDEzOXZmbzA1ZGdqeDJkSFVSQkFTbmhCRW9h"

# --- PERSISTENCE & CACHE ---
_magellan_token = None
_magellan_token_expiry = 0
overkiz_client = None  # Client persistant

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- MODULE MAGELLAN (CORRIGÉ) ---
async def get_magellan_token():
    global _magellan_token, _magellan_token_expiry
    if _magellan_token and time.time() < _magellan_token_expiry - 60:
        return _magellan_token

    headers = {
        "Authorization": f"Basic {CLIENT_BASIC}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/1.12.1"
    }
    payload = {"grant_type": "password", "username": BEC_USER, "password": BEC_PASS}
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ATLANTIC_API}/token", headers=headers, data=payload, timeout=12)
            if r.status_code == 200:
                data = r.json()
                _magellan_token = data["access_token"]
                _magellan_token_expiry = time.time() + data.get("expires_in", 3600)
                log_koyeb("✅ Nouveau Token Magellan généré")
                return _magellan_token
            log_koyeb(f"❌ Erreur Auth Magellan: {r.status_code}")
            return None
        except Exception as e:
            log_koyeb(f"⚠️ Exception Magellan Auth: {e}")
            return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur authentification Magellan"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/v1/enduserAPI/setup",
                                 headers={"Authorization": f"Bearer {token}"})
            data = r.json()
            # Utilisation de la structure imbriquée signalée par Claude
            devices = data.get('setup', {}).get('devices', data.get('devices', []))
            
            aqueo = next((d for d in devices if any(x in str(d.get('uiClass','')) + str(d.get('label','')) 
                          for x in ["HotWater", "Water", "Aqueo", "DHW"])), None)

            if not aqueo: return "❓ Aquéo non trouvé (vérifier logs setup)"

            device_url = aqueo['deviceURL']

            if action == "GET":
                states = {s['name'].split(':')[-1]: s['value'] for s in aqueo.get('states', [])}
                mode = states.get('OperatingModeState', states.get('DHWMode', '??'))
                capa = states.get('RemainingHotWaterCapacityState', '??')
                return f"💧 Mode: {mode}\n🚿 Capacité: {capa}%"

            # Gestion des commandes Absence / Présence
            cmd_name = "setAbsenceMode" if action == "ABSENCE" else "setOperatingMode"
            params = ["on"] if action == "ABSENCE" else ["manual"] # À ajuster selon usage
            
            payload = {
                "label": cmd_name,
                "actions": [{"deviceURL": device_url, "commands": [{"name": cmd_name, "parameters": params}]}]
            }
            res = await client.post(f"{ATLANTIC_API}/magellan/cozytouch/v1/enduserAPI/exec/apply",
                                    headers={"Authorization": f"Bearer {token}"}, json=payload)
            return "✅ Commande envoyée" if res.status_code in [200, 201] else f"❌ Erreur {res.status_code}"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- MODULE CHAUFFAGE (OPTIMISÉ) ---
async def get_overkiz_client():
    global overkiz_client
    if overkiz_client is None:
        overkiz_client = OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"])
    try:
        if not overkiz_client.authenticated:
            await overkiz_client.login()
    except:
        await overkiz_client.login()
    return overkiz_client

async def apply_heating_mode(target_mode):
    client = await get_overkiz_client()
    devices = await client.get_devices()
    results = []
    
    # Mapping des ID pour tes radiateurs
    rooms = {"14253355#1": "Salon", "1640746#1": "Chambre", "190387#1": "Bureau", "4326513#1": "Sèche-Serviette"}
    
    for d in devices:
        sid = d.device_url.split('/')[-1]
        if sid in rooms:
            t_val = 19.5 if target_mode == "HOME" else 16.0
            mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
            m_val = "internal" if target_mode == "HOME" else ("basic" if "Heater" in d.widget else "external")
            try:
                await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(mode_cmd, [m_val])])
                results.append(f"✅ {rooms[sid]}")
            except: results.append(f"❌ {rooms[sid]}")
    return "\n".join(results)

# --- INTERFACE & HANDLERS ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Mode {query.data}...")
        res = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture en cours...")
        client = await get_overkiz_client()
        devices = await client.get_devices()
        lines = []
        rooms = {"14253355#1": "Salon", "1640746#1": "Chambre", "190387#1": "Bureau", "4326513#1": "Sèche-Serviette"}
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in rooms:
                s = {state.name: state.value for state in d.states}
                t = s.get("core:TemperatureState") or s.get("io:TargetTemperatureState") or "??"
                c = s.get("core:TargetTemperatureState") or s.get("io:TargetTemperatureState") or "??"
                lines.append(f"📍 <b>{rooms[sid]}</b>: {t}°C (Cible: {c}°C)")
        await query.edit_message_text("🌡️ <b>ÉTAT SYSTÈME</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data.startswith("BEC_"):
        action = query.data.replace("BEC_", "")
        await query.edit_message_text(f"⏳ Ballon: {action}...")
        res = await manage_bec(action)
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

# --- SERVEUR & START ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"BOOT v{VERSION}")
    app.run_polling()

if __name__ == "__main__":
    main()
    
