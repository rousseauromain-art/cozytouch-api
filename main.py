import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "3.2 (Logs Restaurés & Fix JSON)"

# --- CONFIGURATION LOGS ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BOT")

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

try:
    MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
    print("✅ LOG: Serveur Atlantic chargé")
except:
    MY_SERVER = SUPPORTED_SERVERS.get("ATLANTIC_COZYTOUCH")

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            print(f"\n>>> DÉBUT SESSION - ACTION: {target_mode} <<<")
            
            devices = await client.get_devices()
            
            # 1. Extraction des températures (on logue pour vérifier)
            temps = {}
            for d in devices:
                if "core:TemperatureState" in d.states:
                    root_id = d.device_url.split('/')[-1].split('#')[0]
                    val = d.states.get("core:TemperatureState").value
                    if val is not None:
                        temps[root_id] = val
                        print(f"DEBUG TEMP: {root_id} mesure {val}°C")

            results = []
            for d in devices:
                # Cible : Radiateurs Oniris et Sèche-serviette
                if d.widget in ["AtlanticElectricalHeaterWithAdjustableTemperatureSetpoint", "AtlanticElectricalTowelDryer"]:
                    short_id = d.device_url.split('/')[-1]
                    root_id = short_id.split('#')[0]
                    
                    status = ""
                    if target_mode in ["HOME", "ABSENCE"]:
                        try:
                            cmd_val = "away" if target_mode == "ABSENCE" else "basic"
                            print(f"TENTATIVE: {d.label} ({short_id}) -> {cmd_val}")
                            
                            # Utilisation de la structure Command pour éviter INVALID_API_CALL
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_val])
                            
                            print(f"RETOUR: Succès pour {short_id}")
                            status = " | ✅ OK"
                        except Exception as e:
                            print(f"ERREUR sur {short_id}: {e}")
                            status = " | ❌ Erreur"

                    # Construction de la ligne de rapport
                    current_temp = temps.get(root_id, "??")
                    t_str = f"{round(current_temp, 1)}°C" if isinstance(current_temp, (int, float)) else "??"
                    results.append(f"<b>{d.label}</b> ({short_id})\n└ Temp: {t_str}{status}")

            print(f">>> FIN SESSION - {len(results)} appareils traités <<<\n")
            return "\n\n".join(results)
        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
            return f"Erreur : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"--- /start reçu (PID: {os.getpid()}) ---")
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), 
                 InlineKeyboardButton("❄️ ABSENCE", callback_data="ABSENCE")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"CLIC BOUTON: {query.data}")
    await query.edit_message_text(text="⏳ Traitement en cours...")
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"<b>RAPPORT v{VERSION}</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DÉMARRAGE v{VERSION} (PID: {os.getpid()}) ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
