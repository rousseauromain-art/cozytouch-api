import os, asyncio, threading, httpx, psycopg2, time, base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

# VERSION 12.5 - Magellan Base64 Auth + Overkiz + SQL Stats
VERSION = "12.5"

# --- CONFIGURATION (Koyeb Env) ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

# Identifiants Sauter (Aquéo) - Utilise Overkiz par défaut si non défini
BEC_USER = os.getenv("BEC_EMAIL", OVERKIZ_EMAIL)
BEC_PASS = os.getenv("BEC_PASSWORD", OVERKIZ_PASSWORD)

# --- CONFIG RADIATEURS ---
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE MAGELLAN / SAUTER (AQUÉO) ---
async def get_magellan_token():
    url = "https://apis.groupe-atlantic.com/token"
    # Token d'application officiel Cozytouch (Base64)
    app_auth_token = "czduc0RZZXdWbjVGbVV4UmlYN1pVSUM3ZFI4YTphSDEzOXZmbzA1ZGdqeDJkSFVSQkFTbmhCRW9h"
    
    headers = {
        "Authorization": f"Basic {app_auth_token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/1.12.1 (com.groupeatlantic.cozytouch; build:1.12.1.2; Android 11)"
    }
    
    payload = {
        "grant_type": "password",
        "username": BEC_USER,
        "password": BEC_PASS
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, headers=headers, data=payload, timeout=12)
            if r.status_code == 200:
                return r.json().get("access_token")
            return None
        except: return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur Authentification (OAuth2 Base64)"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Cozytouch/1.12.1"
    }
    
    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        try:
            r = await client.get("https://apis.groupe-atlantic.com/magellan/cozytouch/v1/enduserAPI/setup")
            if r.status_code != 200: return f"❌ Serveur indisponible ({r.status_code})"
            
            devices = r.json().get('devices', [])
            target = next((d for d in devices if any(x in d.get('uiWidget', '') for x in ["Water", "DHW"])), None)
            
            if not target: return "❓ Aquéo non trouvé sur ce compte"

            if action == "GET":
                states = {s['name'].split(':')[-1]: s['value'] for s in target.get('states', [])}
                mode = states.get("OperatingModeState", "Inconnu")
                capa = states.get("RemainingHotWaterCapacityState", "??")
                return f"💧 Mode: {mode}\n🚿 Eau chaude: {capa}%"
            
            return f"✅ Commande {action} prête pour v12.6"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- MODULE CHAUFFAGE & SHELLY ---
async def get_shelly_temp():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", 
                                data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=8)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
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
                    results.append(f"✅ <b>{info['name']}</b>")
                except: results.append(f"❌ <b>{info['name']}</b>")
        return "\n".join(results)

# --- INTERFACE TELEGRAM ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT GÉNÉRAL", callback_data="LIST"), InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text("⏳ Application du mode...")
        res = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture de l'état...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            lines = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_VALS:
                    states = {s.name: s.value for s in d.states}
                    t = states.get("core:TemperatureState", "??")
                    c = states.get("core:TargetTemperatureState", "??")
                    lines.append(f"📍 <b>{CONFORT_VALS[sid]['name']}</b>: {t}°C (Cible: {c}°C)")
            if shelly_t: lines.append(f"\n🌡️ <b>Sonde Shelly (Bureau)</b>: {shelly_t}°C")
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data.startswith("BEC_"):
        await query.edit_message_text("⏳ Communication Magellan...")
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "REPORT":
        try:
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("""SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) 
                           FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;""")
            s = cur.fetchone(); cur.close(); conn.close()
            msg = (f"📊 <b>BILAN 7J (Bureau)</b>\nMesures : {s[3]}\n"
                   f"Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                   f"<b>Δ moyen: {s[2]:+.1f}°C</b>") if s and s[3]>0 else "⚠️ Aucune donnée."
        except: msg = "⚠️ Erreur SQL"
        await query.message.reply_text(msg, parse_mode='HTML')

# --- INITIALISATION & SANTE ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    # Serveur HTTP pour Koyeb
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Lancement v{VERSION}...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
