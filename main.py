import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.7.1 (Fix AttributeError States)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        
        print("\n=== SCAN DES ÉTATS (Debug Koyeb) ===")
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                print(f"\n[APPAREIL: {d.label} ({sid})]")
                
                # Correction ici : d.states est une liste d'objets State
                states_dict = {s.name: s.value for s in d.states}
                for name, val in states_dict.items():
                    print(f"  - {name}: {val}")

                nom = "Sèche-Serviette" if sid == "4326513#1" else d.label
                
                # --- RÉCUPÉRATION DES VALEURS ---
                # Température mesurée (Ambiante)
                ambient_val = states_dict.get("core:TemperatureState", "?")
                if ambient_val == "?":
                    ambient_val = states_dict.get("io:EffectiveTemperatureState", "?")
                
                # Température de consigne (Target)
                target_val = states_dict.get("core:TargetTemperatureState", "?")
                
                # Mode de fonctionnement
                op_mode = states_dict.get("core:OperatingModeState") or states_dict.get("io:TowelDryerOperatingModeState", "?")
                
                # Niveau (Eco / Confort)
                level = states_dict.get("io:TargetHeatingLevelState", "")
                level_str = "🔥 Confort" if level == "comfort" else "🌙 Eco" if level == "eco" else ""

                line = f"<b>{nom}</b>\n"
                line += f"└ Mode: {op_mode} {level_str}\n"
                line += f"└ Consigne: <b>{target_val}°C</b> | Mesuré: {ambient_val}°C"
                res.append(line)
        
        return "\n\n".join(res)

async def apply_heating_mode(target_mode, custom_temp=None):
    # Logique d'envoi conservée (v4.3 stable)
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_manuel = "basic" if "Heater" in d.widget else "external"
                val = custom_temp if custom_temp else 16.0
                if target_mode == "HOME": val = CONFORT_TEMPS[sid]
                
                try:
                    cmds = [
                        Command(name="setTargetTemperature", parameters=[val]),
                        Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else mode_manuel])
                    ]
                    await client.execute_commands(d.device_url, cmds)
                except Exception as e: print(f"Erreur {sid}: {e}")
        await asyncio.sleep(10)
        return "Action terminée."

# --- HANDLERS (Identiques) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME")],
                [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "LIST":
        await query.edit_message_text("🔍 Scan en cours...")
        report = await get_detailed_listing()
        await query.edit_message_text(f"<b>ÉTAT ACTUEL</b>\n\n{report}", parse_mode='HTML')
    else:
        await query.edit_message_text("⏳ Synchronisation...")
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data=="HOME" else "ABSENCE", custom_temp=t)
        await query.edit_message_text("✅ Fait. Vérifiez avec LIST.")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
