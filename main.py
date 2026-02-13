import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import Server

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
SERVER = Server.FRANCE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        # On récupère les appareils avec un état frais (Refresh)
        devices = await client.get_devices()
        
        results = []
        for d in devices:
            # On cible les radiateurs (Oniris, Adelis, etc.)
            cmds = [c.command_name for c in d.definition.commands]
            
            if "setOperatingMode" in cmds:
                try:
                    # 1. Lecture de la température actuelle (Refresh visuel)
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{temp_state.value}°C" if temp_state else "??°C"
                    
                    # 2. Préparation de la commande
                    mode_val = "away" if target_mode == "ABSENCE" else "internal"
                    
                    # 3. Envoi de la commande
                    # On utilise une liste car c'est le format le plus probable pour 1 argument
                    await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ Temp: {curr_temp} | Mode: {mode_val}")
                
                except Exception as e:
                    # En cas d'échec, on récupère le nombre d'arguments requis pour comprendre le format
                    cmd_def = next((c for c in d.definition.commands if c.command_name == "setOperatingMode"), None)
                    n_args = cmd_def.n_arg if cmd_def else "inconnu"
                    
                    error_detail = str(e)
                    results.append(f"❌ **{d.label}**\n   Erreur: {error_detail[:50]}\n   (Attend `{n_args}` argument(s))")
        
        return "\n\n".join(results) if results else "Aucun radiateur compatible trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence", callback_data="ABSENCE")]
    ]
    await update.message.reply_text("Pilotage Chauffage Atlantic :", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message d'attente pour que l'utilisateur sache que ça travaille
    await query.edit_message_text(text=f"⏳ Envoi de la commande {query.data}...")
    
    try:
        # Exécution et récupération du bilan (Refresh inclus)
        status_report = await apply_heating_mode(query.data)
        await query.edit_message_text(text=status_report, parse_mode='Markdown')
    except Exception as global_err:
        await query.edit_message_text(text=f"💥 Erreur critique : {global_err}")

def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN manquant")
        return

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot démarré et prêt.")
    application.run_polling()

if __name__ == "__main__":
    main()
