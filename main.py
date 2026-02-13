import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- GESTION DE VERSION ---
VERSION = "2.0 (No-Markdown Fix)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# Définition du serveur
try:
    MY_SERVER = Server.ATLANTIC_COZYTOUCH
except AttributeError:
    MY_SERVER = Server.FRANCE

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            logger.info("Connexion Overkiz OK")
            
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                # On liste les appareils pour la console Koyeb
                logger.info(f"Analyse appareil : {d.label}")
                
                # Cible les appareils avec la commande setOperatingMode (Oniris / Adelis)
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        # Choix du mode
                        mode_val = "away" if target_mode == "ABSENCE" else "basic"
                        
                        # Exécution
                        await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                        
                        # Récupération température
                        temp = d.states.get("core:TemperatureState")
                        t_str = f"{round(temp.value, 1)}C" if (temp and temp.value) else "??"
                        
                        results.append(f"- {d.label} ({t_str}) : {mode_val}")
                        logger.info(f"Succes pour {d.label}")
                    except Exception as e:
                        logger.error(f"Erreur commande {d.label} : {e}")
                        results.append(f"- {d.label} : ERREUR")
            
            return "\n".join(results) if results else "Aucun radiateur trouve."
        except Exception as e:
            logger.error(f"Erreur Overkiz : {e}")
            return f"Erreur connexion : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start reçue")
    keyboard = [
        [InlineKeyboardButton("MODE MAISON", callback_data="HOME")],
        [InlineKeyboardButton("MODE ABSENCE", callback_data="ABSENCE")]
    ]
    # TEXTE SIMPLE SANS MARKDOWN (pour éviter l'erreur d'offset)
    text = (
        "PILOTAGE CHAUFFAGE ATLANTIC\n"
        f"Version : {VERSION}\n"
        "--------------------------\n"
        "Choisissez une action :"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message d'attente simple
    await query.edit_message_text(text=f"Action v{VERSION} en cours...")
    
    # Execution du listing et des commandes
    report = await apply_heating_mode(query.data)
    
    # Résultat final
    final_text = f"RESULTATS :\n{report}\n\nTermine (v{VERSION})"
    await query.edit_message_text(text=final_text)

def main():
    if not TOKEN:
        logger.error("Token Telegram manquant")
        return
        
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Démarrage Bot v{VERSION}")
    # On garde les stop_signals pour éviter le double bot
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
