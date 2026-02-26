import os, asyncio, threading, httpx, psycopg2, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.25 (Restauration Totale Validée)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")

SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

# Dictionnaire IDs Physiques validés
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
        # --- CHAUFFAGE (RESTAURÉ AVEC CONSIGNES) ---
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

        # --- ÉTAT (RESTAURÉ - UNIQUE ET DÉTAILLÉ) ---
        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
                await client.login()
                devices = await client.get_devices()
                shelly_t = await get_shelly_temp()
                lines = []
                # On utilise un set pour éviter les doublons d'affichage si l'API renvoie plusieurs sous-devices
                processed_ids = set()
                
                for d in devices:
                    bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                    if bid in CONFORT_VALS and bid not in processed_ids:
                        st = {s.name: s.value for s in d.states}
                        t = st.get("core:TemperatureState")
                        c = st.get("io:EffectiveTemperatureSetpointState") or st.get("core:TargetTemperatureState")
                        
                        # On ne garde que les entrées qui ont une température (pour filtrer les "None")
                        if t is not None:
                            n = CONFORT_VALS[bid]["name"]
                            lines.append(f"📍 <b>{n}</b>: {t}°C (Cible: {c}°C)")
                            if n == "Bureau" and shelly_t:
                                lines.append(f"    └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
                            processed_ids.add(bid)
                            
            await query.edit_message_text("🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

        # --- STATS (RESTAURÉES AVEC CALCUL SQL) ---
        elif query.data == "REPORT":
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("""
                SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) 
                FROM temp_logs 
                WHERE room = 'Bureau' 
                AND timestamp > NOW() - INTERVAL '7 days' 
                AND temp_shelly IS NOT NULL;
            """)
            s = cur.fetchone()
            cur.close()
            conn.close()
            
            if s and s[3] > 0:
                msg = (f"📊 <b>BILAN 7J (Bureau)</b>\n"
                       f"Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                       f"<b>Δ moyen: {s[2]:+.1f}°C</b>\n"
                       f"<i>Basé sur {s[3]} mesures</i>")
            else:
                msg = "⚠️ Pas de données pour les 7 derniers jours."
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_keyboard())

        # --- BALLON (REMIS EN ATTENTE SÉCURISÉE) ---
        elif query.data == "BEC_GET":
            await query.edit_message_text("🚿 Fonction Ballon en cours de reconfiguration. Utilisez le mode Scan précédent si besoin.", reply_markup=get_keyboard())

    except Exception as e:
        log_koyeb(f"Erreur Globale: {e}")
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

if __name__ == "__main__":
    main()
    
