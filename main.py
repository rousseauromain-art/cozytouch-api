import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS  # Import manquant corrigé !
from pyoverkiz.models import Command

VERSION = "4.5.1 (Fix Imports & Logs)"

# Configuration
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Mémoire des températures de confort
CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

async def apply_heating_mode(target_mode, custom_temp=None):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n>>> DEBUG KOYEB - ACTION: {target_mode} (Temp: {custom_temp}) <<<")
            
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_TEMPS:
                    mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    mode_manuel = "basic" if "Heater" in d.widget else "external"
                    
                    try:
                        if target_mode == "HOME":
                            val = CONFORT_TEMPS[short_id]
                            print(f"EXEC: {short_id} -> Consigne {val} then internal")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[val]),
                                Command(name=mode_cmd, parameters=["internal"])
                            ])
                        else:
                            val = custom_temp if custom_temp else 16.0
                            print(f"EXEC: {short_id} -> Consigne {val} then {mode_manuel}")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[val]),
                                Command(name=mode_cmd, parameters=[mode_manuel])
                            ])
                    except Exception as e:
                        print(f"ERREUR EXEC {short_id}: {e}")

            print("Pause de 10s pour synchronisation...")
            await asyncio.sleep(10)
            
            updated_devices = await client.get_devices()
            results = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_TEMPS:
                    current_t = "??"
                    for ud in updated_devices:
                        if ud.device_url == d.device_url:
                            st = ud.states.get("core:TargetTemperatureState")
                            current_t = st.value if st else "??"
                    results.append(f"<b>{d.label}</b>\n└ Reçu: {current_t}°C ✅")
            
            return "\n".join(results)
        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
            return f"Erreur : {e}"

# --- GESTIONNAIRES ---

async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: <code>/temp 19.5</code>", parse_mode='HTML')
        return
    try:
        val = float(context.args[0].replace(',', '.'))
        msg = await update.message.reply_text(f"⏳ Forçage à {val}°C...")
        report = await apply_heating_mode("TEMP", custom_temp=val)
        await msg.edit_text(f"<b>RAPPORT FORÇAGE ({val}°C)</b>\n\n{report}", parse_mode='HTML')
    except:
        await update.message.reply_text("❌ Erreur de valeur.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME")],
                [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>\nUtilisez <code>/temp XX</code> pour forcer une valeur.", 
                                  parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_TEMPS:
                    t = d.states.get("core:TargetTemperatureState").value
                    res.append(f"<b>{d.label}</b>: {t}°C")
            await query.edit_message_text(f"<b>ÉTAT ACTUEL</b>\n\n" + "\n".join(res), parse_mode='HTML')
    else:
        await query.edit_message_text("⏳ Synchronisation...")
        t = 16.0 if query.data == "ABS_16" else None
        m = "HOME" if query.data == "HOME" else "ABSENCE"
        report = await apply_heating_mode(m, custom_temp=t)
        await query.edit_message_text(f"<b>RAPPORT ACTION</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("temp", temp_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
