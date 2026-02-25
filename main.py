import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.3 (Ballon Debug Mode)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
OVERKIZ_HOST = "https://ha110-1.overkiz.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

_magellan_token = None
_magellan_token_expiry = 0

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- MODULE BALLON AVEC LOGS DE DEBUG ---
async def get_magellan_token():
    global _magellan_token, _magellan_token_expiry
    if _magellan_token and time.time() < _magellan_token_expiry - 60:
        return _magellan_token

    log_koyeb("DEBUG AUTH: Tentative de récupération du Token Atlantic...")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ATLANTIC_API}/token",
                headers={"Authorization": f"Basic {CLIENT_BASIC}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "password", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS}, 
                timeout=12)
            
            if r.status_code != 200:
                log_koyeb(f"DEBUG AUTH: Echec Token (Code {r.status_code}): {r.text}")
                return None
            
            d = r.json()
            _magellan_token = d["access_token"]
            _magellan_token_expiry = time.time() + d.get("expires_in", 3600)
            log_koyeb("DEBUG AUTH: Token Atlantic OK.")
            return _magellan_token
        except Exception as e:
            log_koyeb(f"DEBUG AUTH: Erreur fatale Token: {e}")
            return None

async def get_overkiz_session():
    token = await get_magellan_token()
    if not token: return None

    async with httpx.AsyncClient() as client:
        try:
            log_koyeb("DEBUG AUTH: Récupération du JWT...")
            r = await client.get(f"{ATLANTIC_API}/magellan/accounts/jwt", headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                log_koyeb(f"DEBUG AUTH: Echec JWT (Code {r.status_code}): {r.text}")
                return None
            
            jwt = r.text.strip().strip('"')
            log_koyeb("DEBUG AUTH: JWT récupéré. Connexion à Overkiz ha110...")

            r2 = await client.post(f"{OVERKIZ_HOST}/enduser-mobile-web/enduserAPI/login", data={"jwt": jwt})
            if r2.status_code != 200 or not r2.json().get("success"):
                log_koyeb(f"DEBUG AUTH: Echec Login Overkiz: {r2.text}")
                return None
            
            log_koyeb("DEBUG AUTH: Session Overkiz validée (Cookies OK).")
            return dict(r2.cookies)
        except Exception as e:
            log_koyeb(f"DEBUG AUTH: Erreur session: {e}")
            return None

async def manage_bec(action="GET"):
    cookies = await get_overkiz_session()
    if not cookies: return "❌ Session Aquéo impossible (voir logs Koyeb)"

    async with httpx.AsyncClient(timeout=15, cookies=cookies) as client:
        try:
            r = await client.get(f"{OVERKIZ_HOST}/enduser-mobile-web/enduserAPI/setup")
            data = r.json()
            devices = data.get("devices", [])
            aqueo = next((d for d in devices if any(x in str(d.get("uiClass", "")) for x in ["HotWater", "WaterHeating"])), None)

            if not aqueo: 
                log_koyeb(f"DEBUG BEC: Aquéo non trouvé. Devices listés: {[d.get('uiClass') for d in devices]}")
                return "❓ Aquéo non trouvé"

            if action == "GET":
                states = {s["name"].split(":")[-1]: s["value"] for s in aqueo.get("states", [])}
                return f"💧 Mode: {states.get('OperatingModeState', '??')}\n🚿 Capacité: {states.get('RemainingHotWaterCapacityState', '??')}%"
            
            # Pour l'instant on se concentre sur le GET
            return "✅ Commande ignorée en Debug"
        except Exception as e: 
            return f"⚠️ Erreur BEC: {str(e)}"

# --- LE RESTE DU CODE (SANS CHANGEMENT) ---

async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            base_id = d.device_url.split('#')[0].split('/')[-1]
            full_id = f"{base_id}#1"
            if full_id in CONFORT_VALS:
                name = CONFORT_VALS[full_id]["name"]
                if name not in data: data[name] = {"temp": None, "target": None}
                states = {s.name: s.value for s in d.states}
                t = states.get("core:TemperatureState")
                c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                if t is not None: data[name]["temp"] = t
                if c is not None: data[name]["target"] = c
        return data, shelly_t

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        data, shelly_t = await get_current_data()
        lines = [f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)" for n, v in data.items()]
        if shelly_t: lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
        await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data.startswith("BEC_"):
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    # Autres handlers inchangés...

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
    
