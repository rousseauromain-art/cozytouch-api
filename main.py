import os, asyncio, threading, httpx, psycopg2, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "15.4 (Rigueur BDD & Fix BEC)"

# --- CONFIGURATION ---
TOKEN            = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL    = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL           = os.getenv("DATABASE_URL")
BEC_USER         = os.getenv("BEC_EMAIL")
BEC_PASS         = os.getenv("BEC_PASSWORD")
SHELLY_TOKEN     = os.getenv("SHELLY_TOKEN")
SHELLY_ID        = os.getenv("SHELLY_ID")
SHELLY_SERVER    = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

ATLANTIC_API  = "https://apis.groupe-atlantic.com"
CLIENT_BASIC  = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
MY_SERVER     = SUPPORTED_SERVERS["atlantic_cozytouch"]

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 17.5, "eco": 14.5},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0}
}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# --- LOGIQUE HORAIRE ---
def is_heure_creuse():
    now = datetime.now().time()
    slots = [
        (datetime.strptime("01:56", "%H:%M").time(), datetime.strptime("07:56", "%H:%M").time()),
        (datetime.strptime("14:26", "%H:%M").time(), datetime.strptime("16:26", "%H:%M").time())
    ]
    for start, end in slots:
        if start <= now <= end: return True
    return False

def minutes_until_next_transition():
    now = datetime.now()
    transitions = ["01:56", "07:56", "14:26", "16:26"]
    times = []
    for t_str in transitions:
        t_time = datetime.strptime(t_str, "%H:%M").time()
        dt = datetime.combine(now.date(), t_time)
        if dt <= now: dt += timedelta(days=1)
        times.append(dt)
    return int((min(times) - now).total_seconds())

# --- BASE DE DONNÉES ---
def init_db():
    if not DB_URL: return
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS records (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, rad_temp FLOAT, shelly_temp FLOAT)")
    cur.execute("CREATE TABLE IF NOT EXISTS bec_transitions (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, index_kwh FLOAT, is_hc BOOLEAN)")
    conn.commit(); cur.close(); conn.close()

async def perform_record():
    """Enregistre les températures Bureau (Rad + Shelly) en BDD."""
    try:
        s_temp = None
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://{SHELLY_SERVER}/device/status?id={SHELLY_ID}&auth_key={SHELLY_TOKEN}", timeout=10)
            s_temp = r.json()['data']['device_status']['temperature:0']['tC']
        
        r_temp = None
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
            await c.login()
            devs = await c.get_devices()
            for d in devs:
                if "1640746#1" in d.device_url: # Bureau
                    r_temp = next((s.value for s in d.states if s.name == "core:TemperatureState"), None)
        
        if s_temp is not None and r_temp is not None and DB_URL:
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("INSERT INTO records (rad_temp, shelly_temp) VALUES (%s, %s)", (r_temp, s_temp))
            conn.commit(); cur.close(); conn.close()
            log(f"BDD OK: Rad={r_temp} Shelly={s_temp}")
    except Exception as e: log(f"ERR BDD: {e}")

# --- BALLON (BEC) ---
async def manage_bec(action="GET"):
    try:
        async with httpx.AsyncClient() as client:
            r_auth = await client.post(f"{ATLANTIC_API}/users/token",
                headers={"Authorization": f"Basic {CLIENT_BASIC}"},
                data={"grant_type": "password", "scope": "openid", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS}, timeout=15)
            token = r_auth.json().get("access_token")
            h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            
            r_setup = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
            setup = r_setup.json()[0]
            aqueo = next((d for d in setup["devices"] if "aqueo" in str(d.get("name","")).lower() or d.get("type") == "WATER_HEATER"), None)
            
            if action == "GET":
                r_caps = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={aqueo['deviceId']}", headers=h)
                caps = {c['capabilityId']: c['value'] for c in r_caps.json()}
                pui = caps.get(164, 0)
                idx = float(caps.get(168) if caps.get(168) is not None else caps.get(59, 0)) / 1000
                return (f"💧 <b>{aqueo['name']}</b>\n⚡ {pui}W | {'<b>HC</b>' if is_heure_creuse() else 'HP'}\n📊 {idx:.3f} kWh\n✈️ Absence: {'OUI' if setup.get('absence') else 'NON'}")

            if action in ["HOME", "ABSENCE"]:
                payload = {"id": setup["id"], "name": setup["name"], "type": setup["type"]}
                payload["absence"] = {"startDate": int(time.time()), "endDate": int(time.time()) + (365*24*3600)} if action == "ABSENCE" else {}
                await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup['id']}", json=payload, headers=h)
                return f"✅ Ballon: {action} OK"
    except Exception as e: return f"❌ Erreur BEC: {e}"

# --- INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS", callback_data="LIST")],
        [InlineKeyboardButton("📊 BILAN 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 ÉTAT BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🏡 BALLON HOME", callback_data="BEC_HOME"), InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data

    try:
        # 1. ACTIONS RADS (HOME/ABSENCE)
        if data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Mode {data}...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
                await c.login(); devs = await c.get_devices()
                for d in devs:
                    sid = d.device_url.split('/')[-1]
                    if sid in CONFORT_VALS:
                        conf = CONFORT_VALS[sid]
                        t_val = conf["temp"] if data == "HOME" else conf["eco"]
                        op = "internal" if data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                        cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                        await c.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [op])])
            await query.edit_message_text(f"✅ Radiateurs en mode {data}", reply_markup=get_keyboard())

        # 2. ÉTAT RADS
        elif data == "LIST":
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
                await c.login(); devs = await c.get_devices(); lines = []
                for d in devs:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS:
                        t = next((s.value for s in d.states if s.name == "core:TemperatureState"), "?")
                        lines.append(f"📍 <b>{CONFORT_VALS[bid]['name']}</b>: {t}°C")
                await query.edit_message_text("🌡️ <b>TEMPÉRATURES</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        # 3. ACTIONS BALLON
        elif data.startswith("BEC_"):
            res = await manage_bec(data.replace("BEC_", ""))
            await query.edit_message_text(res, parse_mode='HTML', reply_markup=get_keyboard())

        # 4. REPORT 7J
        elif data == "REPORT":
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(rad_temp), AVG(shelly_temp), AVG(rad_temp - shelly_temp), COUNT(*) FROM records WHERE timestamp > NOW() - INTERVAL '7 days'")
            s = cur.fetchone()
            msg = f"📊 <b>BILAN 7J</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\nΔ: {s[2]:+.1f}°C ({s[3]} pts)" if s[3]>0 else "Pas de données."
            cur.close(); conn.close()
            await query.message.reply_text(msg, parse_mode='HTML')

    except Exception as e:
        log(f"Handler ERR: {e}")
        await query.edit_message_text(f"❌ Erreur: {e}", reply_markup=get_keyboard())

# --- TASKS ---
async def background_rad_logger():
    while True:
        await perform_record() # Enregistre toutes les heures
        await asyncio.sleep(3600)

async def background_transition_logger():
    while True:
        sec = minutes_until_next_transition()
        await asyncio.sleep(sec + 10)
        # Logique transition BEC ici...

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), BaseHTTPRequestHandler).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_rad_logger())
    loop.create_task(background_transition_logger())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
    
