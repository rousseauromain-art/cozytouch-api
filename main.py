import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
# C'est probablement cet import et ces crochets dont tu te souviens :
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "2.5 (Supported Servers Fix)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Sélection du serveur via le dictionnaire des serveurs supportés
# C'est ici qu'on utilise les [] sur le dictionnaire global
try:
    MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
    logger.info("Serveur Atlantic chargé via SUPPORTED_SERVERS")
except KeyError:
    # Si la clé minuscule ne marche pas, on tente la majuscule
    MY_SERVER = SUPPORTED_SERVERS.get("ATLANTIC_COZYTOUCH")

async def apply_heating_mode(target_mode):
    # On passe l'entrée du dictionnaire qui contient BIEN l'attribut endpoint
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        if target_mode in ["ABSENCE", "HOME"]:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                        
                        temp = d.states.get("core:TemperatureState")
                        t_val = f"{round(temp.value, 1)}C" if temp else "??"
                        results.append(f"- {d.label} ({t_val})")
                    except Exception as e:
                        results.append(f"- {d.label} : Erreur")
            
            return "\n".join(results) if results else "Aucun appareil trouve."
        except Exception as e:
            logger.error(f"Erreur Overkiz : {e}")
            return f"Erreur : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("MAISON", callback_data="HOME")],
        [InlineKeyboardButton("ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("LISTING", callback_data="LIST")]
    ]
    await update.message.reply_text(
        f"CHAUFFAGE v{VERSION}\n(Mode: SUPPORTED_SERVERS)\n\nAction :", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Connexion...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"RAPPORT :\n{report}\n\n(v{VERSION})")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    logger.info(f"Démarrage v{VERSION}")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
