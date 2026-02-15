import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "7.0 (Full Scan & Temperature Refresh)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

# On garde le dictionnaire pour l'affichage Telegram, mais les logs montreront TOUT
DEVICE_NAMES = {
    "14253355#1": "Salon",
    "1640746#1": "Chambre",
    "190387#1": "Bureau",
    "4326513#1": "Sèche-Serviette"
}

CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

# --- SERVEUR ANTI-INTERRUPTION KOYEB ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        print(f"[HEALTH] Ping Koyeb reçu à {self.date_time_string()}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Healthy")

def run_web_server():
    print(f"[SERVER] Port 8000 actif.")
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LOGIQUE D'EXTRACTION ---
async def get_detailed_listing():
    print(f"\n" + "!"*50)
    print(f"--- DÉBUT DU SCAN INTEGRAL (v{VERSION}) ---")
    print(f"!"*50)
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        print(f"[DEBUG] Nombre total d'entités trouvées : {len(all_devices)}")
        
        # 1. FORCER LE REFRESH SUR TOUS LES APPAREILS COMPATIBLES
        print("[REFRESH] Envoi de la commande 'refreshTemperature'...")
        for d in all_devices:
            if "refreshTemperature" in [cmd.name for cmd in d.definition.commands]:
                try:
                    print(f"  -> Refresh sur : {d.label} ({d.ui_widget})")
                    await client.execute_commands(d.device_url, [Command("refreshTemperature")])
                except Exception as e:
                    print(f"  [ERREUR REFRESH] {d.label}: {e}")
        
        # Petite pause pour laisser le temps aux commandes de remonter
        await asyncio.sleep(2)

        # 2. LOG DE TOUS LES APPAREILS SANS EXCEPTION
        temp_map = {}
        res_telegram = []
        
        print("\n--- LISTE DE TOUS LES ÉQUIPEMENTS DÉTECTÉS ---")
        for d in all_devices:
            states = {s.name: s.value for s in d.states}
            base_url = d.device_url.split('#')[0]
            sid = d.device_url.split('/')[-1]
            
            # On log absolument tout dans la console Koyeb
            print(f"Device: {d.label} | URL: {d.device_url} | Widget: {d.ui_widget}")
            for s_name, s_val in states.items():
                if "Temperature" in s_name or "Mode" in s_name or "Rate" in s_name:
                    print(f"   └ {s_name}: {s_val}")
                
                # Capture des températures pour la correspondance
                if s_name == "core:TemperatureState" and s_val is not None:
                    temp_map[base_url] = s_val

            # Préparation du rapport Telegram (pour tes radiateurs principaux)
            if sid in DEVICE_NAMES:
                name = DEVICE_NAMES[sid]
                eff = states.get("io:EffectiveTemperatureSetpointState", "?")
                ambient = states.get("core:TemperatureState")
                if ambient is None:
                    ambient = temp_map.get(base_url, "??")
                
                rate = states.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                line = f"<b>{name}</b> {icon}\n"
                line += f"└ 🌡️ Ambiante: <b>{ambient}°C</b>\n"
                line += f"└ 🎯 Consigne: <b>{eff}°C</b> (Effort: {rate}%)"
                res_telegram.append(line)
        
        print("\n" + "!"*50)
        print("--- FIN DU SCAN INTEGRAL ---")
        print("!"*50 + "\n")
        
        return "\n\n".join(res_telegram)

# --- PILOTAGE ---
async def apply_heating_mode(target_mode, custom_temp=None):
    print(f"[ACTION] Passage en mode {target_mode}")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                is_towel = "Towel" in d.ui_widget
                mode_cmd = "setTowelDryerOperatingMode" if is_towel else "setOperatingMode"
                manuel_val = "external" if is_towel else "basic"
                val = custom_temp if custom_temp else 16.0
                if target_mode == "HOME": val = CONFORT_TEMPS.get(sid, 19.0)
                
                try:
                    print(f"  [CMD] {DEVICE_NAMES[sid]} -> {val}°C")
                    cmds = [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ]
                    await client.execute_commands(d.device_url, cmds)
                except Exception as e:
                    print(f"  [ERREUR CMD] {sid}: {e}")
        await asyncio.sleep(5)

# --- INTERFACE BOT ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 SCAN TOTAL + REFRESH", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("🔄 Synchronisation & Refresh API...")
    
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)

    report = await get_detailed_listing()
    await m.edit_text(f"<b>ÉTAT RÉEL (API)</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    print(f"\n{'='*40}\nDEMARRAGE BOT v{VERSION}\n{'='*40}")
    threading.Thread(target=run_web_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"Bot v{VERSION} prêt.", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("[BOT] Polling démarré avec drop_pending_updates=True")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
