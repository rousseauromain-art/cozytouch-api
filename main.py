import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- GESTION DE VERSION ---
VERSION = "2.1 (Listing & Server Patch)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PATCH SERVEUR ---
# On prépare un objet serveur "propre" pour éviter l'AttributeError
try:
    MY_SERVER = Server.ATLANTIC_COZYTOUCH
except AttributeError:
    MY_SERVER = Server.FRANCE

# Si l'objet est buggé (pas d'endpoint), on le reconstruit manuellement
if not hasattr(MY_SERVER, "endpoint"):
    logger.info("Patching du serveur Atlantic...")
    class PatchedServer:
        name = "ATLANTIC_COZYTOUCH"
        label = "Atlantic Cozytouch"
        endpoint = "https://ha110-1.overkiz.com/enduser-mobile-web/enduserapi"
    MY_SERVER = PatchedServer()

async def get_heating_report(action="LIST"):
    """Récupère l'état ou applique un mode"""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                # On cible Oniris et Adelis
                if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                    try:
                        # 1. Action si demandée
                        if action in ["ABSENCE", "HOME"]:
                            target = "away" if action == "ABSENCE" else "basic"
                            await client.execute_command(d.device_url, "setOperatingMode", [target])
                        
                        # 2. Lecture état (Refresh)
                        temp = d.states.get("core:TemperatureState")
                        t_val = f"{round(temp.value, 1)}C" if (temp and temp.value) else "??"
                        
                        mode = d.states.get("core:OperatingModeState")
                        m_val = mode.value if mode else "inconnu"
                        
                        results.append(f"- {d.label}: {t_val} (Mode: {m_val})")
                    except Exception as e:
                        results.append(f"- {d.label}: Erreur")
            
            return "\n".join(results) if results else "Aucun appareil trouve."
        except Exception as e:
            logger.error(f"Erreur Overkiz: {e}")
            return f"Erreur connexion: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start")
    keyboard = [
        [InlineKeyboardButton("🏠 MODE MAISON", callback_data="HOME")],
        [InlineKeyboardButton("❄️ MODE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ETAT ACTUEL", callback_data="LIST")]
    ]
    text = f"PILOTAGE ATLANTIC v{VERSION}\n\nChoisissez une action :"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(text=f"⏳ v{VERSION} - Connexion en cours...")
    
    report = await get_heating_report(query.data)
    
    final_text = f"RAPPORT :\n{report}\n\nTermine (v{VERSION})"
    await query.edit_message_text(text=final_text)

def main():
    if not TOKEN:
        return
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Demarrage v{VERSION}")
    # On garde les signaux pour éviter le double bot
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
