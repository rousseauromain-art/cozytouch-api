import os, asyncio, threading, httpx, psycopg2, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "15.3 (HC: 14h26-16h26 & 01h56-07h56)"

# =============================================================================
# CONFIGURATION
# =============================================================================
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

# Radiateurs
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 17.5, "eco": 14.5},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0}
}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# =============================================================================
# LOGIQUE HEURES CREUSES (MODIFIÉE)
# =============================================================================
def is_heure_creuse():
    """Vérifie si on est dans tes plages HC."""
    now = datetime.now().time()
    # Tes plages : 14:26-16:26 et 01:56-07:56
    slots = [
        (datetime.strptime("01:56", "%H:%M").time(), datetime.strptime("07:56", "%H:%M").time()),
        (datetime.strptime("14:26", "%H:%M").time(), datetime.strptime("16:26", "%H:%M").time())
    ]
    for start, end in slots:
        if start <= now <= end:
            return True
    return False

def minutes_until_next_transition():
    """Calcule le temps en secondes jusqu'au prochain changement HC/HP."""
    now = datetime.now()
    # Liste de tous les points de bascule dans la journée
    transitions = ["01:56", "07:56", "14:26", "16:26"]
    
    times = []
    for t_str in transitions:
        t_time = datetime.strptime(t_str, "%H:%M").time()
        dt = datetime.combine(now.date(), t_time)
        if dt <= now:
            dt += timedelta(days=1)
        times.append(dt)
    
    next_t = min(times)
    return int((next_t - now).total_seconds())

# =============================================================================
# BASE DE DONNÉES & BEC
# =============================================================================
def init_db():
    if not DB_URL: return
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    # Table Radiateurs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            rad_temp FLOAT,
            shelly_temp FLOAT
        )
    """)
    # Table Ballon (Transitions)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bec_transitions (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            index_kwh FLOAT,
            is_hc BOOLEAN
        )
    """)
    conn.commit(); cur.close(); conn.close()

def save_transition(index, is_hc):
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("INSERT INTO bec_transitions (index_kwh, is_hc) VALUES (%s, %s)", (index, is_hc))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"Erreur save_transition: {e}")

async def bec_get_index():
    """Récupère l'index (cap 59 ou 168) et la puissance (cap 164)."""
    try:
        async with httpx.AsyncClient() as client:
            r_auth = await client.post(f"{ATLANTIC_API}/users/token",
                headers={"Authorization": f"Basic {CLIENT_BASIC}"},
                data={"grant_type": "password", "scope": "openid", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS}, timeout=15)
            token = r_auth.json().get("access_token")
            h = {"Authorization": f"Bearer {token}"}
            
            r_setup = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h, timeout=15)
            setup = r_setup.json()[0]
            # On cherche par nom 'aqueo' ou par type WATER_HEATER pour être sûr
            aqueo = next((d for d in setup["devices"] if "aqueo" in str(d.get("name","")).lower() or d.get("type") == "WATER_HEATER"), None)
            
            if not aqueo: return None
            
            r_caps = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={aqueo['deviceId']}", headers=h, timeout=15)
            caps = {c['capabilityId']: c['value'] for c in r_caps.json()}
            
            # Index : priorité au cap 168 (standard Aqueo) puis 59
            idx_val = caps.get(168) if caps.get(168) is not None else caps.get(59, 0)
            return float(idx_val) / 1000
    except Exception as e:
        log(f"Erreur bec_get_index: {e}")
        return None

# =============================================================================
# MODULES RADIATEURS & SHELLY (Inchangés pour préserver ce qui marche)
# =============================================================================
async def get_shelly_temp():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://{SHELLY_SERVER}/device/status?id={SHELLY_ID}&auth_key={SHELLY_TOKEN}", timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

async def perform_record():
    s_temp = await get_shelly_temp()
    r_temp = None
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
            await c.login()
            devs = await c.get_devices()
            for d in devs:
                if d.device_url.split('/')[-1] == "1640746#1": # Bureau
                    r_temp = next((s.value for s in d.states if s.name == "core:TemperatureState"), None)
    except: pass
    if s_temp and r_temp and DB_URL:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("INSERT INTO records (rad_temp, shelly_temp) VALUES (%s, %s)", (r_temp, s_temp))
        conn.commit(); cur.close(); conn.close()
        log(f"Enregistrement : Rad={r_temp} Shelly={s_temp}")

# =============================================================================
# TELEGRAM HANDLERS (Utilisant tes boutons originaux)
# =============================================================================
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS", callback_data="LIST")],
        [InlineKeyboardButton("📊 BILAN 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 ÉTAT BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🏡 BALLON HOME", callback_data="BEC_HOME"), InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")]
    ])

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
                pui = int(caps.get(164, 0))
                idx = float(caps.get(168) if caps.get(168) is not None else caps.get(59, 0)) / 1000
                return (f"💧 <b>{aqueo['name']}</b>\n"
                        f"⚡ Puissance : {pui}W {'🔥' if pui>0 else '💤'}\n"
                        f"🕒 Tarif : {'<b>HC</b>' if is_heure_creuse() else 'HP'}\n"
                        f"📊 Index : {idx:.3f} kWh\n"
                        f"✈️ Absence : {'OUI' if setup.get('absence') else 'NON'}")

            if action in ["HOME", "ABSENCE"]:
                payload = {"id": setup["id"], "name": setup["name"], "type": setup["type"]}
                if action == "ABSENCE":
                    start = int(time.time())
                    payload["absence"] = {"startDate": start, "endDate": start + (365*24*3600)}
                else: payload["absence"] = {}
                await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup['id']}", json=payload, headers=h)
                return f"✅ Ballon : Mode {action} OK"
    except Exception as e: return f"❌ Erreur: {e}"

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    try:
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Passage en mode {query.data}...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
                await c.login(); devices = await c.get_devices(); res = []
                for d in devices:
                    sid = d.device_url.split('/')[-1]
                    if sid in CONFORT_VALS:
                        conf = CONFORT_VALS[sid]
                        t_val = conf["temp"] if query.data == "HOME" else conf["eco"]
                        op_mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                        cmd_name = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                        await c.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd_name, [op_mode])])
                        res.append(f"✅ {conf['name']}")
                await query.edit_message_text(f"<b>MODE {query.data} ACTIVÉ</b>\n\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
                await c.login(); devices = await c.get_devices(); lines = []
                for d in devices:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS:
                        st = {s.name: s.value for s in d.states}
                        lines.append(f"📍 <b>{CONFORT_VALS[bid]['name']}</b>: {st.get('core:TemperatureState')}°C")
                await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            if not DB_URL: await query.message.reply_text("DB non configurée"); return
            conn = psycopg2.connect(DB_URL); cur = conn.cursor()
            cur.execute("SELECT AVG(rad_temp), AVG(shelly_temp), AVG(rad_temp - shelly_temp), COUNT(*) FROM records WHERE timestamp > NOW() - INTERVAL '7 days'")
            s = cur.fetchone()
            msg = (f"📊 <b>BILAN 7J (Bureau)</b>\n"
                   f"Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                   f"<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures.</i>") if s and s[3] > 0 else "⚠️ Pas de données."
            cur.close(); conn.close()
            await query.message.reply_text(msg, parse_mode='HTML')

        elif query.data.startswith("BEC_"):
            action = query.data.replace("BEC_", "")
            res = await manage_bec(action)
            await query.edit_message_text(f"<b>BALLON</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    except Exception as e:
        log(f"Handler ERR: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# =============================================================================
# BACKGROUND TASKS
# =============================================================================
async def background_transition_logger():
    while True:
        wait_sec = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait_sec//60}min")
        await asyncio.sleep(wait_sec + 10)
        index = await bec_get_index()
        if index is not None:
            hc = is_heure_creuse()
            save_transition(index, hc)
            log(f"Transition enregistrée : {index:.3f} kWh — {'HC' if hc else 'HP'}")

async def background_rad_logger():
    while True:
        await asyncio.sleep(3600)
        if DB_URL: await perform_record()

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
    loop.create_task(background_transition_logger())
    loop.create_task(background_rad_logger())
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
