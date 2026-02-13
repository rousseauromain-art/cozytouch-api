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
        # REFRESH : On récupère les états frais des serveurs Cozytouch
        devices = await client.get_devices()
        
        results = []
        for d in devices:
            # On cherche les radiateurs Oniris/Adelis via leurs commandes disponibles
            cmds = [c.command_name for c in d.definition.commands]
            
            if "setOperatingMode" in cmds:
                try:
                    # 1. RÉCUPÉRATION TEMPÉRATURE (Refresh visuel)
                    temp_state = d.states.get("core:TemperatureState")
                    # On arrondit à 1 décimale comme sur l'écran Cozytouch
                    curr_temp = f"{round(temp_state.value, 1)}°C" if temp_state else "??°C"
                    
                    # 2. PRÉPARATION DU PAYLOAD
                    mode_val = "away" if target_mode == "ABSENCE" else "internal"
                    
                    # 3. ENVOI DE LA COMMANDE
                    # Note: On envoie une liste [valeur] car c'est le standard TaHoma pour 1 argument
                    await client.execute_command(d.device_url, "setOperatingMode", [mode_val])
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ Temp: {curr_temp} | Mode: {mode_val}")
                
                except Exception as e:
                    # Si INVALID_API_CALL, on vérifie le nombre d'arguments requis
                    cmd_def = next((c for c in d.definition.commands if c.command_name == "setOperatingMode"), None)
                    n_args = cmd_def.n_arg if cmd_def else "?"
                    results.append(f"❌ **{d.label}**\n   Erreur: Format rejeté\n   (Attend `{n_args}` argument(s))")
        
        return "\n\n".join(results) if results else "Aucun radiateur trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison 1.0 (Auto)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence 1.0 (10°C)", callback_data="ABSENCE")]
    ]
    await update.message.reply_text("🌡️ Pilotage Chauffage :\n(Données rafraîchies à chaque action)", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message d'attente pour le Refresh
    await query.edit_message_text(text=f"⏳ Synchronisation Cozytouch ({query.data})...")
    
    # Exécution et affichage du bilan complet
    status_report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=status_report, parse_mode='Markdown')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print("Bot opérationnel.")
    application.run_polling()

if __name__ == "__main__":
    main()
