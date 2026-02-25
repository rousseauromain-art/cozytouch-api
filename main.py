import os, asyncio, threading, httpx, psycopg2, time, base64, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "12.6 (Log Fix & State Fix)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

BEC_USER = os.getenv("BEC_EMAIL", OVERKIZ_EMAIL)
BEC_PASS = os.getenv("BEC_PASSWORD", OVERKIZ_PASSWORD)

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- LOGGING FORCE POUR KOYEB ---
def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- MODULE MAGELLAN (BASE64) ---
async def get_magellan_token():
    url = "https://apis.groupe-atlantic.com/token"
    app_auth_token = "czduc0RZZXdWbjVGbVV4UmlYN1pVSUM3ZFI4YTphSDEzOXZmbzA1ZGdqeDJkSFVSQkFTbmhCRW9h"
    
    headers = {
        "Authorization": f"Basic {app_auth_token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/1.12.1 (com.groupeatlantic.cozytouch; build:1.12.1.2; Android 11)"
    }
    payload = {"grant_type": "password", "username": BEC_USER, "password": BEC_PASS}

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, headers=headers, data=payload, timeout=12)
            log_koyeb(f"Magellan Auth Status: {r.status_code}")
            if r.status_code == 200:
                return r.json().get("access_token")
            log_koyeb(f"Magellan Error Body: {r.text}")
            return None
        except Exception as e:
            log_koyeb(f"Magellan Exception: {e}")
            return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur Auth (OAuth2 Base64)"
    
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        try:
            r = await client.get("https://apis.groupe-atlantic.com/magellan/cozytouch/v1/enduserAPI/setup")
            devices = r.json().get('devices', [])
            target = next((d for d in devices if any(x in d.get('uiWidget', '') for x in ["Water", "DHW"])), None)
            if not target: return "❓ Aquéo non trouvé"
            states = {s['name'].split(':')[-1]: s['value'] for s in target.get('states', [])}
            return f"💧 Mode: {states.get('OperatingModeState','??')}\n🚿 Capacité: {states.get('RemainingHotWaterCapacityState','??')}%"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- MODULE CHAUFFAGE ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_VALS:
                info = CONFORT_VALS[sid]
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                m_val = "internal" if target_mode == "HOME" else ("basic" if "Heater" in d.widget else "external")
                t_val = info["temp"] if target_mode == "HOME" else 16.0
                try:
                    await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(mode_cmd, [m_val])])
                    results.append(f"✅ {info['name']}")
                except: results.append(f"❌ {info['name']}")
        return "\n".join(results)

# --- INTERFACE ---
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
    
    if query.data in ["HOME", "ABSENCE"]:
        res = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
            await client.login()
            devices = await client.get_devices()
            lines = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_VALS:
                    s = {state.name: state.value for state in d.states}
                    # Test de plusieurs clés possibles pour la température actuelle
                    t = s.get("core:TemperatureState") or s.get("core:LuminanceState") or s.get("io:MiddleWaterTemperatureState") or "??"
                    c = s.get("core:TargetTemperatureState") or s.get("io:EffectiveTemperatureSetpointState") or "??"
                    lines.append(f"📍 <b>{CONFORT_VALS[sid]['name']}</b>: {t}°C (Cible: {c}°C)")
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data.startswith("BEC_"):
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J</b>\nMesures: {s[3]}\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\nΔ: {s[2]:+.1f}°C" if s and s[3]>0 else "Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

# --- MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
