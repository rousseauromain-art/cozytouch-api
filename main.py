import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- CONFIGURATION SÉCURISÉE ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# Correction de l'erreur 'endpoint' : on s'assure d'utiliser l'objet Server correct
# Pour Atlantic en France, c'est Server.FRANCE ou Server.ATLANTIC_COZYTOUCH
try:
    SERVER = Server.FRANCE
except AttributeError:
    SERVER = Server.ATLANTIC_COZYTOUCH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    # Utilisation explicite de l'objet server pour éviter l'AttributeError endpoint
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
        except Exception as auth_err:
            return f"❌ Erreur connexion Overkiz : {auth_err}"
        
        results = []
        for d in devices:
            # On cible les radiateurs et sèche-serviettes
            cmds = [c.command_name for c in d.definition.commands]
            
            if "setOperatingMode" in cmds:
                try:
                    # REFRESH : Lecture température ambiante
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                    
                    if target_mode == "ABSENCE":
                        # Passage en Hors-gel (7°C par défaut sur Oniris)
                        await client.execute_command(d.device_url, "setOperatingMode", ["away"])
                        mode_label = "❄️ Absence"
                    else:
                        # Retour au planning (Confort 19/20.5 - Éco 16)
                        # On utilise 'basic' comme vu dans tes logs réussis
                        await client.execute_command(d.device_url, "setOperatingMode", ["basic"])
                        mode_label = "🏠 Maison"
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ {curr_temp} | {mode_label}")
                
                except Exception as e:
                    results.append(f"❌ **{d.label}** : {str(e)[:30]}")
        
        return "\n\n".join(results) if results else "Aucun appareil trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence (7°C)", callback_data="ABSENCE")]
    ]
    await update.message.reply_text("🌡️ Pilotage Atlantic Oniris & Adelis :", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(text=f"⏳ Communication avec le serveur {SERVER.name}...")
    
    status_report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=status_report, parse_mode='Markdown')

def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN manquant")
        return
        
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Bot démarré avec succès sur {SERVER.name}")
    application.run_polling()

if __name__ == "__main__":
    main()
