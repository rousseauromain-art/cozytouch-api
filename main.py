import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "3.6 (Debug Logs & Frost Protection)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n>>> DEBUG - DEBUT SESSION - ACTION: {target_mode} <<<")
            
            # 1. Extraction des températures (Listing debug complet)
            temps = {}
            for d in devices:
                if "core:TemperatureState" in d.states:
                    root_id = d.device_url.split('/')[-1].split('#')[0]
                    val = d.states.get("core:TemperatureState").value
                    if val is not None: 
                        temps[root_id] = val
                        print(f"DEBUG: Temp trouvée pour {root_id} ({d.label}) : {val}°C")

            results = []
            for d in devices:
                # Filtrage des types d'appareils
                is_radiator = "HeaterWithAdjustableTemperatureSetpoint" in d.widget
                is_towel_dryer = "TowelDryer" in d.widget
                
                if is_radiator or is_towel_dryer:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    status = ""
                    
                    print(f"DEBUG: Analyse appareil {d.label} (ID: {short_id}, Widget: {d.widget})")

                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            # --- LOGIQUE DE COMMANDE ---
                            if is_radiator:
                                cmd_name = "setOperatingMode"
                                # 'basic' pour Home, 'frostprotection' pour Absence (Oniris)
                                cmd_param = "basic" if target_mode == "HOME" else "frostprotection"
                            else: # Sèche-serviette
                                cmd_name = "setTowelDryerOperatingMode"
                                # 'external' pour Home, 'standby' pour Absence
                                cmd_param = "external" if target_mode == "HOME" else "standby"

                            print(f"DEBUG: EXECUTION -> {d.label} : {cmd_name}({cmd_param})")
                            command = Command(name=cmd_name, parameters=[cmd_param])
                            await client.execute_commands(d.device_url, [command])
                            
                            print(f"DEBUG: RESULTAT -> Succès sur {short_id}")
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"DEBUG: ERREUR sur {short_id}: {str(e)}")
                            status = f" | ❌ Erreur ({str(e)[:20]}...)"

                    # Construction du message Telegram
                    current_temp = temps.get(root_id, "??")
                    t_str = f"{round(current_temp, 1)}°C" if isinstance(current_temp, (int, float)) else "??"
                    results.append(f"<b>{d.label}</b> ({short_id})\n└ Temp: {t_str}{status}")

            print(f">>> DEBUG - FIN SESSION <<<\n")
            return "\n\n".join(results)
        except Exception as e:
            print(f"DEBUG: ERREUR CRITIQUE SESSION: {str(e)}")
            return f"Erreur critique : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"DEBUG: Commande /start reçue de {update.effective_user.first_name}")
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"),
                InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"DEBUG: Clic bouton -> {query.data}")
    await query.edit_message_text(text="⏳ Action en cours...")
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
