import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.1 (Manuel 16°C / Retour Prog)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n>>> DEBUG - ACTION: {target_mode} <<<")
            
            # Récupération des températures pour le rapport
            stats = {}
            for d in devices:
                root_id = d.device_url.split('/')[-1].split('#')[0]
                if root_id not in stats: stats[root_id] = {"temp": "??", "target": "??"}
                if "core:TemperatureState" in d.states:
                    val = d.states.get("core:TemperatureState").value
                    if val is not None: stats[root_id]["temp"] = round(val, 1)
                if "core:TargetTemperatureState" in d.states:
                    val = d.states.get("core:TargetTemperatureState").value
                    if val is not None: stats[root_id]["target"] = round(val, 1)

            results = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                is_target = (short_id == "190387#1" or "TowelDryer" in d.widget)
                
                if is_target:
                    status = ""
                    try:
                        if target_mode == "HOME":
                            # REPRISE DE LA PROGRAMMATION
                            print(f"DEBUG: Retour PROG sur {short_id}")
                            cmd_name = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            await client.execute_commands(d.device_url, [Command(name=cmd_name, parameters=["internal"])])
                            status = " | ✅ PROG REPRISE"
                        
                        elif target_mode == "ABSENCE":
                            # PASSAGE EN MANUEL (BASIC/EXTERNAL) + CONSIGNE 16°C
                            print(f"DEBUG: Passage Manuel 16°C sur {short_id}")
                            
                            # 1. On change le mode pour autoriser la main sur la température
                            mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            mode_param = "basic" if "Heater" in d.widget else "external"
                            
                            # 2. On envoie les deux commandes : Mode Manuel PUIS Température 16
                            # Envoyer les deux dans la même liste garantit la prise en compte par Atlantic
                            commands = [
                                Command(name=mode_cmd, parameters=[mode_param]),
                                Command(name="setTargetTemperature", parameters=[16.0])
                            ]
                            await client.execute_commands(d.device_url, commands)
                            status = " | ✅ MANUEL 16°C"

                    except Exception as e:
                        print(f"DEBUG: Erreur sur {short_id}: {e}")
                        status = f" | ❌ Erreur"
                    
                    res = stats.get(short_id.split('#')[0])
                    results.append(f"<b>{d.label}</b>\n└ T°: {res['temp']} | Consigne: {res['target']}{status}")

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur session : {str(e)}"

# --- INTERFACE TELEGRAM (Identique) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON (Prog)", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
