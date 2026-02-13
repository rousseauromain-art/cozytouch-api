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

# Sécurité pour le serveur selon la version de la librairie
try:
    SERVER = Server.FRANCE
except AttributeError:
    SERVER = Server.ATLANTIC_COZYTOUCH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        # REFRESH : Lecture des états réels
        devices = await client.get_devices()
        
        results = []
        for d in devices:
            # On cherche les radiateurs Oniris (V 2000W et H 1000W)
            cmds = [c.command_name for c in d.definition.commands]
            
            if "setOperatingMode" in cmds:
                try:
                    # RÉCUPÉRATION TEMPÉRATURE
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                    
                    # LOGIQUE DE COMMANDE
                    mode_val = "away" if target_mode == "ABSENCE" else "internal"
                    
                    # ENVOI
                    await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ Actuel: {curr_temp} | Mode: {mode_val}")
                
                except Exception as e:
                    cmd_def = next((c for c in d.definition.commands if c.command_name == "setOperatingMode"), None)
                    n_args = cmd_def.n_arg if cmd_def else "?"
                    results.append(f"❌ **{d.label}**\n   Erreur: {str(e)[:30]}\n   (Attend `{n_args}` arg)")
        
        return "\n\n".join(results) if results else "Aucun radiateur trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison 1.0", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence1.0 ", callback_data="ABSENCE")]
    ]
    await update.message.reply_text("🌡️ Gestion Oniris V & H :\n(Serveur: Atlantic)", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"⏳ Action en cours : {query.data}...")
    
    status_report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=status_report, parse_mode='Markdown')

def main():
    if not TOKEN:
        print("Erreur : TELEGRAM_TOKEN manquant.")
        return
        
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"Bot démarré sur le serveur {SERVER}")
    application.run_polling()

if __name__ == "__main__":
    main()
