import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
# On n'importe plus 'Server' pour éviter les conflits d'attributs

VERSION = "1.5 (Retour aux sources - Logs Console)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# On active les logs au maximum pour voir ce qui se passe dans la console Koyeb
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # ANCIENNE MÉTHODE : On ne précise pas 'server=', 
    # ou on laisse la librairie gérer sa valeur par défaut.
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD) as client:
        try:
            await client.login()
            logger.info("Connexion réussie au serveur Overkiz")
            devices = await client.get_devices()
            
            results = []
            for d in devices:
                # Filtrage Oniris / Adelis
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        # On récupère la température
                        temp = d.states.get("core:TemperatureState")
                        val = f"{round(temp.value, 1)}°C" if temp else "??°C"
                        
                        # Choix du mode
                        cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                        
                        await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                        results.append(f"✅ {d.label} ({val}) -> {cmd_val}")
                        logger.info(f"Commande envoyée à {d.label}")
                    except Exception as e:
                        logger.error(f"Erreur sur {d.label}: {e}")
                        results.append(f"❌ {d.label} : Erreur")
            
            return "\n".join(results) if results else "Aucun radiateur trouvé."
            
        except Exception as e:
            logger.error(f"Erreur de login/connexion : {e}")
            return f"❌ Erreur de connexion : {e}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start reçue")
    keyboard = [[InlineKeyboardButton("Maison", callback_data="HOME"), 
                 InlineKeyboardButton("Absence", callback_data="ABSENCE")]]
    await update.message.reply_text(f"Bot v{VERSION}\nPrêt.", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info(f"Action demandée : {query.data}")
    
    status = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"{status}\n\n(v{VERSION})")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Démarrage Bot v{VERSION}")
    # On reste sur le polling standard
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
