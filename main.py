import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- GESTION DE VERSION ---
VERSION = "1.7 (Listing Stable & Graceful)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# Configuration du serveur Atlantic
try:
    MY_SERVER = Server.FRANCE
except AttributeError:
    MY_SERVER = Server.ATLANTIC_COZYTOUCH

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # Utilisation de MY_SERVER configuré plus haut
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            logger.info("✅ Connexion Overkiz réussie")
            
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                # Log pour la console Koyeb
                logger.info(f"Appareil détecté : {d.label}")
                
                # Cible : Oniris et Adelis (possédant setOperatingMode)
                cmds = [c.command_name for c in d.definition.commands]
                if "setOperatingMode" in cmds:
                    try:
                        # 1. Lecture de la température (REFRESH)
                        temp_state = d.states.get("core:TemperatureState")
                        curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                        
                        # 2. Choix du mode selon tes diagnostics HA
                        mode_val = "away" if target_mode == "ABSENCE" else "basic"
                        
                        # 3. Envoi de la commande
                        await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                        
                        results.append(f"✅ **{d.label}**\n   🌡️ {curr_temp} | Mode: {mode_val}")
                        logger.info(f"👍 Commande envoyée avec succès à {d.label}")
                        
                    except Exception as cmd_err:
                        logger.error(f"❌ Erreur sur {d.label}: {cmd_err}")
                        results.append(f"❌ **{d.label}** : Erreur")
            
            return "\n\n".join(results) if results else "Aucun radiateur compatible trouvé."
            
        except Exception as auth_err:
            logger.error(f"💥 Erreur globale : {auth_err}")
            return f"❌ Erreur de connexion : {auth_err}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start reçue")
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence", callback_data="ABSENCE")]
    ]
    await update.message.reply_text(
        f"🌡️ **Bot v{VERSION}**\nServeur : {MY_SERVER.name}\n\nChoisissez un mode :", 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message de transition
    await query.edit_message_text(text=f"⏳ v{VERSION} - Action en cours...")
    
    status_report = await apply_heating_mode(query.data)
    
    # Affichage final
    await query.edit_message_text(
        text=status_report + f"\n\n_Bot v{VERSION}_", 
        parse_mode='Markdown'
    )

def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN manquant")
        return
        
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info(f"Démarrage Bot v{VERSION} sur {MY_SERVER.name}")
    
    # Utilisation du stop_signals pour éviter les doubles bots
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
