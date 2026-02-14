import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "3.9 (Fix Hors-Gel Protocole)"

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
                
                # Température réelle
                if "core:TemperatureState" in d.states:
                    val = d.states.get("core:TemperatureState").value
                    if val is not None: stats[root_id]["temp"] = round(val, 1)
                
                # Température de CONSIGNE (Target)
                if "core:TargetTemperatureState" in d.states:
                    val = d.states.get("core:TargetTemperatureState").value
                    if val is not None: stats[root_id]["target"] = round(val, 1)

            results = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                root_id = short_id.split('#')[0]
                
                # On ne touche qu'au radiateur 190387#1 et au sèche-serviette
                is_target = (short_id == "190387#1" or "TowelDryer" in d.widget)
                
                if is_target:
                    status = ""
                    try:
                        if target_mode == "HOME":
                            # Retour au planning interne (Mode Programmation)
                            cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            await client.execute_commands(d.device_url, [Command(name=cmd, parameters=["internal"])])
                        
                        elif target_mode == "ABSENCE":
                            # Pour le Hors-Gel "Prog", on utilise setAbsenceMode si dispo, sinon away
                            if "Heater" in d.widget:
                                # Tentative sur le radiateur cible
                                await client.execute_commands(d.device_url, [Command(name="setOperatingMode", parameters=["away"])])
                            else:
                                # Pour le sèche-serviette, 'auto' semble être la clé du mode Prog Hors-gel
                                await client.execute_commands(d.device_url, [Command(name="setTowelDryerOperatingMode", parameters=["auto"])])
                        
                        status = " | ✅ OK"
                        print(f"DEBUG: Succès sur {short_id}")
                    except Exception as e:
                        print(f"DEBUG: Erreur sur {short_id}: {e}")
                        status = " | ❌ Erreur"
                    
                    res = stats.get(root_id)
                    results.append(f"<b>{d.label}</b>\n└ T°: {res['temp']}°C | Consigne: {res['target']}°C{status}")

            return "\n\n".join(results) or "Aucun appareil compatible trouvé."
        except Exception as e:
            return f"Erreur session : {str(e)}"

# --- INTERFACE TELEGRAM (Identique) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON (Prog)", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE (Hors-gel)", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Commande en cours...")
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
