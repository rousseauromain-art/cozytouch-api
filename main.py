import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

VERSION = "2.2 (Retour code fonctionnel)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# Utilisation de la configuration serveur qui marchait au debut
# On ne patche plus rien manuellement pour ne pas creer de 404
try:
    MY_SERVER = Server.ATLANTIC_COZYTOUCH
except AttributeError:
    MY_SERVER = Server.FRANCE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # Retour a la methode simple d'hier
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            logger.info("Connexion reussie")
            devices = await client.get_devices()
            
            results = []
            for d in devices:
                # Listing de tous les appareils dans la console Koyeb pour debug
                logger.info(f"Appareil trouve : {d.label}")
                
                # Filtrage Oniris / Adelis
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        if target_mode in ["ABSENCE", "HOME"]:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                        
                        # Recuperation temperature (Refresh)
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
    logger.info("Commande /start")
    keyboard = [
        [InlineKeyboardButton("MAISON", callback_data="HOME")],
        [InlineKeyboardButton("ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("ETAT (LISTING)", callback_data="LIST")]
    ]
    # Texte brut sans Markdown pour eviter le crash 400
    await update.message.reply_text(
        f"CONTROLE CHAUFFAGE v{VERSION}\nChoisissez une option :", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"Connexion v{VERSION}...")
    
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"RAPPORT :\n{report}\n\nTermine.")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Demarrage v{VERSION}")
    # Securite socket pour Koyeb
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
