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
    # Correction ici : callback_data à la place de callback_query_data
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Contrôle du chauffage Romain :\n(Utilise /liste pour tester)", 
        reply_markup=reply_markup
    )

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
    """Version améliorée pour trouver le sèche-serviette"""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        listing = []
        for d in devices:
            # On ignore le bridge Cozytouch
            if d.ui_usage == "CentralControlUnit" or "pod" in d.device_url:
                continue
            # À insérer dans la boucle for d in devices
            if "Towel" in d.definition.ui_widget or "Adelis" in d.label:
                listing.append(f"🧼 SÈCHE-SERVIETTE TROUVÉ : {d.label}")
                
            cmds = [c.command_name for c in d.definition.commands] if d.definition else []
            
            if "setHolidays" in cmds:
                listing.append(f"🌡️ RADIATEUR : {d.label}")
            # On cherche tout ce qui ressemble à un sèche-serviette ou un radiateur sans holidays
            elif any("Towel" in c or "OperatingMode" in c for c in cmds):
                listing.append(f"🧼 APPAREIL DÉTECTÉ (Sèche-serviette ?) : {d.label}")
                # Optionnel : décommente la ligne suivante pour voir ses commandes dans les logs
                print(f"DEBUG: {d.label} possède les commandes: {cmds}")
                
        return "\n".join(listing) if listing else "Aucun appareil trouvé."

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste)) # Nouvelle commande
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot démarré...")
    app.run_polling() # Cette ligne maintient le bot en vie
