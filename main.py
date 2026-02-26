import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.19 (Final Bridge ha110)"

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
        [InlineKeyboardButton("🚿 BALLON (Scan)", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        # --- CHAUFFAGE & ÉTAT (STRUCTURE STABLE) ---
        if query.data in ["HOME", "ABSENCE", "LIST", "REPORT"]:
            # On garde le code validé précédemment pour ces fonctions
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

        # --- BALLON (SCAN MAGELLAN) ---
        elif query.data == "BEC_GET":
            await query.edit_message_text("🚿 Pont Magellan ha110...", reply_markup=get_keyboard())
            
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT_APP}) as client:
                # 1. Token Atlantic
                r = await client.post(f"{ATLANTIC_API}/token",
                    headers={"Authorization": f"Basic {CLIENT_BASIC}"},
                    data={"grant_type": "password", "username": BEC_USER, "password": BEC_PASS})
                
                if r.status_code != 200:
                    await query.edit_message_text(f"❌ Erreur Auth Atlantic", reply_markup=get_keyboard())
                    return
                
                # 2. JWT Atlantic
                token = r.json()["access_token"]
                r2 = await client.get(f"{ATLANTIC_API}/magellan/accounts/jwt", headers={"Authorization": f"Bearer {token}"})
                jwt = r2.text.strip().replace('"', '')

                # 3. Login Overkiz Magellan (Crucial)
                # On envoie le JWT comme paramètre de formulaire
                r3 = await client.post("https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI/login",
                                       data={"jwt": jwt},
                                       headers={"Content-Type": "application/x-www-form-urlencoded"})
                
                log_koyeb(f"ha110 Login Status: {r3.status_code}")
                
                # Si le login direct échoue, on tente avec le JWT dans l'URL (parfois requis par ha110)
                if r3.status_code != 200 or not r3.json().get("success"):
                    log_koyeb("Tentative alternative ha110...")
                    r3 = await client.get(f"https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI/login?jwt={jwt}")

                if not r3.json().get("success"):
                    await query.edit_message_text(f"❌ Login ha110 impossible.\nRéponse: {r3.text[:100]}", reply_markup=get_keyboard())
                    return

                # 4. Setup
                r4 = await client.get("https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI/setup", cookies=r3.cookies)
                devices = r4.json().get("devices", [])
                
                if not devices:
                    await query.edit_message_text("❓ Aucun équipement Magellan.", reply_markup=get_keyboard())
                    return

                lines = ["<b>📋 Équipements Magellan :</b>"]
                for d in devices:
                    lines.append(f"• <b>{d.get('label','?')}</b>\n  <code>{d.get('deviceURL','?')}</code>")
                
                await query.edit_message_text("\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Erreur: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# --- SERVEUR & MAIN ---
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
    
