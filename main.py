import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.24 (Azure Endpoint Scanner)"

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
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
USER_AGENT_APP = "com.groupeatlantic.cozytouch/2.15.0 (Android; 14; FR)"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 17.5, "eco": 14.5},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0}
}

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 SCAN AZURE", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        # --- CHAUFFAGE (INCHANGÉ) ---
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Activation {query.data}...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login()
                devices = await client.get_devices()
                res = []
                for d in devices:
                    sid = d.device_url.split('/')[-1]
                    if sid in CONFORT_VALS:
                        conf = CONFORT_VALS[sid]
                        t_val = conf["temp"] if query.data == "HOME" else conf["eco"]
                        try:
                            mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                            cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [mode])])
                            res.append(f"✅ {conf['name']}")
                        except: res.append(f"❌ {conf['name']}")
                await query.edit_message_text(f"<b>RÉSULTAT:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        # --- ÉTAT (INCHANGÉ) ---
        elif query.data == "LIST":
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login()
                devices = await client.get_devices()
                lines = []
                for d in devices:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS:
                        st = {s.name: s.value for s in d.states}
                        lines.append(f"📍 <b>{CONFORT_VALS[bid]['name']}</b>: {st.get('core:TemperatureState')}°C")
                await query.edit_message_text("🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        # --- SCAN ENDPOINTS AZURE (INTÉGRATION CLAUDE) ---
        elif query.data == "BEC_GET":
            await query.edit_message_text("🚿 Test API Azure Atlantic...", reply_markup=get_keyboard())
            
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT_APP}, follow_redirects=True) as client:
                # 1. Auth pour récupérer le token frais
                r_auth = await client.post(f"{ATLANTIC_API}/token",
                    headers={"Authorization": f"Basic {CLIENT_BASIC}", "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "password", "username": BEC_USER, "password": BEC_PASS})
                
                if r_auth.status_code != 200:
                    await query.edit_message_text(f"❌ Auth échouée: {r_auth.status_code}", reply_markup=get_keyboard())
                    return
                
                token = r_auth.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                
                # Endpoints identifiés dans le PCAP
                endpoints = [
                    "https://azfun-messconsapi-prod-001.azurewebsites.net/api/installations",
                    "https://azfun-messconsapi-prod-001.azurewebsites.net/api/devices",
                    "https://apis-availability.iot-groupe-atlantic.com/api/installations",
                    "https://apis.groupe-atlantic.com/magellan/cozytouch/v1/enduserAPI/setup",
                ]
                
                lines = ["<b>🔍 Résultats Scan Azure :</b>"]
                for url in endpoints:
                    try:
                        r = await client.get(url, headers=headers, timeout=8)
                        status = r.status_code
                        name = url.split('/')[-1]
                        
                        # Si on a un 200, on logue un peu du contenu pour identifier le ballon
                        if status == 200:
                            log_koyeb(f"SUCCÈS {name}: {r.text[:300]}")
                            lines.append(f"• ✅ <b>{name}</b> (200 OK)")
                        else:
                            lines.append(f"• <code>{status}</code> {name}")
                    except Exception as e:
                        lines.append(f"• ❌ Error {name}")
                
                await query.edit_message_text("\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Erreur Globale: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# --- PROGRAMME ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
