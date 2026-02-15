import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "6.2 (Verbose Debug & Health Check)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

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
        print(f"[HEALTH] Ping reçu de Koyeb à {self.date_time_string()}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Healthy")

def run_web_server():
    print(f"[SERVER] Démarrage du serveur Keep-Alive sur le port 8000...")
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LOGIQUE D'EXTRACTION ---
async def get_detailed_listing():
    print(f"\n--- DÉBUT DU SCAN COMPLET (v{VERSION}) ---")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        print(f"[DEBUG] {len(all_devices)} appareils détectés sur le compte.")
        
        # 1. On cherche les capteurs de température (Sonde ambiante)
        temp_map = {}
        for d in all_devices:
            base_url = d.device_url.split('#')[0]
            for s in d.states:
                if s.name == "core:TemperatureState":
                    print(f"  [TROUVÉ] T° Ambiante sur {d.label} ({d.device_url}): {s.value}")
                    temp_map[base_url] = s.value

        res = []
        # 2. On traite nos radiateurs cibles
        for d in all_devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                name = DEVICE_NAMES[sid]
                s = {state.name: state.value for state in d.states}
                base_url = d.device_url.split('#')[0]
                
                eff = s.get("io:EffectiveTemperatureSetpointState", "?")
                ambient = s.get("core:TemperatureState")
                
                if ambient is None:
                    ambient = temp_map.get(base_url, "??")

                rate = s.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                print(f"[SCAN] {name} -> Consigne: {eff} | Ambiante: {ambient} | Chauffe: {rate}%")
                
                line = f"<b>{name}</b> {icon}\n"
                line += f"└ 🌡️ Ambiante: <b>{ambient}°C</b>\n"
                line += f"└ 🎯 Consigne: <b>{eff}°C</b> (Effort: {rate}%)"
                res.append(line)
        
        print("--- FIN DU SCAN ---\n")
        return "\n\n".join(res)

# --- PILOTAGE ---
async def apply_heating_mode(target_mode, custom_temp=None):
    print(f"[ACTION] Passage en mode {target_mode}...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                is_towel = "Towel" in d.widget
                mode_cmd = "setTowelDryerOperatingMode" if is_towel else "setOperatingMode"
                manuel_val = "external" if is_towel else "basic"
                
                val = custom_temp if custom_temp else 16.0
                if target_mode == "HOME": val = CONFORT_TEMPS.get(sid, 19.0)
                
                try:
                    print(f"  [CMD] {DEVICE_NAMES[sid]} -> {val}°C ({target_mode})")
                    cmds = [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ]
                    await client.execute_commands(d.device_url, cmds)
                except Exception as e:
                    print(f"  [ERREUR] {sid}: {e}")
        await asyncio.sleep(8)

# --- INTERFACE BOT ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 RÉACTUALISER", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("⏳ Synchronisation en cours...")
    
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)

    report = await get_detailed_listing()
    await m.edit_text(f"<b>ÉTAT ACTUEL</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    # Affichage immédiat dans les logs au lancement
    print(f"\n" + "="*40)
    print(f"DÉMARRAGE DU BOT v{VERSION}")
    print(f"="*40)
    
    threading.Thread(target=run_web_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"Bot v{VERSION} prêt.", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("[BOT] Polling démarré...")
    app.run_polling()

if __name__ == "__main__":
    main()
