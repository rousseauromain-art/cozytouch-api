import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "2.7 (Identification Totale)"

# --- CONFIGURATION LOGS ---
# On force le niveau INFO pour être sûr de voir nos messages
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("BOT_ATLANTIC") 

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")

try:
    MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]
except:
    MY_SERVER = SUPPORTED_SERVERS.get("ATLANTIC_COZYTOUCH")

async def apply_heating_mode(target_mode):
    async with OverkizClient(os.getenv("OVERKIZ_EMAIL"), os.getenv("OVERKIZ_PASSWORD"), server=MY_SERVER) as client:
        try:
            await client.login()
            devices = await client.get_devices()
            results = []
            
            print(f"\n>>> SCAN COMPLET - ACTION: {target_mode} <<<")
            
            for d in devices:
                # On affiche TOUS les appareils dans la console pour identifier le sèche-serviette
                print(f"DEBUG: Appareil détecté -> Nom: {d.label} | Modèle: {d.widget} | URL: {d.device_url}")
                
                available_cmds = [c.command_name for c in d.definition.commands]
                
                # On cible tout ce qui peut chauffer
                if "setOperatingMode" in available_cmds or "setHeatingLevel" in available_cmds:
                    try:
                        if target_mode in ["ABSENCE", "HOME"]:
                            cmd_param = "away" if target_mode == "ABSENCE" else "basic"
                            
                            # Correction de la syntaxe de commande pour éviter INVALID_API_CALL
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_param])
                            print(f"SUCCÈS: {d.label} -> {cmd_param}")
                        
                        # Lecture des infos
                        temp = d.states.get("core:TemperatureState")
                        t_val = f"{round(temp.value, 1)}C" if (temp and temp.value is not None) else "??"
                        mode = d.states.get("core:OperatingModeState")
                        m_val = mode.value if mode else "eco/confort"
                        
                        results.append(f"- {d.label}: {t_val} ({m_val})")
                    except Exception as e:
                        print(f"ERREUR sur {d.label}: {e}")
                        results.append(f"- {d.label}: Erreur")
            
            return "\n".join(results) if results else "Aucun appareil trouvé."
        except Exception as e:
            return f"Erreur connexion: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("--- Commande /start recue ---")
    keyboard = [
        [InlineKeyboardButton("🏠 MAISON (BASIC)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ ABSENCE (AWAY)", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 VERIFIER ETAT", callback_data="LIST")]
    ]
    await update.message.reply_text(
        f"PILOTAGE v{VERSION}\nSelectionnez un mode :", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    msg_wait = "Mise a jour en cours..." if query.data != "LIST" else "Lecture des sondes..."
    await query.edit_message_text(text=msg_wait)
    
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=f"RAPPORT v{VERSION} :\n{report}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE DU BOT v{VERSION} ===")
    application.run_polling(stop_signals=[signal.SIGTERM, signal.SIGINT])

if __name__ == "__main__":
    main()
