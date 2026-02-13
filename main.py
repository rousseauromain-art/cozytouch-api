import os
import asyncio
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
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        
        for d in devices:
            # On vérifie si c'est un appareil de chauffage
            cmds = [c.command_name for c in d.definition.commands]
            if "setOperatingMode" not in cmds and "setHolidays" not in cmds:
                continue

            try:
                if target_mode == "ABSENCE":
                    # SYNTAXE DIRECTE : pas d'objet Command, paramètres dans une LISTE []
                    if "setOperatingMode" in cmds:
                        await client.execute_command(d.device_url, "setOperatingMode", ["away"])
                    
                    if "setHolidaysTargetTemperature" in cmds:
                        await client.execute_command(d.device_url, "setHolidaysTargetTemperature", [10.0])
                    
                    results.append(f"✅ {d.label} -> ABSENCE")
                
                else:
                    if "setOperatingMode" in cmds:
                        await client.execute_command(d.device_url, "setOperatingMode", ["internal"])
                    
                    results.append(f"🏠 {d.label} -> MAISON")
            
            except Exception as e:
                results.append(f"❌ {d.label} erreur: {str(e)[:50]}")
                
        return "\n".join(results) if results else "Aucun radiateur détecté."

async def refresh_logic():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for device in devices:
            if "refreshStates" in [c.command_name for c in device.definition.commands]:
                try:
                    await client.execute_command(device.device_url, Command("refreshStates"))
                except: continue
        return "🔄 Actualisation demandée aux radiateurs."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❄️ Absence (10°C)", callback_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Maison (Planning)", callback_data="HOME")],
        [InlineKeyboardButton("🔄 Rafraîchir l'état", callback_data="REFRESH")]
    ]
    await update.message.reply_text("Pilotage Cozytouch :", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "REFRESH":
        msg = await refresh_logic()
    else:
        msg = await apply_heating_mode(data)
    
    await query.edit_message_text(text=f"Résultat :\n{msg}\n\nQue voulez-vous faire ensuite ?", 
                                  reply_markup=query.message.reply_markup)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("refresh", lambda u, c: u.message.reply_text("🔄 Refresh lancé..."))) # Optionnel
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot démarré...")
    app.run_polling()

if __name__ == "__main__":
    main()
