import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.1 (Correctif Valeurs & Keep-Alive)"

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

# On garde tes valeurs de confort exactes
CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

# --- SERVEUR WEB POUR KOYEB (Keep-Alive) ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LOGIQUE CHAUFFAGE ---
async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                states = {s.name: s.value for s in d.states}
                
                # LA VALEUR QUE TU AS TROUVÉE :
                target = states.get("io:EffectiveTemperatureSetpointState", "?")
                
                # Tentative pour la mesure (si dispo)
                ambient = states.get("core:TemperatureState") or states.get("io:EffectiveTemperatureState")
                ambient_display = f"{round(ambient, 1)}°C" if isinstance(ambient, (int, float)) else "N/A"
                
                rate = states.get("io:CurrentWorkingRateState", 0)
                icon = "♨️" if (isinstance(rate, (int, float)) and rate > 0) else "⚪"
                
                op_mode = states.get("core:OperatingModeState", "manual")
                mode_str = "AUTO" if op_mode == "internal" else "MANU"

                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ {mode_str} | Consigne: <b>{target}°C</b> (Mesuré: {ambient_display})"
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
        await asyncio.sleep(12) # On attend que le cloud Atlantic digère

# --- TELEGRAM ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON (Confort)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    msg_wait = await query.edit_message_text("⏳ Traitement en cours...")
    
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)

    report = await get_detailed_listing()
    await msg_wait.edit_text(f"<b>RAPPORT</b>\n\n{report}\n\n---", parse_mode='HTML')
    await context.bot.send_message(chat_id=query.message.chat_id, text="<b>Menu :</b>", parse_mode='HTML', reply_markup=get_keyboard())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>PILOTAGE</b>", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== RUNNING v{VERSION} ===")
    app.run_polling()

if __name__ == "__main__":
    main()
