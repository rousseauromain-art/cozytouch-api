import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "3.7 (Consignes & Fix Modes)"

# Configuration
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
            
            # 1. Extraction Températures ET Consignes
            stats = {}
            for d in devices:
                root_id = d.device_url.split('/')[-1].split('#')[0]
                if root_id not in stats: stats[root_id] = {"temp": "??", "target": "??"}
                
                # Température réelle
                if "core:TemperatureState" in d.states:
                    val = d.states.get("core:TemperatureState").value
                    if val is not None: stats[root_id]["temp"] = round(val, 1)
                
                # Température de consigne
                if "core:TargetTemperatureState" in d.states:
                    val = d.states.get("core:TargetTemperatureState").value
                    if val is not None: stats[root_id]["target"] = round(val, 1)

            results = []
            for d in devices:
                is_radiator = "HeaterWithAdjustableTemperatureSetpoint" in d.widget
                is_towel_dryer = "TowelDryer" in d.widget
                
                if is_radiator or is_towel_dryer:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    status = ""
                    
                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            if is_radiator:
                                cmd_name = "setOperatingMode"
                                # On tente 'away' pour l'absence (souvent le vrai nom technique du hors-gel)
                                cmd_param = "basic" if target_mode == "HOME" else "away"
                            else: # Sèche-serviette
                                cmd_name = "setTowelDryerOperatingMode"
                                # 'internal' pour reprendre le programme, 'frostprotection' pour le hors-gel
                                cmd_param = "internal" if target_mode == "HOME" else "frostprotection"

                            print(f"DEBUG: EXEC -> {d.label} : {cmd_name}({cmd_param})")
                            await client.execute_commands(d.device_url, [Command(name=cmd_name, parameters=[cmd_param])])
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"DEBUG: ERREUR {short_id}: {e}")
                            status = f" | ❌ Erreur"

                    res = stats.get(root_id)
                    results.append(
                        f"<b>{d.label}</b>\n"
                        f"└ Temp: {res['temp']}°C | Consigne: {res['target']}°C{status}"
                    )

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur : {str(e)}"

# --- FONCTIONS TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON (Auto)", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE (Hors-gel)", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Synchronisation Cozytouch...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} (PID: {os.getpid()}) ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
