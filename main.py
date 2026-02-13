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

# Correction définitive de l'erreur 'endpoint'
# On force l'utilisation de l'objet Server reconnu par pyoverkiz
try:
    SERVER = Server.FRANCE
except AttributeError:
    # Si 'FRANCE' n'est pas trouvé, on utilise la constante de secours pour Atlantic
    SERVER = Server.ATLANTIC_COZYTOUCH

logging.basicConfig(level=logging.INFO)

async def apply_heating_mode(target_mode):
    # L'astuce ici est de passer l'objet SERVER complet
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        # REFRESH : On récupère les états frais
        devices = await client.get_devices()
        
        results = []
        for d in devices:
            # On cible Oniris (V & H) et Adelis
            available_cmds = [c.command_name for c in d.definition.commands]
            
            if "setOperatingMode" in available_cmds:
                try:
                    # LECTURE TEMPÉRATURE (Refresh visuel)
                    temp_state = d.states.get("core:TemperatureState")
                    curr_temp = f"{round(temp_state.value, 1)}°C" if (temp_state and temp_state.value) else "??°C"
                    
                    if target_mode == "ABSENCE":
                        # Mode Absence (7°C ou 10°C selon ton radiateur)
                        await client.execute_command(d.device_url, "setOperatingMode", ["away"])
                        mode_label = "❄️ Absence"
                    else:
                        # Retour à la normale (Planning : 16°C ou 19/20.5°C)
                        # Tes diagnostics montrent que 'basic' est la commande de succès
                        await client.execute_command(d.device_url, "setOperatingMode", ["basic"])
                        mode_label = "🏠 Maison"
                    
                    results.append(f"✅ **{d.label}**\n   🌡️ {curr_temp} | {mode_label}")
                
                except Exception as e:
                    results.append(f"❌ **{d.label}** : Erreur commande")
        
        return "\n\n".join(results) if results else "Aucun radiateur trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ Mode Absence (Hors-gel)", callback_data="ABSENCE")]
    ]
    await update.message.reply_text("🌡️ Contrôle Chauffage :\n(Données rafraîchies à chaque clic)", 
                                  reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message d'attente
    await query.edit_message_text(text=f"⏳ Communication avec Atlantic ({query.data})...")
    
    # Exécution du refresh et de la commande
    status_report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=status_report, parse_mode='Markdown')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"Bot démarré avec succès sur {SERVER}")
    application.run_polling()

if __name__ == "__main__":
    main()
