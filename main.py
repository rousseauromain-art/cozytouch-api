import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.0 (Absence Mode Fix)"

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
                            # Retour au mode normal / programmation
                            cmd_name = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            await client.execute_commands(d.device_url, [Command(name=cmd_name, parameters=["internal"])])
                        
                        elif target_mode == "ABSENCE":
                            # ON TESTE LA COMMANDE DÉDIÉE D'ABSENCE
                            # Sur Atlantic, 'setAbsenceMode' avec 'absence' ou 'frostprotection'
                            # Si c'est un radiateur Oniris :
                            if "Heater" in d.widget:
                                # On tente la commande de dérogation d'absence directe
                                print(f"DEBUG: Tentative setAbsenceMode sur {short_id}")
                                await client.execute_commands(d.device_url, [Command(name="setAbsenceMode", parameters=["frostprotection"])])
                            else:
                                # Pour le sèche-serviette, on force le mode standby qui est son équivalent absence sécurisé
                                # ou on tente aussi le setAbsenceMode
                                await client.execute_commands(d.device_url, [Command(name="setTowelDryerOperatingMode", parameters=["standby"])])

                        status = " | ✅ OK"
                        print(f"DEBUG: Succès sur {short_id}")
                    except Exception as e:
                        # Si setAbsenceMode échoue, on tente une dernière chance avec setDerogatedTargetTemperature
                        print(f"DEBUG: Echec commande standard, tentative alternative sur {short_id}")
                        try:
                            if target_mode == "ABSENCE":
                                # Forçage manuel à 7°C (Hors Gel universel)
                                await client.execute_commands(d.device_url, [Command(name="setTargetTemperature", parameters=[7])])
                                status = " | ✅ OK (Forcé 7°C)"
                        except:
                            status = f" | ❌ Erreur"

                    res = stats[short_id.split('#')[0]]
                    results.append(f"<b>{d.label}</b>\n└ T°: {res['temp']}°C | Consigne: {res['target']}°C{status}")

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur session : {str(e)}"

# --- INTERFACE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
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
    application.run_polling()

if __name__ == "__main__":
    main()
