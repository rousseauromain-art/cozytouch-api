import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

VERSION = "2.4 (Syntaxe Crochets [])"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ACCÈS PAR DICTIONNAIRE ---
# On essaie de récupérer le serveur comme une clé de dictionnaire
try:
    # C'est probablement cette syntaxe qui avait fonctionné !
    MY_SERVER = Server["ATLANTIC_COZYTOUCH"]
except Exception:
    # Repli au cas où
    MY_SERVER = Server.FRANCE

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                # Listing console pour voir ce qui se passe
                logger.info(f"Appareil trouve : {d.label}")
                
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        if target_mode in ["ABSENCE", "HOME"]:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                        
                        # Refresh température
                        temp = d.states.get("core:TemperatureState")
                        t_val = f"{round(temp.value, 1)}C" if temp else "??"
                        results.append(f"- {d.label} ({t_val})")
                    except Exception as e:
                        logger.error(f"Erreur commande sur {d.label} : {e}")
                        results.append(f"- {d.label} : Erreur")
            
            return "\n".join(results) if results else "Aucun appareil trouvé."
        except Exception as e:
            logger.error(f"Erreur Overkiz : {e}")
            return f"Erreur connexion : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start reçue")
    keyboard = [
        [InlineKeyboardButton("MAISON", callback_data="HOME")],
        [InlineKeyboardButton("ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("ETAT ACTUEL", callback_data="LIST")]
    ]
    # Texte brut (pas de Markdown)
    await update.message.reply_text(
        f"PILOTAGE v{VERSION}\nServeur detecte via []\n\nChoisir :", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"Action v{VERSION}...")
    
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"RESULTAT :\n{report}\n\n(v{VERSION})")

def main():
    if not TOKEN:
        return
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Démarrage v{VERSION}")
    # Signal pour éviter le double bot
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
