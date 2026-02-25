import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.2 (Restoration Valeurs + Magellan)"

# --- CONFIGURATION ---
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

# Reprise de ta structure CONFORT_VALS fonctionnelle
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

_magellan_token = None
_magellan_token_expiry = 0
_overkiz_cookies = None

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- MODULE LECTURE (RESTAURÉ DU 16/02) ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", 
                                data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

async def get_current_data():
    """Reprise exacte de la logique de ta version fonctionnelle"""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            # Nettoyage de l'ID pour correspondre à CONFORT_VALS
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

# --- MODULE BALLON (JWT AUTH) ---
async def get_magellan_token():
    global _magellan_token, _magellan_token_expiry
    if _magellan_token and time.time() < _magellan_token_expiry - 60: return _magellan_token
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{ATLANTIC_API}/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "password", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS}, timeout=12)
        if r.status_code != 200: return None
        d = r.json()
        _magellan_token, _magellan_token_expiry = d["access_token"], time.time() + d.get("expires_in", 3600)
        return _magellan_token

async def get_overkiz_session():
    global _overkiz_cookies
    token = await get_magellan_token()
    if not token: return None
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ATLANTIC_API}/magellan/accounts/jwt", headers={"Authorization": f"Bearer {token}"})
        jwt = r.text.strip().strip('"')
        r2 = await client.post(f"{OVERKIZ_HOST}/enduser-mobile-web/enduserAPI/login", data={"jwt": jwt})
        if not r2.json().get("success"): return None
        _overkiz_cookies = dict(r2.cookies)
        return _overkiz_cookies

async def manage_bec(action="GET"):
    cookies = await get_overkiz_session()
    if not cookies: return "❌ Session Aquéo impossible"
    async with httpx.AsyncClient(timeout=15, cookies=cookies) as client:
        try:
            r = await client.get(f"{OVERKIZ_HOST}/enduser-mobile-web/enduserAPI/setup")
            devices = r.json().get("devices", [])
            aqueo = next((d for d in devices if any(x in str(d.get("uiClass", "")) for x in ["HotWater", "WaterHeating"])), None)
            if not aqueo: return "❓ Aquéo non trouvé"
            if action == "GET":
                states = {s["name"].split(":")[-1]: s["value"] for s in aqueo.get("states", [])}
                return f"💧 Mode: {states.get('OperatingModeState', '??')}\n🚿 Capacité: {states.get('RemainingHotWaterCapacityState', '??')}%"
            cmd = "setAbsenceMode" if action == "ABSENCE" else "setOperatingMode"
            params = ["on"] if action == "ABSENCE" else ["manual"]
            payload = {"label": cmd, "actions": [{"deviceURL": aqueo["deviceURL"], "commands": [{"name": cmd, "parameters": params}]}]}
            res = await client.post(f"{OVERKIZ_HOST}/enduser-mobile-web/enduserAPI/exec/apply", json=payload)
            return "✅ Commande envoyée" if res.status_code in [200, 201] else f"❌ {res.status_code}"
        except Exception as e: return f"⚠️ {str(e)}"

# --- HANDLERS ---
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
        lines = []
        for n, v in data.items():
            lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
            if n == "Bureau" and shelly_t:
                lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
        await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Activation {query.data}...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
            await client.login()
            devices = await client.get_devices()
            res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_VALS:
                    conf = CONFORT_VALS[sid]; name = conf["name"]
                    t_val = conf["temp"] if query.data == "HOME" else 16.0
                    mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                    cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    try:
                        await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [mode])])
                        res.append(f"✅ {name}")
                    except: res.append(f"❌ {name}")
            await query.edit_message_text(f"<b>RÉSULTAT:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        try:
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
            s = cur.fetchone(); cur.close(); conn.close()
            msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        except: msg = "⚠️ Erreur SQL"
        await query.message.reply_text(msg, parse_mode='HTML')

    elif query.data.startswith("BEC_"):
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_headers(); self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"BOOT v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
                
