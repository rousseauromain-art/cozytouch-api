import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server, SUPPORTED_SERVERS
from pyoverkiz.models import Command

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

# Configuration des logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        diag_msg = ""
        for d in devices:
            if "Radiateur" in d.label:
                # On cherche la définition de la commande dans le dictionnaire de l'appareil
                cmd_def = next((c for c in d.definition.commands if c.command_name == "setOperatingMode"), None)
                
                # On récupère aussi la température actuelle pour préparer le futur refresh
                temp_state = d.states.get("core:TemperatureState")
                current_temp = temp_state.value if temp_state else "Inconnue"

                diag_msg += f"📡 **{d.label}**\n"
                diag_msg += f"Temp actuelle: {current_temp}°C\n"
                if cmd_def:
                    diag_msg += f"Format attendu: `{cmd_def.parameters}`\n\n"
        
        return diag_msg if diag_msg else "Aucun radiateur trouvé."

# --- COMMANDES TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les boutons de contrôle."""
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_data="ABSENCE")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Contrôle du chauffage Atlantic :", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les clics sur les boutons."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    await query.edit_message_text(text=f"⏳ Application du mode {data}...")
    
    # Exécution de la commande Overkiz
    status_message = await apply_heating_mode(data)
    
    # Mise à jour avec le résultat final
    await query.edit_message_text(text=status_message)

def main():
    """Lance le bot."""
    if not TOKEN:
        print("Erreur : TELEGRAM_TOKEN manquant.")
        return

    print("Bot démarré...")
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    application.run_polling()

if __name__ == "__main__":
    main()
