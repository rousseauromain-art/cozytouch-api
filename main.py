import os, asyncio, threading, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "7.5 (Multi-Sondes & Debug Max)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Mapping des URLs de base pour regrouper les entités par pièce
ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Healthy")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

async def get_detailed_listing():
    print(f"\n" + "!"*60)
    print(f"--- SCAN INTEGRAL v{VERSION} ---")
    print(f"!"*60)
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        # Structure pour agréger les données
        results = {name: {"temp": None, "target": None, "window": None} for name in ROOMS.values()}
        
        print(f"\n--- LOGS BRUTS (27 ENTITÉS) ---")
        for d in all_devices:
            # DEBUG MAX : On imprime tout comme demandé
            states = {s.name: s.value for s in d.states}
            cmds = [c.command_name for c in d.definition.commands] if hasattr(d, 'definition') else []
            print(f"DEBUG: {d.label} | URL: {d.device_url} | Widget: {getattr(d, 'widget', 'N/A')}")
            print(f"  > Commandes: {cmds}")
            print(f"  > Etats: {states}")

            # LOGIQUE DE MAPPING
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room_name = ROOMS[base_url]
                
                # Récupération température (on ignore 0.0 et None)
                t_val = states.get("core:TemperatureState")
                if t_val is not None and t_val > 0:
                    results[room_name]["temp"] = t_val
                
                # Consigne effective
                if "io:EffectiveTemperatureSetpointState" in states:
                    results[room_name]["target"] = states["io:EffectiveTemperatureSetpointState"]
                
                # Fenêtre
                if "core:ContactState" in states:
                    results[room_name]["window"] = "🔴 Ouverte" if states["core:ContactState"] == "opened" else "🟢 Fermée"

        # Construction du rapport
        report_lines = []
        for room, data in results.items():
            t_amb = f"<b>{data['temp']}°C</b>" if data['temp'] else "<i>Inconnue</i>"
            t_set = f"<b>{data['target']}°C</b>" if data['target'] else "<i>--</i>"
            win = f" | Fenêtre: {data['window']}" if data['window'] else ""
            
            report_lines.append(f"📍 {room}\n└ Ambiante: {t_amb} | Consigne: {t_set}{win}")

        print(f"\n--- FIN DU SCAN ---")
        sys.stdout.flush()
        return "\n\n".join(report_lines)

async def apply_heating_mode(target_mode, custom_temp=None):
    print(f"[ACTION] Passage en mode {target_mode}")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                widget = getattr(d, 'widget', '')
                is_towel = "Towel" in widget
                mode_cmd = "setTowelDryerOperatingMode" if is_towel else "setOperatingMode"
                manuel_val = "external" if is_towel else "basic"
                
                val = custom_temp if custom_temp else 16.0
                if target_mode == "HOME":
                    val = CONFORT_TEMPS.get(sid, 19.0)
                
                try:
                    print(f"  [CMD] {sid} -> {val}°C ({target_mode})")
                    await client.execute_commands(d.device_url, [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ])
                except Exception as e:
                    print(f"  [ERREUR] {sid}: {e}")
        await asyncio.sleep(2)

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON (Confort)", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 SCAN COMPLET", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("⏳ Traitement en cours...")
    
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)
    
    report = await get_detailed_listing()
    await m.edit_text(f"<b>ÉTAT DU CHAUFFAGE</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    print(f"Démarrage v{VERSION}")
    sys.stdout.flush()
    threading.Thread(target=run_web_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"Bot v{VERSION} actif.", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
