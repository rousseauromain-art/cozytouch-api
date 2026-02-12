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

import asyncio
from pyoverkiz.client import OverkizClient
from pyoverkiz.models import Command

# ... (tes constantes OVERKIZ_EMAIL, etc.)

async def apply_heating_mode(target_mode):
    """Bascule les radiateurs entre le planning interne et le mode Absence global."""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        results = []
        for device in devices:
            # On ne cible que les radiateurs (IO Heating System)
            if device.ui_model == "HeatingSystem":
                cmds = [c.command_name for c in device.definition.commands]
                
                if target_mode == "ABSENCE":
                    # 1. On règle la température Hors-Gel (souvent 7-10°C)
                    if "setHolidaysTargetTemperature" in cmds:
                        await client.execute_command(device.device_url, Command("setHolidaysTargetTemperature", [16.0]))
                    
                    # 2. IMPORTANT : On bascule le mode de fonctionnement sur 'away'
                    # C'est ce qui active le bandeau Absence dans l'app Cozytouch
                    if "setOperatingMode" in cmds:
                        await client.execute_command(device.device_url, Command("setOperatingMode", ["away"]))
                    
                    results.append(f"❄️ {device.label} : Mode ABSENCE (16°C) activé")

                else:
                    # Retour au mode normal (Planning interne)
                    if "setOperatingMode" in cmds:
                        await client.execute_command(device.device_url, Command("setOperatingMode", ["internal"]))
                    
                    results.append(f"🏠 {device.label} : Retour au mode PLANNING")
        
        # On lance un refresh automatique après les commandes
        await refresh_cozytouch_states()
        
        return "\n".join(results)

async def refresh_cozytouch_states():
    """Force l'actualisation des données entre les serveurs et les radiateurs."""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        for device in devices:
            if "refreshStates" in [c.command_name for c in device.definition.commands]:
                try:
                    await client.execute_command(device.device_url, Command("refreshStates"))
                except Exception:
                    continue # Certains appareils sont parfois occupés
        return "🔄 Actualisation demandée aux radiateurs."
        
async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Recherche de tes équipements...")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        msg = "Équipements détectés :\n"
        for d in devices:
            msg += f"\n📍 {d.label} ({d.widget})"
        await update.message.reply_text(msg)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Message de chargement immédiat
    await query.edit_message_text(text="⏳ Connexion aux serveurs Atlantic en cours...")
    
    report = await apply_heating_mode(query.data)
    await query.edit_message_text(text=report)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (16°C)", callback_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")]
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
