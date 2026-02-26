import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.23 (Dual-Client Bridge ha110)"

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

# --- MODULES ANNEXES ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", 
                                data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

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
                            res.append(f"✅ {conf['name']} ({t_val}°C)")
                        except Exception as e_dev:
                            log_koyeb(f"Erreur sur {conf['name']}: {e_dev}")
                            res.append(f"❌ {conf['name']}")
                await query.edit_message_text(f"<b>RÉSULTAT {query.data}:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login()
                devices = await client.get_devices()
                shelly_t = await get_shelly_temp()
                lines = []
                for d in devices:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS:
                        st = {s.name: s.value for s in d.states}
                        t = st.get("core:TemperatureState")
                        c = st.get("io:EffectiveTemperatureSetpointState") or st.get("core:TargetTemperatureState")
                        n = CONFORT_VALS[bid]["name"]
                        lines.append(f"📍 <b>{n}</b>: {t}°C (Cible: {c}°C)")
                        if n == "Bureau" and shelly_t:
                            lines.append(f"    └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("""
                SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) 
                FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;
            """)
            s = cur.fetchone(); cur.close(); conn.close()
            msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>" if s and s[3]>0 else "⚠️ Pas de données."
            await query.message.reply_text(msg, parse_mode='HTML')

        elif query.data == "BEC_GET":
            if not BEC_USER or not BEC_PASS:
                await query.edit_message_text("❌ BEC_EMAIL ou BEC_PASSWORD manquants.", reply_markup=get_keyboard())
                return
            await query.edit_message_text("🚿 Connexion Magellan (Dual-Client)...", reply_markup=get_keyboard())
            
            # Client GET (suit les redirects pour le JWT)
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT_APP}, follow_redirects=True) as get_client:
                # Client POST (bloque les redirects pour garder le POST)
                async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT_APP}, follow_redirects=False) as post_client:
                    
                    # 1. Auth Atlantic (POST)
                    r = await post_client.post(f"{ATLANTIC_API}/token",
                        headers={"Authorization": f"Basic {CLIENT_BASIC}", "Content-Type": "application/x-www-form-urlencoded"},
                        data={"grant_type": "password", "username": BEC_USER, "password": BEC_PASS}, timeout=10)
                    
                    if r.status_code != 200:
                        await query.edit_message_text(f"❌ Auth Atlantic: {r.status_code}", reply_markup=get_keyboard())
                        return
                    
                    token = r.json()["access_token"]

                    # 2. JWT (GET - doit suivre les redirects !)
                    r2 = await get_client.get(f"{ATLANTIC_API}/magellan/accounts/jwt",
                                               headers={"Authorization": f"Bearer {token}"})
                    log_koyeb(f"JWT status: {r2.status_code} - Body: {r2.text[:50]}")
                    
                    if r2.status_code != 200:
                        await query.edit_message_text(f"❌ JWT échoué: {r2.status_code}", reply_markup=get_keyboard())
                        return

                    jwt = r2.text.strip().strip('"')

                    # 3. Login Overkiz (POST - sans redirect auto)
                    login_url = "https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI/login"
                    r3 = await post_client.post(login_url,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data={"jwt": jwt})
                    
                    log_koyeb(f"Login Initial: {r3.status_code}")

                    # Redirection manuelle pour garder le POST
                    if r3.status_code in [301, 302, 307, 308]:
                        redirect_url = r3.headers.get("location")
                        log_koyeb(f"🔄 Redirect POST vers: {redirect_url}")
                        r3 = await post_client.post(redirect_url,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            data={"jwt": jwt})

                    if not r3.json().get("success"):
                        await query.edit_message_text(f"❌ Login ha110 échoué\n<code>{r3.text[:100]}</code>", parse_mode='HTML', reply_markup=get_keyboard())
                        return

                    # 4. Setup (GET)
                    r4 = await get_client.get("https://ha110-1.overkiz.com/enduser-mobile-web/enduserAPI/setup", cookies=r3.cookies)
                    devices = r4.json().get("devices", [])
                    
                    if not devices:
                        await query.edit_message_text("❓ Aucun équipement Magellan.", reply_markup=get_keyboard())
                        return

                    lines = ["<b>📋 Équipements BEC :</b>"]
                    for d in devices:
                        lines.append(f"• <b>{d.get('label','?')}</b>")
                        lines.append(f"  URL: <code>{d.get('deviceURL','?')}</code>")

                    await query.edit_message_text("\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Erreur Globale: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# --- SERVEUR ---
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
                    
