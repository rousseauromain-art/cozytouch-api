import socket
import os, asyncio, threading, httpx, psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command
import httpx

VERSION = "9.24 (Final - Shelly UI & Debug)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")
# --- CONFIG BEC (CozySauter) ---
# Utilisation des variables confirmées
EMAIL_BEC = os.getenv("BEC_EMAIL")
PASS_BEC = os.getenv("BEC_PASSWORD")
BEC_EMAIL = os.getenv("BEC_EMAIL")
BEC_PASSWORD = os.getenv("BEC_PASSWORD")
SERVER_BEC = "ha110-1.overkiz.com"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}


# --- CLASSE TECHNIQUE POUR LE BEC SAUTER ---
class SauterAuth:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.headers = {"X-Application-Id": "cp7He8X6836936S6"}
        self.client = httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=20)

    async def get_data(self):
        # Tentative de login + récupération immédiate
        login_url = "https://ha101-1.overkiz.com/externalapi/rest/login"
        r = await self.client.post(login_url, data={"userId": self.email, "userPassword": self.password})
        if r.status_code == 200:
            # Si login OK, on demande les appareils
            dev_res = await self.client.get("https://ha101-1.overkiz.com/externalapi/rest/setup/devices")
            return dev_res.json() if dev_res.status_code == 200 else None
        print(f"DEBUG SAUTER ERR: {r.status_code} - {r.text}")
        return None

    async def login(self):
        url = f"{self.base_url}/login"
        payload = {"userId": self.email, "userPassword": self.password}
        # On tente le login direct
        r = await self.session.post(url, data=payload)
        if r.status_code == 200:
            print("✅ Connexion Sauter réussie via API Directe")
            return True
        print(f"❌ Échec Auth Sauter: {r.status_code} - {r.text}")
        return False

    async def get_devices(self):
        r = await self.session.get(f"{self.base_url}/setup/devices")
        return r.json() if r.status_code == 200 else []

def init_db():
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS temp_logs (id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT);")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"DB ERR: {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            base_id = d.device_url.split('#')[0].split('/')[-1]
            full_id = f"{base_id}#1"
            if full_id in CONFORT_VALS:
                name = CONFORT_VALS[full_id]["name"]
                if name not in data: data[name] = {"temp": None, "target": None}
                states = {s.name: s.value for s in d.states}
                t = states.get("core:TemperatureState")
                c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                if t is not None: data[name]["temp"] = t
                if c is not None: data[name]["target"] = c
        return data, shelly_t

async def perform_record():
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        count = 0
        for name, vals in data.items():
            if vals["temp"] is not None:
                cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                           (name, vals["temp"], (shelly_t if name=="Bureau" else None), vals["target"]))
                count += 1
        conn.commit(); cur.close(); conn.close()
        print(f"DEBUG: Enregistrement auto réussi pour {count} pièces.")
    except Exception as e: print(f"RECORD ERR: {e}")

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
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
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except: results.append(f"❌ <b>{info['name']}</b> : Erreur")
        return "\n".join(results)
####
async def brute_force_sauter(update: Update):
    """Teste toutes les méthodes d'auth pour percer le READ ERROR"""
    status_msg = await update.message.reply_text("🧪 Lancement du protocole Brute Force d'authentification...")
    
    # Liste des configurations à tester
    scenarios = [
        {"name": "Sauter App iOS", "url": "https://ha101-1.overkiz.com/externalapi/rest/login", 
         "headers": {"X-Application-Id": "cp7He8X6836936S6", "User-Agent": "Cozytouch/2.10.0 (iOS 14.4)"}},
        {"name": "Cluster Global", "url": "https://kiz-api.overkiz.com/externalapi/rest/login", 
         "headers": {"X-Application-Id": "cp7He8X6836936S6"}},
        {"name": "Standard Overkiz", "url": "https://ha101-1.overkiz.com/externalapi/rest/login", 
         "headers": {"User-Agent": "Mozilla/5.0"}},
    ]

    for sc in scenarios:
        await status_msg.edit_text(f"📡 Test : {sc['name']}...")
        try:
            async with httpx.AsyncClient(headers=sc['headers'], follow_redirects=True, timeout=15) as client:
                # On force le format x-www-form-urlencoded qui est souvent requis
                r = await client.post(sc['url'], data={"userId": BEC_EMAIL, "userPassword": BEC_PASSWORD})
                
                if r.status_code == 200:
                    await status_msg.edit_text(f"✅ SUCCÈS : {sc['name']} !\nRécupération des devices...")
                    # On continue avec cette session
                    dev_r = await client.get(sc['url'].replace('/login', '/setup/devices'))
                    if dev_r.status_code == 200:
                        devices = dev_r.json()
                        res = [f"📊 <b>{len(devices)} Appareils trouvés</b>"]
                        for d in devices:
                            res.append(f"\n📍 {d.get('label')} ({d.get('widget')})")
                            # On cherche la conso ou l'état de chauffe
                            for s in d.get('states', []):
                                if any(k in s['name'] for k in ['Energy', 'Consumption', 'Temperature', 'OperatingMode']):
                                    res.append(f" • {s['name'].split(':')[-1]}: <b>{s['value']}</b>")
                        
                        await update.message.reply_text("\n".join(res), parse_mode='HTML')
                        return
                    else:
                        await update.message.reply_text(f"⚠️ Auth OK mais erreur devices: {dev_r.status_code}")
                else:
                    print(f"DEBUG: {sc['name']} a échoué ({r.status_code})", flush=True)
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur sur {sc['name']}: {type(e).__name__}")
            
    await status_msg.edit_text("❌ Toutes les méthodes ont échoué. Vérifie tes identifiants BEC.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Activation {query.data}...")
        report = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT {query.data}</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture...")
        data, shelly_t = await get_current_data()
        lines = []
        for n, v in data.items():
            lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
            if n == "Bureau" and shelly_t:
                lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
        await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data == "REPORT":
        conn = psycopg2.connect(DB_URL); cur = conn.cursor()
        cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
        s = cur.fetchone(); cur.close(); conn.close()
        msg = f"📊 <b>BILAN 7J (Bureau)</b>\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures en base.</i>" if s and s[3]>0 else "⚠️ Pas de données."
        await query.message.reply_text(msg, parse_mode='HTML')

def get_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],[InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")]])

async def background_logger():
    while True:
        await perform_record()
        await asyncio.sleep(3600)

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Pilotage v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CommandHandler("bec", bec_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
