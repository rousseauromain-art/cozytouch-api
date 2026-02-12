import os
import asyncio
from datetime import datetime
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from pyoverkiz.models import Command
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG ---
OVERKIZ_EMAIL = os.getenv("OVERKIZ_USER")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

async def apply_heating_mode(target_mode):
    start_time = datetime.now()
    results = []
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        print(f"--- [{start_time.strftime('%H:%M:%S')}] DÉBUT DES COMMANDES ---")
        
        for device in devices:
            cmds_list = [c.command_name for c in device.definition.commands]
            dev_start = datetime.now()
            
            try:
                # 1. Gestion des Radiateurs ONIRIS
                if "setHolidays" in cmds_list:
                    if target_mode == "ABSENCE":
                        # On envoie d'abord la consigne de température, puis le mode
                        await client.execute_command(device.device_url, Command("setHolidaysTargetTemperature", [10.0]))
                        await client.execute_command(device.device_url, Command("setHolidays", ["on"]))
                        status = "❄️ 10°C"
                    else:
                        await client.execute_command(device.device_url, Command("setHolidays", ["off"]))
                        status = "🏠 Planning"
                
                # 2. Gestion du sèche-serviette ADELIS
                elif "setOperatingMode" in cmds_list:
                    mode = "away" if target_mode == "ABSENCE" else "internal"
                    await client.execute_command(device.device_url, Command("setOperatingMode", [mode]))
                    status = f"🧼 {mode}"
                
                else:
                    continue # On passe les équipements non pilotables

                elapsed = (datetime.now() - dev_start).total_seconds()
                res_msg = f"{device.label} : {status} OK ({elapsed:.1f}s)"
                print(f"[LOG] {res_msg}")
                results.append(res_msg)

            except Exception as e:
                print(f"[ERR] {device.label} a échoué : {str(e)}")
                results.append(f"❌ {device.label} : Erreur")

        total_duration = (datetime.now() - start_time).total_seconds()
        print(f"--- FIN (Durée totale: {total_duration:.1f}s) ---")
        
        return f"✅ **Terminé à {datetime.now().strftime('%H:%M:%S')}**\n⏱ Durée : {total_duration:.1f}s\n\n" + "\n".join(results)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message de chargement immédiat
    await query.edit_message_text(text="⏳ Connexion aux serveurs Atlantic en cours...")
    
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=report)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_query_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_query_data="HOME")]
    ]
    await update.message.reply_text("Commande Chauffage Romain :", reply_markup=InlineKeyboardMarkup(keyboard))

async def main():
    # 1. On configure l'application
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # 2. On nettoie les sessions (drop_pending_updates=True est crucial ici)
    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()
    
    # 3. On ajoute les handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste)) # Si tu as gardé la fonction liste
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Log: Anciennes sessions Telegram nettoyées.")
    print("Bot démarré...")
    
    # 4. On lance le polling de manière asynchrone pour ne pas bloquer la boucle
    await app.updater.start_polling()
    
    # 5. On maintient le script en vie indéfiniment
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await app.stop()

if __name__ == "__main__":
    # Plus besoin de nest_asyncio ici, on utilise la méthode standard la plus robuste
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
