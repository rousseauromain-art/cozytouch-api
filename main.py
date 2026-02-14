import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.2.1 (Consigne puis Prog)"

# Configuration
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
            
            results = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                
                if short_id in CONFORT_TEMPS:
                    status = ""
                    confort_val = CONFORT_TEMPS[short_id]
                    try:
                        if target_mode == "HOME":
                            # ORDRE INVERSÉ : Consigne d'abord, Programmation ensuite
                            mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                            
                            cmds = [
                                Command(name="setTargetTemperature", parameters=[confort_val]),
                                Command(name=mode_cmd, parameters=["internal"])
                            ]
                            await client.execute_commands(d.device_url, cmds)
                            status = f" | ✅ {confort_val}°C -> PROG"
                        
                        elif target_mode == "ABSENCE":
                            if "Heater" in d.widget:
                                # Mode ECO pour les Oniris (maintien du 16°C relatif)
                                await client.execute_commands(d.device_url, [Command(name="setOperatingMode", parameters=["eco"])])
                                status = " | ✅ Mode ECO"
                            else:
                                # Manuel 16°C pour le sèche-serviette
                                cmds = [
                                    Command(name="setTowelDryerOperatingMode", parameters=["external"]),
                                    Command(name="setTargetTemperature", parameters=[16.0])
                                ]
                                await client.execute_commands(d.device_url, cmds)
                                status = " | ✅ Manuel 16°C"

                    except Exception as e:
                        status = " | ❌ Erreur"

                    results.append(f"<b>{d.label}</b> ({short_id}){status}")

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur : {str(e)}"

# --- INTERFACE TELEGRAM (Identique) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON (Reset + Prog)", callback_data="HOME")],
                [InlineKeyboardButton("❄️ ABSENCE (Eco / 16°C)", callback_data="ABSENCE")],
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
