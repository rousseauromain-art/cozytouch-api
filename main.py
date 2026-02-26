import os, asyncio, threading, httpx, psycopg2, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "14.0 (IDs Fix + Eco + BEC Azure)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

# IDs corrigés : Chambre=190387, Bureau=1640746 (intervertis)
# Températures eco ajoutées + Bureau: 18°C confort, 15°C eco
CONFORT_VALS = {
    "14253355#1": {"name": "Salon",           "temp": 19.5, "eco": 16.0},
    "190387#1":   {"name": "Chambre",          "temp": 19.0, "eco": 16.0},
    "1640746#1":  {"name": "Bureau",           "temp": 18.0, "eco": 15.0},
    "4326513#1":  {"name": "Sèche-Serviette",  "temp": 19.5, "eco": 16.0},
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- DB ---
def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS temp_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT
        );""")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: log(f"DB ERR: {e}")

# --- SHELLY ---
async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status",
                                  data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

# --- OVERKIZ ---
async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            full_id = d.device_url.split('#')[0].split('/')[-1] + "#1"
            if full_id in CONFORT_VALS:
                name = CONFORT_VALS[full_id]["name"]
                if name not in data: data[name] = {"temp": None, "target": None}
                states = {s.name: s.value for s in d.states}
                t = states.get("core:TemperatureState")
                c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                if t is not None: data[name]["temp"] = t
                if c is not None: data[name]["target"] = c
        return data, shelly_t

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_VALS:
                info = CONFORT_VALS[sid]
                t_val = info["temp"] if target_mode == "HOME" else info["eco"]
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                m_val = "internal" if target_mode == "HOME" else ("basic" if "Heater" in d.widget else "external")
                try:
                    await client.execute_commands(d.device_url, [
                        Command("setTargetTemperature", [t_val]),
                        Command(mode_cmd, [m_val])
                    ])
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except Exception as e:
                    log(f"Erreur {info['name']}: {e}")
                    results.append(f"❌ <b>{info['name']}</b>")
        return "\n".join(results)

async def perform_record():
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        for name, vals in data.items():
            if vals["temp"] is not None:
                cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s,%s,%s,%s)",
                            (name, vals["temp"], (shelly_t if name == "Bureau" else None), vals["target"]))
        conn.commit(); cur.close(); conn.close()
    except Exception as e: log(f"RECORD ERR: {e}")

# --- MODULE BEC (Ballon Aquéo) ---
async def bec_get_token():
    """Auth Atlantic — email brut sans préfixe (validé)"""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.post(f"{ATLANTIC_API}/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "password", "username": BEC_USER, "password": BEC_PASS},
            timeout=12)
        log(f"BEC Auth: {r.status_code}")
        if r.status_code == 200:
            return r.json().get("access_token")
        log(f"BEC Auth error: {r.text[:150]}")
        return None

async def bec_scan():
    """Scan des endpoints Azure capturés dans le PCAP"""
    token = await bec_get_token()
    if not token:
        return "❌ Auth Magellan échouée"

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Endpoints issus du PCAP + variantes plausibles
    endpoints = [
        ("azfun", "https://azfun-messconsapi-prod-001.azurewebsites.net/api/v1/devices"),
        ("azfun", "https://azfun-messconsapi-prod-001.azurewebsites.net/api/v1/homes"),
        ("azfun", "https://azfun-messconsapi-prod-001.azurewebsites.net/api/v1/setup"),
        ("azfun", "https://azfun-messconsapi-prod-001.azurewebsites.net/api/consumption"),
        ("avail", "https://apis-availability.iot-groupe-atlantic.com/api/v1/devices"),
        ("avail", "https://apis-availability.iot-groupe-atlantic.com/api/v1/installations"),
        ("atl",   "https://apis.groupe-atlantic.com/iot/v1/devices"),
        ("atl",   "https://apis.groupe-atlantic.com/iot/v1/setup"),
        ("atl",   "https://apis.groupe-atlantic.com/magellan/cozytouch/v2/enduserAPI/setup"),
    ]

    lines = ["<b>🔍 Scan BEC endpoints :</b>"]
    async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
        for label, url in endpoints:
            try:
                r = await client.get(url, headers=headers)
                log(f"BEC {r.status_code} {url} → {r.text[:200]}")
                icon = "🟢" if r.status_code == 200 else ("🟡" if r.status_code not in [404, 403] else "🔴")
                lines.append(f"{icon} <code>{r.status_code}</code> [{label}] .../{url.split('/')[-1]}")
                if r.status_code == 200:
                    lines.append(f"   <code>{r.text[:80]}</code>")
            except Exception as e:
                log(f"BEC ERR {url}: {e}")
                lines.append(f"⚫ ERR [{label}] .../{url.split('/')[-1]}")

    return "\n".join(lines)

# --- INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"),
         InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"),
         InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON SCAN", callback_data="BEC_SCAN")],
    ])

# --- HANDLERS ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Activation {query.data}...")
            report = await apply_heating_mode(query.data)
            await query.edit_message_text(
                f"<b>RÉSULTAT {query.data}</b>\n\n{report}",
                parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            data, shelly_t = await get_current_data()
            lines = []
            for n, v in data.items():
                lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
                if n == "Bureau" and shelly_t:
                    lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            await query.edit_message_text(
                "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            conn = None
            try:
                conn = psycopg2.connect(DB_URL); cur = conn.cursor()
                cur.execute("""SELECT AVG(temp_radiateur), AVG(temp_shelly),
                    AVG(temp_shelly - temp_radiateur), COUNT(*)
                    FROM temp_logs WHERE room = 'Bureau'
                    AND timestamp > NOW() - INTERVAL '7 days'
                    AND temp_shelly IS NOT NULL;""")
                s = cur.fetchone(); cur.close()
                msg = (f"📊 <b>BILAN 7J (Bureau)</b>\n"
                       f"Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                       f"<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures.</i>"
                       ) if s and s[3] > 0 else "⚠️ Pas de données."
            except Exception as e:
                log(f"SQL ERR: {e}"); msg = "⚠️ Erreur SQL"
            finally:
                if conn: conn.close()
            await query.message.reply_text(msg, parse_mode='HTML')

        elif query.data == "BEC_SCAN":
            if not BEC_USER or not BEC_PASS:
                await query.edit_message_text("❌ BEC_EMAIL ou BEC_PASSWORD manquants dans Koyeb.", reply_markup=get_keyboard())
                return
            await query.edit_message_text("🚿 Scan endpoints Aquéo...", reply_markup=get_keyboard())
            result = await bec_scan()
            msg = result if len(result) <= 4000 else result[:3990] + "\n...(tronqué)"
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log(f"Handler ERR: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# --- BACKGROUND LOGGER ---
async def background_logger():
    while True:
        await perform_record()
        await asyncio.sleep(3600)

# --- MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
