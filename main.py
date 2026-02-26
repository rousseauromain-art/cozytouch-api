import os, asyncio, threading, httpx, psycopg2, time, sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.26 (Magellan V2 - hub.py Integration)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 17.5, "eco": 14.5},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0}
}

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- MODULE BEC (LOGIQUE HUB.PY) ---

async def bec_authenticate():
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{ATLANTIC_API}/users/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "password", "scope": "openid", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS},
            timeout=12)
        if r.status_code == 200: return r.json().get("access_token")
        log_koyeb(f"BEC Auth Error: {r.text}")
        return None

async def bec_get_setup(token):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2",
                             headers={"Authorization": f"Bearer {token}"}, timeout=12)
        return r.json() if r.status_code == 200 else None

async def bec_get_capabilities(token, device_id):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={device_id}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=12)
        return r.json() if r.status_code == 200 else []

async def manage_bec(action="GET"):
    token = await bec_authenticate()
    if not token: return "❌ Auth échouée (Vérifiez préfixe GA-PRIVATEPERSON)"
    
    setup_data = await bec_get_setup(token)
    if not setup_data: return "❌ Setup introuvable"
    
    setup = setup_data[0]
    setup_id = setup.get("id")
    aqueo = next((d for d in setup.get("devices", []) if any(x in str(d.get("name","")).lower() for x in ["aqueo", "ballon", "dhw"])), None)
    
    if not aqueo and setup.get("devices"): aqueo = setup["devices"][0]
    if not aqueo: return "❓ Aucun device trouvé"

    device_id = aqueo.get("deviceId")

    if action == "GET":
        caps = await bec_get_capabilities(token, device_id)
        lines = [f"💧 <b>{aqueo.get('name')}</b> (ID: {device_id})\n"]
        # On cherche les infos utiles dans les capabilities (Temp, Mode, etc)
        relevant = [c for c in caps if c.get('capabilityId') in [1, 2, 520, 521, 525, 526]]
        for cap in (relevant if relevant else caps[:10]):
            lines.append(f"• {cap.get('capabilityId')}: <code>{cap.get('value')}</code>")
        return "\n".join(lines)

    # Gestion Absence via PUT Setup
    json_data = {key: setup[key] for key in ("address", "area", "currency", "mainHeatingEnergy", "mainDHWEnergy", "name", "numberOfPersons", "numberOfRooms", "setupBuildingDate", "type") if key in setup}
    
    if action == "ABSENCE":
        now = int(datetime.now().timestamp())
        end = now + (30 * 24 * 3600)
        json_data["absence"] = {"startDate": now, "endDate": end}
    else:
        json_data["absence"] = {}

    async with httpx.AsyncClient() as client:
        r = await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}", json=json_data,
                             headers={"Authorization": f"Bearer {token}"}, timeout=12)
        return "✅ Opération réussie" if r.status_code in (200, 204) else f"❌ Erreur {r.status_code}"

# --- INTERFACE & HANDLERS ---

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🏡 BALLON HOME", callback_data="BEC_HOME"), InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Radiateurs {query.data}...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login(); devices = await client.get_devices(); res = []
                for d in devices:
                    sid = d.device_url.split('/')[-1]
                    if sid in CONFORT_VALS:
                        conf = CONFORT_VALS[sid]; t_val = conf["temp"] if query.data == "HOME" else conf["eco"]
                        try:
                            mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                            cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [mode])])
                            res.append(f"✅ {conf['name']} ({t_val}°C)")
                        except: res.append(f"❌ {conf['name']}")
                await query.edit_message_text(f"<b>RADIATEURS:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login(); devices = await client.get_devices(); lines = []; processed = set()
                for d in devices:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS and bid not in processed:
                        st = {s.name: s.value for s in d.states}
                        t = st.get("core:TemperatureState")
                        if t is not None:
                            c = st.get("io:EffectiveTemperatureSetpointState") or st.get("core:TargetTemperatureState")
                            lines.append(f"📍 <b>{CONFORT_VALS[bid]['name']}</b>: {t}°C (Cible: {c}°C)")
                            processed.add(bid)
                await query.edit_message_text("🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
            s = cur.fetchone(); cur.close(); conn.close()
            msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\nΔ: {s[2]:+.1f}°C" if s and s[3]>0 else "⚠️ Pas de données."
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data.startswith("BEC_"):
            action = query.data.replace("BEC_", "")
            await query.edit_message_text(f"⏳ Ballon {action}...", reply_markup=get_keyboard())
            res = await manage_bec(action)
            await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Erreur: {e}"); await query.edit_message_text(f"⚠️ Erreur : {e}", reply_markup=get_keyboard())

# --- SERVEUR & MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"DÉMARRAGE v{VERSION}"); app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
