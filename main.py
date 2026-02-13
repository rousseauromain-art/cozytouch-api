import os
import asyncio
import logging
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "2.6 (Logs Visibles & Command Fix)"

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
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        try:
            await client.login()
            # LOG VISIBLE DANS KOYEB
            print(f"\n>>> CONNEXION REUSSIE - ACTION: {target_mode} <<<")
            
            devices = await client.get_devices()
            results = []
            
            for d in devices:
                # On cherche les radiateurs (Oniris, Adelis)
                available_cmds = [c.command_name for c in d.definition.commands]
                
                if "setOperatingMode" in available_cmds:
                    # LOG POUR CHAQUE RADIATEUR
                    print(f"--- Tentative sur : {d.label} ---")
                    
                    try:
                        if target_mode in ["ABSENCE", "HOME"]:
                            # 'away' pour absence, 'basic' pour maison (vu dans tes logs HA)
                            cmd_param = "away" if target_mode == "ABSENCE" else "basic"
                            
                            await client.execute_command(d.device_url, "setOperatingMode", [cmd_param])
                            print(f"SUCCES: {d.label} passe en {cmd_param}")
                        
                        # Lecture état
                        temp = d.states.get("core:TemperatureState")
                        t_val = f"{round(temp.value, 1)}C" if (temp and temp.value is not None) else "??"
                        
                        # Récupération du mode actuel pour confirmation
                        curr_mode = d.states.get("core:OperatingModeState")
                        m_val = curr_mode.value if curr_mode else "non lu"
                        
                        results.append(f"- {d.label}: {t_val} (Mode: {m_val})")
                    except Exception as e:
                        print(f"ERREUR sur {d.label}: {e}")
                        results.append(f"- {d.label}: Erreur commande")
            
            return "\n".join(results) if results else "Aucun appareil trouvé."
        except Exception as e:
            print(f"ERREUR CRITIQUE: {e}")
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
