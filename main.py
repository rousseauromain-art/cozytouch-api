import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.3 (Double Check & Radiateur Fix)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            print(f"\n--- EXECUTION v{VERSION} : {target_mode} ---")
            
            # 1. ENVOI DES COMMANDES
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_TEMPS:
                    confort_val = CONFORT_TEMPS[short_id]
                    mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    mode_manuel = "basic" if "Heater" in d.widget else "external"
                    
                    try:
                        if target_mode == "HOME":
                            print(f"ENVOI {short_id} -> {confort_val}°C puis PROG")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[confort_val]),
                                Command(name=mode_cmd, parameters=["internal"])
                            ])
                        elif target_mode == "ABSENCE":
                            print(f"ENVOI {short_id} -> 16.0°C puis MANUEL")
                            await client.execute_commands(d.device_url, [
                                Command(name="setTargetTemperature", parameters=[16.0]),
                                Command(name=mode_cmd, parameters=[mode_manuel])
                            ])
                    except Exception as e:
                        print(f"ERREUR ENVOI {short_id}: {e}")

            # 2. ATTENTE DE SYNCHRONISATION (10 secondes)
            print("Pause de 10s pour synchronisation serveur...")
            await asyncio.sleep(10)

            # 3. VERIFICATION (Jusqu'à 2 tentatives)
            results = []
            for attempt in range(2):
                print(f"Tentative de lecture #{attempt + 1}")
                updated_devices = await client.get_devices()
                results = []
                all_synced = True

                for d in devices:
                    short_id = d.device_url.split('/')[-1]
                    if short_id in CONFORT_TEMPS:
                        # Recherche de l'état actuel
                        current_target = "??"
                        for ud in updated_devices:
                            if ud.device_url == d.device_url:
                                state = ud.states.get("core:TargetTemperatureState")
                                if state: current_target = state.value
                        
                        expected = CONFORT_TEMPS[short_id] if target_mode == "HOME" else 16.0
                        
                        # Si la température lue n'est pas encore la température voulue
                        if current_target != expected:
                            all_synced = False
                        
                        results.append(f"<b>{d.label}</b>\n└ Cible: {expected}°C | Reçu: {current_target}°C")
                
                if all_synced:
                    break # On arrête si tout est OK
                if attempt == 0:
                    print("Pas encore synchronisé, nouvelle attente de 10s...")
                    await asyncio.sleep(10)

            return "\n\n".join(results)
        except Exception as e:
            return f"Erreur critique : {e}"

# --- TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⏳ Commandes envoyées. Attente de confirmation (10-20s)...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT FINAL v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
