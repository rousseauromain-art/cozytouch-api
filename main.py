import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.2 (Scanner Complet + Keep-Alive)"

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

# --- SERVEUR WEB POUR KOYEB ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    print("--- SERVEUR WEB KEEP-ALIVE DEMARRE SUR PORT 8000 ---")
    server.serve_forever()

# --- LOGIQUE DE SCAN ET COMMANDE ---
async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        
        print("\n" + "="*50)
        print("DEBUG LOGS COMPLETS - " + VERSION)
        print("="*50)

        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                print(f"\n[SCAN APPAREIL: {DEVICE_NAMES[sid]} ({sid})]")
                states_dict = {s.name: s.value for s in d.states}
                
                # ON LOG TOUT SANS EXCEPTION
                for name, val in states_dict.items():
                    print(f"  - {name}: {val}")

                # Extraction des valeurs clés
                target = states_dict.get("io:EffectiveTemperatureSetpointState", "?")
                ambient = states_dict.get("core:TemperatureState") or states_dict.get("io:EffectiveTemperatureState")
                ambient_display = f"{round(ambient, 1)}°C" if isinstance(ambient, (int, float)) else "N/A"
                
                rate = states_dict.get("io:CurrentWorkingRateState", 0)
                icon = "♨️" if (isinstance(rate, (int, float)) and rate > 0) else "⚪"
                
                op_mode = states_dict.get("core:OperatingModeState", "manual")
                level = states_dict.get("io:TargetHeatingLevelState", "")
                
                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ Mode: {op_mode} ({level})\n"
                line += f"└ Consigne: <b>{target}°C</b> | Mesuré: {ambient_display}"
                res.append(line)
        
        print("\n" + "="*50 + "\n")
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
                
                print(f"COMMANDE: {DEVICE_NAMES[sid]} -> {val}°C (Mode: {target_mode})")
                
                try:
                    cmds = [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                    ]
                    await client.execute_commands(d.device_url, cmds)
                except Exception as e:
                    print(f"ERREUR EXECUTION {sid}: {e}")
        
        await asyncio.sleep(12)

# --- INTERFACE TELEGRAM ---
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON (Confort)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=get_main_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("⏳ Action en cours + Scan complet dans les logs...")

    if query.data != "LIST":
        temp = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABSENCE", custom_temp=temp)

    report = await get_detailed_listing()
    
    await query.edit_message_text(f"<b>ÉTAT DES RADIATEURS</b>\n\n{report}\n\n---", parse_mode='HTML')
    await context.bot.send_message(chat_id=query.message.chat_id, text="<b>Menu de contrôle :</b>", parse_mode='HTML', reply_markup=get_main_keyboard())

def main():
    # Thread Keep-Alive pour éviter la mise en veille Koyeb
    threading.Thread(target=run_web_server, daemon=True).start()
    
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"=== BOT DEMARRE (v{VERSION}) ===")
    application.run_polling()

if __name__ == "__main__":
    main()
