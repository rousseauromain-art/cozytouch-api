import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "4.7 (Scanner d'états Deep Debug) "

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
        
        print("\n=== SCAN COMPLET DES ÉTATS (Koyeb Debug) ===")
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                print(f"\n[APPAREIL: {d.label} ({sid})]")
                # On print ABSOLUMENT TOUT dans Koyeb pour trouver les bonnes clés
                for state_name, state_obj in d.states.items():
                    print(f"  - {state_name}: {state_obj.value}")

                nom = "Sèche-Serviette" if sid == "4326513#1" else d.label
                
                # Tentative de récupération multi-clés pour la température mesurée
                ambient = d.states.get("core:TemperatureState") or \
                          d.states.get("io:EffectiveTemperatureState") or \
                          d.states.get("core:LuminanceState") # Parfois détourné
                ambient_val = round(ambient.value, 1) if (ambient and ambient.value is not None) else "?"

                # Tentative pour la consigne
                target = d.states.get("core:TargetTemperatureState") or \
                         d.states.get("core:EcoHeatingTargetTemperatureState")
                target_val = target.value if (target and target.value is not None) else "?"

                # Mode et Niveau
                op_mode = d.states.get("core:OperatingModeState") or d.states.get("io:TowelDryerOperatingModeState")
                heating_level = d.states.get("io:TargetHeatingLevelState")
                
                level_str = ""
                if heating_level:
                    level_str = "🔥 Confort" if heating_level.value == "comfort" else "🌙 Eco"

                line = f"<b>{nom}</b>\n"
                line += f"└ Mode: {op_mode.value if op_mode else '?'}\n"
                line += f"└ Planning: {level_str if level_str else 'N/A'}\n"
                line += f"└ Consigne: <b>{target_val}°C</b> | Mesuré: {ambient_val}°C"
                res.append(line)
        
        return "\n\n".join(res)

async def apply_heating_mode(target_mode, custom_temp=None):
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
                    target_cmd = "setTargetTemperature"
                    # Pour certains modèles, la commande de consigne change en manuel
                    print(f"DEBUG: Envoi {val}°C à {sid}")
                    
                    cmds = [Command(name=target_cmd, parameters=[val])]
                    if target_mode == "HOME":
                        cmds.append(Command(name=mode_cmd, parameters=["internal"]))
                    else:
                        cmds.append(Command(name=mode_cmd, parameters=[mode_manuel]))
                    
                    await client.execute_commands(d.device_url, cmds)
                except Exception as e:
                    print(f"ERREUR EXEC {sid}: {e}")

        await asyncio.sleep(10)
        return "Commandes envoyées. Faites 'LIST' pour vérifier."

# --- INTERFACE IDENTIQUE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 MAISON", callback_data="HOME")],
                [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
                [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]]
    await update.message.reply_text(f"<b>PILOTAGE v{VERSION}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "LIST":
        await query.edit_message_text("🔍 Scan des états en cours...")
        report = await get_detailed_listing()
        await query.edit_message_text(f"<b>ÉTAT ACTUEL</b>\n\n{report}", parse_mode='HTML')
    else:
        await query.edit_message_text("⏳ Synchronisation...")
        t = 16.0 if "ABS" in query.data else None
        await apply_heating_mode("HOME" if query.data=="HOME" else "ABSENCE", custom_temp=t)
        await query.edit_message_text("✅ Action terminée. Refaites un LIST.")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
