import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

VERSION = "1.6 (Listing & String Server)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # On définit le serveur de manière à ce qu'il soit reconnu même si l'objet bug
    # ATLANTIC_COZYTOUCH est le nom technique interne
    server_to_use = Server.ATLANTIC_COZYTOUCH
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=server_to_use) as client:
        try:
            await client.login()
            logger.info("Connecté ! Récupération de la liste...")
            devices = await client.get_devices()
            
            results = []
            for d in devices:
                # On affiche TOUT ce qu'on trouve pour débugger dans la console
                logger.info(f"Appareil trouvé : {d.label} (URL: {d.device_url})")
                
                # Filtrage Oniris / Adelis par la commande setOperatingMode
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        temp = d.states.get("core:TemperatureState")
                        val = f"{round(temp.value, 1)}°C" if temp else "??°C"
                        
                        # Mode à envoyer
                        cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                        
                        await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                        results.append(f"✅ {d.label} ({val})")
                    except Exception as e:
                        logger.error(f"Erreur commande sur {d.label}: {e}")
                        results.append(f"❌ {d.label}")
            
            return "\n".join(results) if results else "Aucun appareil compatible trouvé."
            
        except Exception as e:
            logger.error(f"Erreur globale : {e}")
            return f"Erreur : {e}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start")
    keyboard = [[InlineKeyboardButton("Maison", callback_data="HOME"), 
                 InlineKeyboardButton("Absence", callback_data="ABSENCE")]]
    await update.message.reply_text(f"Bot v{VERSION}\nPrêt pour listing.", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"⏳ v{VERSION} - Action...")
    status = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"{status}\n\n(v{VERSION})")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Démarrage v{VERSION}")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
