import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.3 (Full API Dump)"

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

# --- KEEP ALIVE KOYEB ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- ANALYSEUR DE CHAMPS ---
async def dump_all_states():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        print("\n" + "!"*60)
        print("!!! DUMP COMPLET DES DONNÉES API !!!")
        print("!"*60)

        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                print(f"\n[APPAREIL: {DEVICE_NAMES[sid]} | URL: {d.device_url}]")
                print(f"Widget: {d.widget} | UI: {d.ui_widget}")
                
                # On trie par nom pour s'y retrouver
                sorted_states = sorted(d.states, key=lambda s: s.name)
                for state in sorted_states:
                    # On affiche le nom et la valeur brute
                    print(f"  > {state.name}: {state.value}")
        
        print("\n" + "!"*60 + "\n")

# --- LOGIQUE PRINCIPALE ---
async def get_detailed_listing():
    # On lance le dump dans les logs à chaque fois qu'on demande le listing
    await dump_all_states()
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                s = {state.name: state.value for state in d.states}
                
                # On garde tes champs identifiés
                target = s.get("io:EffectiveTemperatureSetpointState", "?")
                
                # Tentative large pour la mesure en attendant ton retour sur les logs
                ambient = s.get("core:TemperatureState") or \
                          s.get("io:TargetHeatingLevelState") or \
                          s.get("core:LuminanceState") # Parfois détourné ?
                
                ambient_display = f"{round(ambient, 1)}°C" if isinstance(ambient, (int, float)) else "???"
                rate = s.get("io:CurrentWorkingRateState", 0)
                icon = "♨️" if (isinstance(rate, (int, float)) and rate > 0) else "⚪"
                
                line = f"<b>{DEVICE_NAMES[sid]}</b> {icon}\n"
                line += f"└ Consigne: <b>{target}°C</b> | Mesuré: {ambient_display}"
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
    await query.edit_message_text("⏳ Scan en cours (Regarde les logs Koyeb)...")

    if query.data != "LIST":
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABSENCE", custom_temp=t)

    report = await get_detailed_listing()
    await query.edit_message_text(f"<b>ÉTAT DES RADIATEURS</b>\n\n{report}\n\n---", parse_mode='HTML')
    await context.bot.send_message(chat_id=query.message.chat_id, text="<b>Menu :</b>", parse_mode='HTML', reply_markup=get_main_keyboard())

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== READY v{VERSION} ===")
    app.run_polling()

if __name__ == "__main__":
    main()
