import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.2.3 (Affichage Consignes & Logs)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Mémoire des températures de confort
CONFORT_TEMPS = {
    "14253355#1": 19.5,  # Salon
    "1640746#1": 19.0,   # Chambre
    "190387#1": 19.0,    # Bureau
    "4326513#1": 19.5    # Sèche-serviette
}

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n--- DEBUG KOYEB - ACTION: {target_mode} ---")
            
            results = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                
                if short_id in CONFORT_TEMPS:
                    confort_val = CONFORT_TEMPS[short_id]
                    status_msg = ""
                    try:
                        if target_mode == "HOME":
                            mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            print(f"ACTION: {short_id} -> Consigne {confort_val}°C + Mode PROG")
                            
                            cmds = [
                                Command(name="setTargetTemperature", parameters=[confort_val]),
                                Command(name=mode_cmd, parameters=["internal"])
                            ]
                            await client.execute_commands(d.device_url, cmds)
                            status_msg = f"Restauré à {confort_val}°C (Prog)"
                        
                        elif target_mode == "ABSENCE":
                            if "Heater" in d.widget:
                                print(f"ACTION: {short_id} -> Passage Mode ECO")
                                await client.execute_commands(d.device_url, [Command(name="setOperatingMode", parameters=["eco"])])
                                status_msg = "Passé en ECO (16°C rel.)"
                            else:
                                print(f"ACTION: {short_id} -> Manuel 16.0°C")
                                cmds = [
                                    Command(name="setTowelDryerOperatingMode", parameters=["external"]),
                                    Command(name="setTargetTemperature", parameters=[16.0])
                                ]
                                await client.execute_commands(d.device_url, cmds)
                                status_msg = "Passé à 16.0°C (Manuel)"
                        
                        # Petite attente pour laisser le serveur se mettre à jour avant de relire l'état
                        await asyncio.sleep(1)
                        
                        # Récupération de la nouvelle consigne pour confirmation
                        updated_devices = await client.get_devices()
                        new_target = "??"
                        for ud in updated_devices:
                            if ud.device_url == d.device_url:
                                state = ud.states.get("core:TargetTemperatureState")
                                if state: new_target = state.value
                        
                        results.append(f"<b>{d.label}</b>\n└ {status_msg} | Reçu: {new_target}°C")

                    except Exception as e:
                        print(f"ERREUR {short_id}: {e}")
                        results.append(f"<b>{d.label}</b>\n└ ❌ Erreur : {str(e)[:30]}")

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur session : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Synchronisation avec Cozytouch...")
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
