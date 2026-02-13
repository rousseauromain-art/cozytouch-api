import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- GESTION DE VERSION ---
VERSION = "1.4 (Manual Endpoint Fix)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

# MÉTHODE RADICALE : On définit l'objet Server manuellement 
# pour éviter l'erreur "AttributeError: 'Server' object has no attribute 'endpoint'"
# On prend les valeurs exactes du serveur France (Atlantic/Cozytouch)
MY_SERVER = Server.ATLANTIC_COZYTOUCH
# On s'assure manuellement que l'attribut manquant est présent si besoin
if not hasattr(MY_SERVER, 'endpoint'):
    # On force l'URL pour la France
    MY_SERVER.endpoint = "https://ha110-1.overkiz.com/enduser-mobile-web/enduserapi"

logging.basicConfig(level=logging.INFO)

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        
        for d in devices:
            if "setOperatingMode" in [c.command_name for c in d.definition.commands]:
                try:
                    # Refresh Température
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                    
                    # Logique Modes (vu dans ton diag HA)
                    mode_val = "away" if target_mode == "ABSENCE" else "basic"
                    
                    await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                    results.append(f"✅ **{d.label}**\n   🌡️ {curr_temp} | Mode: {mode_val}")
                except Exception as e:
                    results.append(f"❌ **{d.label}** : Erreur commande")
        
        return "\n\n".join(results) if results else "Aucun radiateur trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 Maison (Planning)", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ Absence (Hors-gel)", callback_data="ABSENCE")]]
    await update.message.reply_text(
        f"🌡️ **Bot v{VERSION}**\nChoisissez un mode :", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"⏳ v{VERSION} - Connexion Atlantic...")
    
    try:
        status_report = await apply_heating_mode(query.data)
        await query.edit_message_text(text=status_report + f"\n\n_v{VERSION}_", parse_mode='Markdown')
    except Exception as e:
        await query.edit_message_text(text=f"❌ Erreur critique v{VERSION}:\n{str(e)}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"Démarrage v{VERSION} - Endpoint forcé")
    # Graceful shutdown pour aider Koyeb à libérer le token
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
