import os
import asyncio
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURATION ---
# Sur Koyeb, remplis ces variables dans l'interface "Environment Variables"
OVERKIZ_EMAIL = os.getenv("OVERKIZ_USER")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD") # Ton mdp Cozytouch
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")     # Ton token de @BotFather
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        results = []
        for device in devices:
            # Traitement des Radiateurs ONIRIS
            if "setHolidays" in [c.command_name for c in device.definition.commands]:
                if target_mode == "ABSENCE":
                    # On règle à 10°C et on active le mode vacances
                    await client.execute_command(device.device_url, "setHolidaysTargetTemperature", 10.0)
                    await client.execute_command(device.device_url, "setHolidays", "holidays")
                else:
                    await client.execute_command(device.device_url, "setHolidays", "home")
                results.append(f"✅ {device.label} mis à jour")
            
            # Traitement du Sèche-serviette ADELIS
            elif "setOperatingMode" in [c.command_name for c in device.definition.commands]:
                mode = "away" if target_mode == "ABSENCE" else "internal"
                await client.execute_command(device.device_url, "setOperatingMode", mode)
                results.append(f"✅ {device.label} ({mode})")
                
        return "\n".join(results)

# --- FONCTIONS DU BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_query_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_query_data="HOME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Contrôle du chauffage Romain :", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(text=f"🔄 Application du mode {query.data}...")
    try:
        status = await apply_heating_mode(query.data)
        await query.edit_message_text(text=f"Terminé !\n{status}")
    except Exception as e:
        await query.edit_message_text(text=f"❌ Erreur : {e}")

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot démarré...")
    app.run_polling()
