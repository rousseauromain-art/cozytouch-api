import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "3.3 (Final Protocol Fix)"

# --- CONFIGURATION LOGS ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BOT")

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            print(f"\n>>> SESSION - ACTION: {target_mode} <<<")
            
            devices = await client.get_devices()
            
            # Récupération des températures (OK en v3.2)
            temps = {}
            for d in devices:
                if "core:TemperatureState" in d.states:
                    root_id = d.device_url.split('/')[-1].split('#')[0]
                    val = d.states.get("core:TemperatureState").value
                    if val is not None: temps[root_id] = val

            results = []
            for d in devices:
                if d.widget in ["AtlanticElectricalHeaterWithAdjustableTemperatureSetpoint", "AtlanticElectricalTowelDryer"]:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    
                    status = ""
                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            print(f"ENVOI: {d.label} ({short_id}) -> {cmd_val}")
                            
                            # --- LE CORRECTIF CI-DESSOUS EST CRITIQUE ---
                            # On crée l'objet Command proprement
                            command_to_send = Command(name="setOperatingMode", parameters=[cmd_val])
                            
                            # On utilise execute_commands (au pluriel) avec une liste d'objets
                            # C'est ce format que le serveur attend pour ne pas faire de "Invalid JSON"
                            await client.execute_commands(d.device_url, [command_to_send])
                            
                            print(f"RETOUR: Succès {short_id}")
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"ERREUR sur {short_id}: {e}")
                            status = " | ❌ Erreur"

                    current_temp = temps.get(root_id, "??")
                    t_str = f"{round(current_temp, 1)}°C" if isinstance(current_temp, (int, float)) else "??"
                    results.append(f"<b>{d.label}</b> ({short_id})\n└ Temp: {t_str}{status}")

            return "\n\n".join(results)
        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
            return f"Erreur : {str(e)}"

# --- LES FONCTIONS TELEGRAM RESTENT IDENTIQUES ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Traitement en cours...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} (PID: {os.getpid()}) ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
