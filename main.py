import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "7.1 (Fix CommandName & Zero Filter)"

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
        print(f"[HEALTH] Check à {self.date_time_string()}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Healthy")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LOGIQUE D'EXTRACTION ---
async def get_detailed_listing():
    print(f"\n" + "!"*60)
    print(f"--- DÉBUT DU SCAN INTEGRAL (v{VERSION}) ---")
    print(f"!"*60)
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        print(f"[DEBUG] {len(all_devices)} entités logicielles détectées sur le compte.")
        
        # 1. FORCER LE REFRESH (Correction du nom de l'attribut)
        for d in all_devices:
            # Correction ici : command_name au lieu de name
            available_commands = [c.command_name for c in d.definition.commands]
            if "refreshTemperature" in available_commands:
                try:
                    print(f"[REFRESH] Envoi à {d.label} ({d.ui_widget})")
                    await client.execute_commands(d.device_url, [Command("refreshTemperature")])
                except Exception as e:
                    print(f"  [!] Erreur refresh sur {d.label}: {e}")
        
        await asyncio.sleep(2)

        temp_map = {}
        res_telegram = []
        
        print("\n--- LOGS BRUTS DE TOUTES LES ENTITÉS (SANS FILTRE) ---")
        for d in all_devices:
            states = {s.name: s.value for s in d.states}
            base_url = d.device_url.split('#')[0]
            sid = d.device_url.split('/')[-1]
            
            # LOG TOTAL DANS KOYEB
            print(f"ENTITÉ : {d.label} | URL: {d.device_url} | Widget: {d.ui_widget} | OID: {d.oid}")
            for s_name, s_val in states.items():
                print(f"    -> {s_name}: {s_val}")
                
                # Sauvegarde des températures pour le mapping radiateur
                if s_name == "core:TemperatureState" and s_val is not None:
                    temp_map[base_url] = s_val

            # Préparation du rapport Telegram
            if sid in DEVICE_NAMES:
                name = DEVICE_NAMES[sid]
                eff = states.get("io:EffectiveTemperatureSetpointState", "?")
                ambient = states.get("core:TemperatureState")
                if ambient is None:
                    ambient = temp_map.get(base_url, "??")
                
                rate = states.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                res_telegram.append(
                    f"<b>{name}</b> {icon}\n"
                    f"└ 🌡️ Ambiante: <b>{ambient}°C</b>\n"
                    f"└ 🎯 Consigne: <b>{eff}°C</b>"
                )
        
        print(f"!"*60)
        print("--- FIN DU SCAN ---")
        print(f"!"*60 + "\n")
        
        return "\n\n".join(res_telegram)

# --- ACTIONS & BOT ---
async def apply_heating_mode(target_mode, custom_temp=None):
    print(f"[ACTION] Mode {target_mode} demandé")
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
                    await client.execute_commands(d.device_url, [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ])
                except Exception as e:
                    print(f"  [ERREUR] {sid}: {e}")
        await asyncio.sleep(5)

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 SCAN & REFRESH TOTAL", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("🔄 Action API en cours...")
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)
    report = await get_detailed_listing()
    await m.edit_text(f"<b>RAPPORT API</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    print(f"\nDÉMARRAGE v{VERSION}")
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"v{VERSION} active", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
