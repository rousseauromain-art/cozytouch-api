import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "6.0 (Monitor Mode)"

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

# --- KEEP ALIVE ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Healthy")
    def log_message(self, format, *args): return

def run_web_server():
    HTTPServer(('0.0.0.0', 8000), KeepAliveServer).serve_forever()

# --- ANALYSE ---
async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        # 1. On crée un dictionnaire de toutes les températures trouvées sur le compte
        all_temperatures = {}
        for d in devices:
            # On cherche dans TOUS les appareils du compte
            for s in d.states:
                if s.name in ["core:TemperatureState", "io:MiddleWaterTemperatureState"]:
                    # On stocke la température trouvée avec une clé liée à l'URL de l'appareil
                    # Souvent le capteur a une URL proche du radiateur (ex: #2 au lieu de #1)
                    base_url = d.device_url.split('#')[0]
                    all_temperatures[base_url] = s.value

        res = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                s = {state.name: state.value for state in d.states}
                base_url = d.device_url.split('#')[0]
                
                eff = s.get("io:EffectiveTemperatureSetpointState", "?")
                
                # 2. On cherche la température ambiante 
                # Soit dans le radiateur, soit dans un capteur qui partage la même base d'URL
                ambient = s.get("core:TemperatureState") 
                if ambient is None:
                    ambient = all_temperatures.get(base_url, "Inconnue")

                rate = s.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ Consigne: <b>{eff}°C</b>\n"
                line += f"└ T° Ambiante: <b>{ambient}°C</b>\n"
                line += f"└ Activité: {rate}%"
                res.append(line)
        
        return "\n\n".join(res)

async def apply_heating_mode(target_mode, custom_temp=None):
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
                if target_mode == "HOME": val = CONFORT_TEMPS[sid]
                
                try:
                    cmds = [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ]
                    await client.execute_commands(d.device_url, cmds)
                except: pass
        await asyncio.sleep(10)

# --- BOT ---
def get_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],[InlineKeyboardButton("🔍 SCAN", callback_data="LIST")]])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("🔄 Action en cours...")
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)
    report = await get_detailed_listing()
    await m.edit_text(f"<b>STATUS</b>\n\n{report}", parse_mode='HTML', reply_markup=get_kb())

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Bot Ready", reply_markup=get_kb())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
