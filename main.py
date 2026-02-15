import os, asyncio, threading, sys, time
import httpx
import psycopg2
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "9.3 (Instant Log & Debug+)"

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

# --- DB & API ---

def init_db():
    if not DB_URL: return
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
        print("DEBUG: [DB] Table vérifiée et prête.")
    except Exception as e: print(f"DEBUG: [DB ERR] {e}")

async def get_shelly_temp():
    if not SHELLY_TOKEN: return None
    url = f"https://{SHELLY_SERVER}/device/status"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            t = r.json()['data']['device_status']['temperature:0']['tC']
            return t
    except Exception as e:
        print(f"DEBUG: [SHELLY ERR] Impossible de lire le Shelly : {e}")
        return None

# --- LOGIQUE D'ENREGISTREMENT ---

async def perform_record(label="AUTO"):
    """La fonction unique qui fait le job d'enregistrement"""
    print(f"DEBUG: [{label}] Début du relevé des températures...")
    try:
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            count = 0
            for d in devices:
                base_url = d.device_url.split('#')[0]
                if base_url in ROOMS:
                    room = ROOMS[base_url]
                    states = {s.name: s.value for s in d.states}
                    t_rad = states.get("core:TemperatureState")
                    t_set = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                    t_sh = shelly_t if room == "Bureau" else None
                    
                    if t_rad:
                        cur.execute("INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s, %s, %s, %s)",
                                   (room, t_rad, t_sh, t_set))
                        count += 1
            
            conn.commit()
            cur.close()
            conn.close()
            print(f"DEBUG: [{label}] Succès : {count} lignes insérées en DB à {datetime.now().strftime('%H:%M:%S')}")
            return True
    except Exception as e:
        print(f"DEBUG: [{label} ERR] Échec de l'enregistrement : {e}")
        return False

async def background_logger():
    """Tâche de fond : Enregistre DE SUITE puis toutes les 60 minutes"""
    print("DEBUG: [LOGGER] Démarrage de la boucle de fond.")
    
    # 1. On attend quelques secondes que le bot soit bien lancé
    await asyncio.sleep(5) 
    
    while True:
        # 2. On effectue le relevé
        await perform_record("AUTO")
        
        # 3. On attend 1 heure
        print("DEBUG: [LOGGER] Prochain relevé auto dans 60 minutes.")
        await asyncio.sleep(3600)

# --- AFFICHAGE & TELEGRAM ---

async def get_full_status():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        results = {name: {"temp": "--", "target": "--"} for name in ROOMS.values()}
        
        for d in devices:
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room = ROOMS[base_url]
                states = {s.name: s.value for s in d.states}
                t_amb = states.get("core:TemperatureState")
                if t_amb: results[room]["temp"] = t_amb
                t_set = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                if t_set: results[room]["target"] = t_set

        # Construction du message texte
        lines = []
        for room, data in results.items():
            t_display = f"<b>{data['temp']}°C</b>" if data['temp'] != "--" else "--"
            s_display = f"<b>{data['target']}°C</b>" if data['target'] != "--" else "--"
            line = f"📍 {room}: {t_display} (Cible: {s_display})"
            if room == "Bureau" and shelly_t:
                diff = f" (Δ {shelly_t - data['temp']:+.1f}°C)" if data['temp'] != "--" else ""
                line += f"\n   └ 🌡️ Shelly: <b>{shelly_t}°C</b>{diff}"
            lines.append(line)
            
        # On profite du clic sur "Actualiser" pour enregistrer aussi un point
        await perform_record("MANUEL")
        
        return "\n".join(lines)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        status_text = await get_full_status()
        await query.edit_message_text(f"🌡️ <b>ÉTAT DU CHAUFFAGE</b>\n\n{status_text}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "REPORT":
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SELECT AVG(temp_radiateur), AVG(temp_shelly), AVG(temp_shelly - temp_radiateur), COUNT(*) FROM temp_logs WHERE room = 'Bureau' AND timestamp > NOW() - INTERVAL '7 days' AND temp_shelly IS NOT NULL;")
            stats = cur.fetchone()
            cur.close(); conn.close()
            if stats and stats[3] > 0:
                msg = (f"📊 <b>BILAN 7 JOURS (Bureau)</b>\n\n• Moy. Radiateur : {stats[0]:.1f}°C\n• Moy. Shelly GT3 : {stats[1]:.1f}°C\n• <b>Écart moyen : {stats[2]:+.1f}°C</b>\n\n<i>Basé sur {stats[3]} mesures.</i>")
            else: msg = "⚠️ Pas encore assez de données."
            await query.message.reply_text(msg, parse_mode='HTML')
        except Exception as e: print(f"DEBUG: [REPORT ERR] {e}")

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
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 Thermostat Connecté (v{VERSION})", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(background_logger())
    
    print(f"Démarrage v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
