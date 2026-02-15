import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.6 (Listing Style CozyTouch)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

CONFORT_TEMPS = {
    "14253355#1": 19.5, # Salon
    "1640746#1": 19.0,  # Chambre
    "190387#1": 19.0,   # Bureau
    "4326513#1": 19.5   # Sèche-serviette
}

async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                # 1. Nom personnalisé
                nom = "Sèche-Serviette" if sid == "4326513#1" else d.label
                
                # 2. Températures (Consigne vs Ambiante)
                target = d.states.get("core:TargetTemperatureState").value if "core:TargetTemperatureState" in d.states else "?"
                ambient = d.states.get("core:TemperatureState").value if "core:TemperatureState" in d.states else "?"
                
                # 3. Modes (Prog vs Manuel)
                op_mode = d.states.get("core:OperatingModeState") or d.states.get("io:TowelDryerOperatingModeState")
                mode_label = op_mode.value if op_mode else "Inconnu"
                
                # 4. Niveau de chauffe (Eco vs Confort) - Spécifique à la programmation
                heating_level = d.states.get("io:TargetHeatingLevelState")
                level_label = ""
                if heating_level:
                    level_val = heating_level.value
                    if level_val == "comfort": level_label = "🔥 Confort"
                    elif level_val == "eco": level_label = "🌙 Eco"
                    else: level_label = level_val.capitalize()

                # Mise en forme
                mode_display = "AUTO (Prog)" if mode_label == "internal" else "MANUEL"
                line = f"<b>{nom}</b>\n"
                line += f"└ État: {mode_display} {level_label}\n"
                line += f"└ Consigne: <b>{target}°C</b> | Mesuré: {ambient}°C"
                res.append(line)
                
        return "\n\n".join(res)

async def apply_heating_mode(target_mode, custom_temp=None):
    # La logique d'envoi reste strictement la même (v4.3 fonctionnelle)
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        print(f"\n>>> ACTION: {target_mode} (Temp: {custom_temp}) <<<")
        
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_manuel = "basic" if "Heater" in d.widget else "external"
                try:
                    if target_mode == "HOME":
                        val = CONFORT_TEMPS[sid]
                        await client.execute_commands(d.device_url, [
                            Command(name="setTargetTemperature", parameters=[val]),
                            Command(name=mode_cmd, parameters=["internal"])
                        ])
                    else:
                        val = custom_temp if custom_temp else 16.0
                        await client.execute_commands(d.device_url, [
                            Command(name="setTargetTemperature", parameters=[val]),
                            Command(name=mode_cmd, parameters=[mode_manuel])
                        ])
                except Exception as e: print(f"Erreur {sid}: {e}")

        await asyncio.sleep(10)
        return "Commandes appliquées. Vérifiez avec le bouton LIST."

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON (Prog)", callback_data="HOME")],
                [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    val = float(context.args[0].replace(',', '.'))
    msg = await update.message.reply_text(f"⏳ Forçage à {val}°C...")
    await apply_heating_mode("TEMP", custom_temp=val)
    await msg.edit_text(f"✅ Terminé. Utilisez /start pour voir le résultat.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        await query.edit_message_text("🔍 Récupération des données...")
        report = await get_detailed_listing()
        await query.edit_message_text(f"<b>ÉTAT DES RADIATEURS</b>\n\n{report}", parse_mode='HTML')
    else:
        await query.edit_message_text("⏳ Synchronisation...")
        t = 16.0 if query.data == "ABS_16" else None
        m = "HOME" if query.data == "HOME" else "ABSENCE"
        await apply_heating_mode(m, custom_temp=t)
        await query.edit_message_text(f"✅ Action terminée. Refaites un LIST dans quelques secondes.")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("temp", temp_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.run_polling()

if __name__ == "__main__":
    main()
