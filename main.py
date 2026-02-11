import os
import asyncio
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG ---
OVERKIZ_EMAIL = "rousseau.romain@gmail.com"
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

async def get_devices_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        listing = []
        for d in devices:
            cmds = [c.command_name for c in d.definition.commands] if d.definition else []
            # On identifie le type pour le test
            if "setHolidays" in cmds:
                listing.append(f"🌡️ RADIATEUR : {d.label}")
            elif "setTowelDryerOperatingMode" in cmds or "setOperatingMode" in cmds:
                if "pod" not in d.device_url:
                    listing.append(f"🧼 SÈCHE-SERVIETTE : {d.label}")
        
        return "\n".join(listing) if listing else "Aucun équipement pilotable trouvé."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Romain prêt !\nUtilise /liste pour voir tes équipements ou les boutons ci-dessous :",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❄️ Mode Absence", callback_query_data="ABSENCE")],
            [InlineKeyboardButton("🏠 Mode Maison", callback_query_data="HOME")]
        ])
    )

async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Recherche des équipements...")
    res = await get_devices_listing()
    await update.message.reply_text(f"Équipements détectés :\n\n{res}")

# Garde le reste du code (button_handler, etc.) identique au précédent
