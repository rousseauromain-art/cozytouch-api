import os
import asyncio
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURATION (Inchangée) ---
OVERKIZ_EMAIL = os.getenv("OVERKIZ_USER")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

# --- FONCTIONS COZYTOUCH (Inchangées) ---
async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for device in devices:
            if "setHolidays" in [c.command_name for c in device.definition.commands]:
                if target_mode == "ABSENCE":
                    await client.execute_command(device.device_url, "setHolidaysTargetTemperature", 10.0)
                    await client.execute_command(device.device_url, "setHolidays", "holidays")
                else:
                    await client.execute_command(device.device_url, "setHolidays", "home")
                results.append(f"✅ {device.label} mis à jour")
            elif "setOperatingMode" in [c.command_name for c in device.definition.commands]:
                mode = "away" if target_mode == "ABSENCE" else "internal"
                await client.execute_command(device.device_url, "setOperatingMode", mode)
                results.append(f"✅ {device.label} ({mode})")
        return "\n".join(results)

# --- GESTION DU BOT (Inchangée) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_query_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_query_data="HOME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Contrôle du chauffage Romain :\n(Utilise /liste pour tester)", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"🔄 Application du mode {query.data}...")
    try:
        status = await apply_heating_mode(query.data)
        await query.edit_message_text(text=f"Terminé !\n{status}")
    except Exception as e:
        await query.edit_message_text(text=f"❌ Erreur : {e}")

# ==========================================================
# NOUVELLES FONCTIONS (Ajoutées à la fin pour le suivi)
# ==========================================================

async def get_devices_listing():
    """Fonction de test pour lister les équipements sans action"""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        listing = []
        for d in devices:
            cmds = [c.command_name for c in d.definition.commands] if d.definition else []
            if "setHolidays" in cmds:
                listing.append(f"🌡️ RADIATEUR : {d.label}")
            elif "setOperatingMode" in cmds and "pod" not in d.device_url:
                listing.append(f"🧼 SÈCHE-SERVIETTE : {d.label}")
        return "\n".join(listing) if listing else "Aucun appareil trouvé."

async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /liste pour tester la visibilité des équipements"""
    await update.message.reply_text("🔍 Recherche de tes équipements...")
    try:
        res = await get_devices_listing()
        await update.message.reply_text(f"Équipements détectés :\n\n{res}")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur listing : {e}")

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste)) # Nouvelle commande
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot démarré...")
    app.run_polling() # Cette ligne maintient le bot en vie
