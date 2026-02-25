   import os, asyncio, threading, httpx, psycopg2, time, base64, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "12.9 (Robustness Patch)"

# --- CONSTANTES GLOBALES ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "czduc0RZZXdWbjVGbVV4UmlYN1pVSUM3ZFI4YTphSDEzOXZmbzA1ZGdqeDJkSFVSQkFTbmhCRW9h"

# Configuration des pièces et températures de confort (Correction v12.9)
ROOMS_CONFIG = {
    "14253355#1": {"name": "Salon", "temp_home": 19.5},
    "1640746#1": {"name": "Chambre", "temp_home": 19.0},
    "190387#1": {"name": "Bureau", "temp_home": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp_home": 19.5}
}

# --- PERSISTENCE & CACHE ---
_magellan_token = None
_magellan_token_expiry = 0
overkiz_client = None

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- GESTIONNAIRE OVERKIZ ROBUSTE ---
async def get_overkiz_client():
    global overkiz_client
    if overkiz_client is None:
        overkiz_client = OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"])
    try:
        if not overkiz_client.authenticated:
            await overkiz_client.login()
    except Exception as e:
        log_koyeb(f"⚠️ Overkiz login échoué, reset client: {e}")
        overkiz_client = None  # Force recréation au prochain appel pour éviter client cassé
        raise RuntimeError(f"Connexion Cozytouch impossible : {e}")
    return overkiz_client

# --- MODULE MAGELLAN (AQUÉO) ---
async def get_magellan_token():
    global _magellan_token, _magellan_token_expiry
    if _magellan_token and time.time() < _magellan_token_expiry - 60:
        return _magellan_token

    headers = {
        "Authorization": f"Basic {CLIENT_BASIC}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Cozytouch/1.12.1"
    }
    payload = {"grant_type": "password", "username": BEC_USER, "password": BEC_PASS}
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ATLANTIC_API}/token", headers=headers, data=payload, timeout=12)
            if r.status_code == 200:
                data = r.json()
                _magellan_token = data["access_token"]
                _magellan_token_expiry = time.time() + data.get("expires_in", 3600)
                return _magellan_token
            return None
        except Exception as e:
            log_koyeb(f"Erreur Token Magellan: {e}")
            return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur auth Magellan"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/v1/enduserAPI/setup",
                                 headers={"Authorization": f"Bearer {token}"})
            data = r.json()
            # Correction structure imbriquée signalée
            devices = data.get('setup', {}).get('devices', data.get('devices', []))
            
            aqueo = next((d for d in devices if any(x in str(d.get('uiClass','')) + str(d.get('label','')) 
                          for x in ["HotWater", "Water", "Aqueo", "DHW"])), None)

            if not aqueo: 
                log_koyeb(f"DEBUG SETUP: {data}") # Log pour trouver les noms si ça échoue encore
                return "❓ Aquéo non trouvé"

            if action == "GET":
                states = {s['name'].split(':')[-1]: s['value'] for s in aqueo.get('states', [])}
                mode = states.get('OperatingModeState', '??')
                capa = states.get('RemainingHotWaterCapacityState', '??')
                return f"💧 Mode: {mode}\n🚿 Eau chaude: {capa}%"

            # Commandes Magellan
            cmd_name = "setAbsenceMode" if action == "ABSENCE" else "setOperatingMode"
            params = ["on"] if action == "ABSENCE" else ["manual"]
            
            payload = {
                "label": cmd_name,
                "actions": [{"deviceURL": aqueo['deviceURL'], "commands": [{"name": cmd_name, "parameters": params}]}]
            }
            res = await client.post(f"{ATLANTIC_API}/magellan/cozytouch/v1/enduserAPI/exec/apply",
                                    headers={"Authorization": f"Bearer {token}"}, json=payload)
            return "✅ Commande envoyée" if res.status_code in [200, 201] else f"❌ Erreur {res.status_code}"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT", callback_data="LIST"), InlineKeyboardButton("📊 STATS", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET"), InlineKeyboardButton("⚙️ DEBUG", callback_data="BEC_DEBUG")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Application {query.data}...")
            client = await get_overkiz_client()
            devices = await client.get_devices()
            res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in ROOMS_CONFIG:
                    conf = ROOMS_CONFIG[sid]
                    t_val = conf["temp_home"] if query.data == "HOME" else 16.0
                    mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                    try:
                        cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                        await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd, [mode])])
                        res.append(f"✅ {conf['name']}")
                    except: res.append(f"❌ {conf['name']}")
            await query.edit_message_text(f"<b>RÉSULTAT:</b>\n" + "\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            client = await get_overkiz_client()
            devices = await client.get_devices()
            lines = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in ROOMS_CONFIG:
                    s = {st.name: st.value for st in d.states}
                    t = s.get("core:TemperatureState") or s.get("io:TargetTemperatureState") or "??"
                    c = s.get("core:TargetTemperatureState") or "??"
                    lines.append(f"📍 <b>{ROOMS_CONFIG[sid]['name']}</b>: {t}°C (Cible: {c}°C)")
            await query.edit_message_text("🌡️ <b>ÉTAT</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data == "REPORT":
            try:
                conn = psycopg2.connect(DB_URL); cur = conn.cursor()
                cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days';")
                s = cur.fetchone(); cur.close(); conn.close()
                msg = f"📊 <b>BILAN 7J</b>\nMesures: {s[2]}\nRad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C" if s and s[2] > 0 else "Pas de données."
            except Exception as e: 
                log_koyeb(f"Erreur SQL REPORT: {e}")
                msg = "⚠️ Erreur SQL"
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_keyboard())

        elif query.data.startswith("BEC_"):
            act = query.data.replace("BEC_", "")
            if act == "DEBUG":
                token = await get_magellan_token()
                async with httpx.AsyncClient() as c:
                    r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/v1/enduserAPI/setup", headers={"Authorization": f"Bearer {token}"})
                    log_koyeb(f"FULL SETUP: {r.text}")
                await query.edit_message_text("📋 JSON complet envoyé dans les logs Koyeb.", reply_markup=get_keyboard())
            else:
                await query.edit_message_text(f"⏳ Ballon {act}...")
                res = await manage_bec(act)
                await query.edit_message_text(f"<b>BALLON:</b>\n{res}", parse_mode='HTML', reply_markup=get_keyboard())

    except RuntimeError as e:
        await query.edit_message_text(f"❌ {str(e)}", reply_markup=get_keyboard())
    except Exception as e:
        log_koyeb(f"Erreur Handler: {e}")
        await query.edit_message_text("⚠️ Une erreur est survenue.", reply_markup=get_keyboard())

# --- MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"DÉMARRAGE v{VERSION}")
    app.run_polling()

if __name__ == "__main__":
    main()
                  
