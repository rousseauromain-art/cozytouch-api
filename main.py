import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.7 (Finalisation Champs)"

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

# Tes valeurs cibles pour le bouton MAISON
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
        self.wfile.write(b"Bot Active")

def run_web_server():
    HTTPServer(('0.0.0.0', 8000), KeepAliveServer).serve_forever()

async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                s = {state.name: state.value for state in d.states}
                
                # LA CONSIGNE REELLE (Celle qui compte pour la chauffe)
                eff_target = s.get("io:EffectiveTemperatureSetpointState", "?")
                # LA CIBLE THEORIQUE
                theorique = s.get("core:TargetTemperatureState", "?")
                # LE MODE ACTUEL (Confort/Eco/etc)
                level = s.get("io:TargetHeatingLevelState", "N/A")
                # HORS-GEL
                frost = s.get("core:HolidaysTargetTemperatureState", "7.0")
                
                rate = s.get("io:CurrentWorkingRateState", 0)
                icon = "♨️" if (isinstance(rate, (int, float)) and rate > 0) else "⚪"

                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ 🌡️ <b>Consigne réelle: {eff_target}°C</b>\n"
                line += f"└ 🎯 Cible théorique: {theorique}°C | Niveau: {level}\n"
                line += f"└ ❄️ Sécurité Hors-gel: {frost}°C"
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
        await asyncio.sleep(12)

# --- BOT INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON (Confort)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    msg = await query.edit_message_text("⏳ Mise à jour Cozytouch...")
    
    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=t)

    report = await get_detailed_listing()
    await msg.edit_text(f"<b>RAPPORT DE CHAUFFE</b>\n\n{report}\n\n---", parse_mode='HTML')
    await context.bot.send_message(chat_id=query.message.chat_id, text="<b>Menu :</b>", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Pilotage OK", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
