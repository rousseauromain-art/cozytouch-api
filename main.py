import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "2.9 (IDs & Command Fix)"

# Configuration
TOKEN = os.getenv("TELEGRAM_TOKEN")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

async def apply_heating_mode(target_mode):
    async with OverkizClient(os.getenv("OVERKIZ_EMAIL"), os.getenv("OVERKIZ_PASSWORD"), server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            results = []
            
            print(f"\n>>> ACTION: {target_mode} <<<")
            
            for d in devices:
                # Filtrage : Radiateurs (AdjustableSetpoint) et Sèche-serviette (TowelDryer)
                if d.widget in ["AtlanticElectricalHeaterWithAdjustableTemperatureSetpoint", "AtlanticElectricalTowelDryer"]:
                    
                    # Extraction simplifiée de l'ID (les 4 derniers chiffres de l'URL)
                    short_id = d.device_url.split('/')[-1]
                    
                    if target_mode != "LIST":
                        try:
                            # Mapping des commandes selon le log HA : parameters=['basic'] ou ['away']
                            cmd_param = "away" if target_mode == "ABSENCE" else "basic"
                            
                            # TEST SYNTAXE : On envoie la commande telle qu'attendue par l'API
                            print(f"Tentative sur {d.label} ({short_id}) -> {cmd_param}")
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_param])
                            status = "✅ OK"
                        except Exception as e:
                            print(f"Erreur sur {short_id}: {e}")
                            status = "❌ Erreur JSON"
                    else:
                        status = "Info"

                    # Lecture état
                    temp = d.states.get("core:TemperatureState")
                    t_val = f"{round(temp.value, 1)}C" if (temp and temp.value is not None) else "??"
                    
                    # On ajoute l'ID dans le message Telegram
                    results.append(f"ID:{short_id} | {d.label}\n   └ Temp: {t_val} | {status}")
            
            return "\n".join(results) if results else "Aucun appareil trouvé."
        except Exception as e:
            return f"Erreur de connexion : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 LISTING COMPLET", callback_data="LIST")]]
    await update.message.reply_text(f"CONTROLE v{VERSION}", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Traitement en cours...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"RAPPORT v{VERSION} :\n\n{report}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
