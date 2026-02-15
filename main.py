import os, asyncio, threading, sys, time
import httpx
import psycopg2
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "8.9 (Auto-Log & Debug Max)"

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")
DB_URL = os.getenv("DATABASE_URL")

ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

# --- DATABASE & LOGGING ---

def init_db():
    if not DB_URL:
        print("DEBUG: DATABASE_URL manquante. Logs désactivés.")
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT,
                temp_radiateur FLOAT,
                temp_shelly FLOAT,
                consigne FLOAT
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("DEBUG: Connexion DB OK et Table vérifiée.")
    except Exception as e: print(f"DEBUG DB ERROR: {e}")

async def save_to_db(results, shelly_t):
    """Enregistre les données actuelles dans Postgres"""
    if not DB_URL: return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        for room, data in results.items():
            t_sh = shelly_t if room == "Bureau" else None
            if data['temp']:
                cur.execute(
                    "INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                    (room, data['temp'], t_sh, data['target'])
                )
        conn.commit()
        cur.close()
        conn.close()
        print(f"DEBUG: Point de mesure enregistré à {datetime.now().strftime('%H:%M:%S')}")
    except Exception as e: print(f"DEBUG DB SAVE ERROR: {e}")

async def background_logger():
    """Tâche de fond : enregistre toutes les 60 minutes"""
    print("DEBUG: Tâche d'enregistrement automatique démarrée (1h).")
    while True:
        await asyncio.sleep(3600)
        try:
            async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
                await client.login()
                devices = await client.get_devices()
                shelly_t = await get_shelly_temp()
                
                # Extraction rapide pour la DB
                results = {name: {"temp": None, "target": None} for name in ROOMS.values()}
                for d in devices:
                    base_url = d.device_url.split('#')[0]
                    if base_url in ROOMS:
                        states = {s.name: s.value for s in d.states}
                        results[ROOMS[base_url]]["temp"] = states.get("core:TemperatureState")
                        results[ROOMS[base_url]]["target"] = states.get("io:EffectiveTemperatureSetpointState")
                
                await save_to_db(results, shelly_t)
        except Exception as e: print(f"DEBUG BACKGROUND ERROR: {e}")

# --- API & DISPLAY ---

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    url = f"https://{SHELLY_SERVER}/device/status"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            t = r.json()['data']['device_status']['temperature:0']['tC']
            print(f"DEBUG SHELLY: {t}°C reçu de {SHELLY_SERVER}")
            return t
    except Exception as e:
        print(f"DEBUG SHELLY ERROR: {e}")
        return None

async def get_clean_listing():
    print("DEBUG: Lancement du listing manuel...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        
        results = {name: {"temp": None, "target": None} for name in ROOMS.values()}
        for d in devices:
            states = {s.name: s.value for s in d.states}
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room = ROOMS[base_url]
                print(f"DEBUG DEVICE: {room} | Temp: {states.get('core:TemperatureState')} | Target: {states.get('io:EffectiveTemperatureSetpointState')}")
                results[room]["temp"] = states.get("core:TemperatureState")
                results[room]["target"] = states.get("io:EffectiveTemperatureSetpointState")

        # Enregistre aussi lors d'un clic sur "Actualiser"
        await save_to_db(results, shelly_t)

        lines = []
        for room, data in results.items():
            t_amb = f"<b>{data['temp']}°C</b>" if data['temp'] else "--"
            t_set = f"<b>{data['target']}°C</b>" if data['target'] else "--"
            line = f"📍 {room}: {t_amb} (Cible: {t_set})"
            if room == "Bureau" and shelly_t:
                diff = f" (Δ {shelly_t - data['temp']:+.1f}°C)" if data['temp'] else ""
                line += f"\n   └ 🌡️ Shelly: <b>{shelly_t}°C</b>{diff}"
            lines.append(line)
        return "\n".join(lines)

# --- HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        txt = await get_clean_listing()
        await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{txt}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "REPORT":
        print("DEBUG: Calcul du rapport 7 jours...")
        if not DB_URL:
            await query.message.reply_text("Base de données non connectée.")
            return
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("""
                SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur) 
                FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;
            """)
            stats = cur.fetchone()
            cur.close()
            conn.close()
            if stats and stats[0]:
                msg = (f"📊 <b>BILAN 7 JOURS (Bureau)</b>\n"
                       f"• Moy. Radiateur : {stats[0]:.1f}°C\n"
                       f"• Moy. Shelly GT3 : {stats[1]:.1f}°C\n"
                       f"• <b>Écart moyen : {stats[2]:+.1f}°C</b>")
            else:
                msg = "Pas encore assez de données en base."
            await query.message.reply_text(msg, parse_mode='HTML')
        except Exception as e: print(f"DEBUG REPORT ERROR: {e}")

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")],
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("📊 RAPPORT 7J", callback_data="REPORT")]
    ])

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Thermostat Connecté", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Lancement du logger auto
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
