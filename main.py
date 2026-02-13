import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server
from pyoverkiz.models import Command

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
SERVER = Server.FRANCE  # Pour Atlantic / Cozytouch

# Configuration des logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    """Applique le mode de chauffage aux radiateurs Oniris/Adelis."""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        
        for d in devices:
            # On récupère les commandes disponibles pour cet appareil
            cmds = [c.command_name for c in d.definition.commands]
            
            # On filtre pour ne garder que les radiateurs compatibles
            if "setOperatingMode" in cmds:
                try:
                    if target_mode == "ABSENCE":
                        # 1. On règle la température hors-gel d'abord (évite l'erreur 'no value')
                        if "setHolidaysTargetTemperature" in cmds:
                            await client.execute_command(d.device_url, "setHolidaysTargetTemperature", [10.0])
                        
                        # 2. On active le mode absence 'away' (confirmé par ton YAML HA)
                        await client.execute_command(d.device_url, "setOperatingMode", ["away"])
                        results.append(f"✅ {d.label} : ❄️ Absence (10°C)")
                    
                    else:
                        # Retour au mode Planning (Interne)
                        await client.execute_command(d.device_url, "setOperatingMode", ["internal"])
                        results.append(f"🏠 {d.label} : 📅 Planning (Auto)")
                
                except Exception as e:
                    logger.error(f"Erreur sur {d.label}: {e}")
                    results.append(f"❌ {d.label} : Erreur format")
        
        return "\n".join(results) if results else "Aucun appareil compatible trouvé."

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
