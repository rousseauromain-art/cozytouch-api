import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- GESTION DE VERSION ---
VERSION = "1.2 (Universal Server Fix)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# Utilisation d'un dictionnaire pour forcer l'objet Server correct
# Cette méthode évite l'erreur 'endpoint' sur les nouvelles versions
try:
    SERVER = Server.FRANCE
except AttributeError:
    # Backup pour Atlantic/Cozytouch France
    SERVER = Server.ATLANTIC_COZYTOUCH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # On passe l'objet SERVER complet à l'initialisation
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        results = []
        for d in devices:
            # On cible Oniris (V & H) et Adelis
            if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                try:
                    # REFRESH TEMPÉRATURE
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                    
                    # LOGIQUE MODES
                    mode_val = "away" if target_mode == "ABSENCE" else "basic"
                    await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ {curr_temp} | Mode: {mode_val}")
                except Exception as e:
                    results.append(f"❌ **{d.label}** : Erreur")
        
        return "\n\n".join(results) if results else "Aucun radiateur trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence", callback_data="ABSENCE")]
    ]
    # La version apparaît ici au démarrage
    await update.message.reply_text(
        f"🌡️ **Contrôle Chauffage Atlantic**\n"
        f"Scripts : `v{VERSION}`\n"
        f"Serveur : `{SERVER.name}`\n\n"
        f"Choisissez un mode :",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # On rappelle la version dans le message de chargement pour être sûr
    await query.edit_message_text(text=f"⏳ v{VERSION} - Synchro Atlantic...")
    
    status_report = await apply_heating_mode(query.data)
    
    # Message final avec résultats et rappel version
    footer = f"\n\n_Bot v{VERSION}_"
    await query.edit_message_text(text=status_report + footer, parse_mode='Markdown')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"Démarrage Bot v{VERSION} sur {SERVER.name}")
    application.run_polling()

if __name__ == "__main__":
    main()
