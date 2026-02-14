import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command # Import pour formater proprement les commandes

VERSION = "3.1 (Fix JSON & Temps)"

# Configuration
TOKEN = os.getenv("TELEGRAM_TOKEN")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

async def apply_heating_mode(target_mode):
    async with OverkizClient(os.getenv("OVERKIZ_EMAIL"), os.getenv("OVERKIZ_PASSWORD"), server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            
            # 1. On crée un dictionnaire des températures par "racine" d'ID (ex: 14253355)
            temps = {}
            for d in devices:
                if "TemperatureState" in d.states:
                    root_id = d.device_url.split('/')[-1].split('#')[0]
                    val = d.states.get("core:TemperatureState").value
                    if val: temps[root_id] = val

            results = []
            print(f"\n>>> ACTION: {target_mode} <<<")
            
            for d in devices:
                # On cible les contrôleurs (#1)
                if d.widget in ["AtlanticElectricalHeaterWithAdjustableTemperatureSetpoint", "AtlanticElectricalTowelDryer"]:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    
                    status = ""
                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            cmd_name = "setOperatingMode"
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            
                            # NOUVELLE SYNTAXE : On utilise l'objet Command pour un JSON parfait
                            command = Command(name=cmd_name, parameters=[cmd_val])
                            await client.execute_command(d.device_url, command.name, command.parameters)
                            
                            print(f"✅ {d.label} ({short_id}) -> {cmd_val}")
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"❌ Erreur {short_id}: {e}")
                            status = " | ❌ Erreur"

                    # Récupération de la température depuis notre dictionnaire
                    current_temp = temps.get(root_id, "??")
                    t_str = f"{round(current_temp, 1)}°C" if isinstance(current_temp, (int, float)) else "??"
                    
                    results.append(f"<b>{d.label}</b> ({short_id})\n└ Temp: {t_str}{status}")
            
            return "\n\n".join(results) if results else "Aucun appareil trouvé."
        except Exception as e:
            return f"Erreur de connexion : {str(e)}"

# --- LE RESTE DU CODE (Start / Button) RESTE IDENTIQUE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Traitement...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
