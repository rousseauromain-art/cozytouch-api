import os, asyncio, threading, httpx, psycopg2, time, base64, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.0"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ROOMS_CONFIG = {
    "14253355#1": {"name": "Salon", "temp_home": 19.5},
    "1640746#1": {"name": "Chambre", "temp_home": 19.0},
    "190387#1": {"name": "Bureau", "temp_home": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp_home": 19.5}
}

_magellan_token = None
_magellan_token_expiry = 0
overkiz_client = None

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- OVERKIZ FIX ---
async def get_overkiz_client():
    global overkiz_client
    if overkiz_client is None:
        overkiz_client = OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"])
        await overkiz_client.login()
    return overkiz_client

# --- MAGELLAN FIX (404 & AUTH) ---
async def get_magellan_token():
    global _magellan_token, _magellan_token_expiry
    if _magellan_token and time.time() < _magellan_token_expiry - 60:
        return _magellan_token
    
    url = "https://apis.groupe-atlantic.com/token"
    headers = {
        "Authorization": "Basic czduc0RZZXdWbjVGbVV4UmlYN1pVSUM3ZFI4YTphSDEzOXZmbzA1ZGdqeDJkSFVSQkFTbmhCRW9h",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/1.12.1"
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers=headers, data={"grant_type": "password", "username": BEC_USER, "password": BEC_PASS}, timeout=10)
            if r.status_code == 200:
                d = r.json()
                _magellan_token, _magellan_token_expiry = d["access_token"], time.time() + d.get("expires_in", 3600)
                return _magellan_token
    except Exception as e: log_koyeb(f"Magellan Token Error: {e}")
    return None

async def fetch_magellan_setup(token):
    # Test de deux URLs possibles pour éviter la 404
    urls = [
        "https://apis.groupe-atlantic.com/magellan/cozytouch/v1/enduserAPI/setup",
        "https://apis.groupe-atlantic.com/enduserAPI/setup"
    ]
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=15) as client:
        for url in urls:
            r = await client.get(url)
            if r.status_code == 200: return r.json()
    return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur Auth Magellan"
    
    data = await fetch_magellan_setup(token)
    if not data: return "❌ Erreur 404 : Ressource API introuvable"
    
    devices = data.get('setup', {}).get('devices', data.get('devices', []))
    aqueo = next((d for d in devices if any(x in str(d.get('uiClass','')) + str(d.get('label','')) for x in ["HotWater", "Water", "Aqueo"])), None)
    
    if not aqueo: return "❓ Aquéo non trouvé dans le setup"
    
    if action == "GET":
        states = {s['name'].split(':')[-1]: s['value'] for s in aqueo.get('states', [])}
        return f"💧 Mode: {states.get('OperatingModeState','??')}\n🚿 Eau chaude: {states.get('RemainingHotWaterCapacityState','??')}%"
    
    return "✅ Commande reconnue (v13.0)"

# --- HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        client = await get_overkiz_client()
        
        if query.data in ["HOME", "ABSENCE"]:
            devices = await client.get_devices()
            res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in ROOMS_CONFIG:
                    conf = ROOMS_CONFIG[sid]
                    t_val = conf["temp_home"] if query.data == "HOME" else 16.0
                    m_val = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                    cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    try:
                        await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [m_val])])
                        res.append(f"✅ {conf['name']}")
                    except: res.append(f"❌ {conf['name']}")
            await query.edit_message_text(f"<b>RÉSULTAT:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            devices = await client.get_devices()
            lines = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in ROOMS_CONFIG:
                    s = {st.name: st.value for st in d.states}
                    t = s.get("core:TemperatureState") or s.get("io:TargetTemperatureState") or "??"
                    c = s.get("core:TargetTemperatureState") or "??"
                    lines.append(f"📍 <b>{ROOMS_CONFIG[sid]['name']}</b>: {t}°C (Cible: {c}°C)")
            await query.edit_message_text("🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days';")
            s = cur.fetchone(); cur.close(); conn.close()
            msg = f"📊 <b>BILAN 7J</b>\nMesures: {s[2]}\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C" if s and s[2] > 0 else "Pas de données."
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data.startswith("BEC_"):
            res = await manage_bec(query.data.replace("BEC_", ""))
            await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Global Error: {e}")
        global overkiz_client
        overkiz_client = None # Reset en cas d'erreur
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")]
    ])

# --- SERVER ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"BOOT v{VERSION}")
    # drop_pending_updates=True est crucial pour éviter le conflit au reboot
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
