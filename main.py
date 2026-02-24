import os, asyncio, threading, httpx, psycopg2, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "11.18 (Magellan Edition)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")

# Infos Magellan (Pour le Ballon Aquéo)
MAGELLAN_URL = "https://apis.groupe-atlantic.com"
# Client ID officiel Cozytouch Android extrait du code source
MAGELLAN_CLIENT_ID = "94615024-8149-4366-879e-4c74238e9a4e"

# Tes réglages Radiateurs
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE MAGELLAN (AQUÉO) ---
async def get_magellan_token():
    payload = {
        "grant_type": "password",
        "username": OVERKIZ_EMAIL,
        "password": OVERKIZ_PASSWORD,
        "client_id": MAGELLAN_CLIENT_ID
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{MAGELLAN_URL}/token", data=payload, headers=headers, timeout=15)
            if r.status_code == 200:
                return r.json().get("access_token")
            print(f"DEBUG MAGELLAN: Auth fail {r.status_code}", flush=True)
            return None
        except Exception as e:
            print(f"DEBUG MAGELLAN: Erreur {e}", flush=True)
            return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur Authentification (OAuth2)"
    
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        try:
            # 1. On cherche l'appareil
            r = await client.get(f"{MAGELLAN_URL}/magellan/cozytouch/v1/enduserAPI/setup")
            devices = r.json().get('devices', [])
            target_url = None
            states = {}
            for d in devices:
                if any(x in d.get('uiWidget', '') for x in ["Water", "DHW"]) or "Aqueo" in d.get('label', ''):
                    target_url = d['deviceURL']
                    states = {s['name'].split(':')[-1]: s['value'] for s in d.get('states', [])}
                    break
            
            if not target_url: return "❓ Aquéo non trouvé"

            if action == "GET":
                mode = states.get("OperatingModeState", "??")
                capa = states.get("RemainingHotWaterCapacityState", "??")
                return f"💧 Mode: {mode}\n🚿 Eau chaude: {capa}%"

            # 2. On envoie la commande
            now = int(time.time())
            end = now + (21 * 24 * 3600) if action == "ABSENCE" else now + 20
            # Format spécifique Magellan pour l'absence
            payload = {
                "actions": [{
                    "deviceURL": target_url,
                    "commands": [{"name": "setAbsenceMode", "parameters": [f"[{now},{end}]"]}]
                }]
            }
            res = await client.post(f"{MAGELLAN_URL}/magellan/cozytouch/v1/enduserAPI/exec/apply", json=payload)
            return "✅ Commande transmise" if res.status_code == 200 else f"❌ Erreur {res.status_code}"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- MODULE CHAUFFAGE (STABLE) ---
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
                t_val, m_val = (info["temp"], "internal") if target_mode == "HOME" else (16.0, mode_manuel)
                try:
                    await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(mode_cmd, [m_val])])
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except: results.append(f"❌ <b>{info['name']}</b> : Erreur")
        return "\n".join(results)

# --- INTERFACE & SQL ---
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
        await query.edit_message_text("⏳ Action Chauffage...")
        res = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data.startswith("BEC_"):
        await query.edit_message_text("⏳ Action Ballon (Magellan)...")
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(f"<b>RÉSULTAT BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

# --- INITIALISATION ---
def main():
    # Serveur Santé Koyeb
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), type('H', (BaseHTTPRequestHandler,), {'do_GET': lambda s: (s.send_response(200), s.end_headers(), s.wfile.write(b"OK"))})).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"Lancement v{VERSION}...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
