import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

VERSION = "4.5 (Commande /temp Libre)"

# Configuration
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

async def apply_heating_mode(target_mode, custom_temp=None):
    """Logique unifiée pour HOME, ABSENCE (boutons) et TEMP (manuel)"""
    from pyoverkiz.client import OverkizClient
    from pyoverkiz.models import Command

    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        for d in devices:
            short_id = d.device_url.split('/')[-1]
            if short_id in CONFORT_TEMPS:
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_manuel = "basic" if "Heater" in d.widget else "external"
                
                try:
                    if target_mode == "HOME":
                        val = CONFORT_TEMPS[short_id]
                        await client.execute_commands(d.device_url, [
                            Command(name="setTargetTemperature", parameters=[val]),
                            Command(name=mode_cmd, parameters=["internal"])
                        ])
                    else: # Mode ABSENCE ou TEMP manuelle
                        val = custom_temp if custom_temp else 16.0
                        await client.execute_commands(d.device_url, [
                            Command(name="setTargetTemperature", parameters=[val]),
                            Command(name=mode_cmd, parameters=[mode_manuel])
                        ])
                except Exception as e:
                    print(f"Erreur sur {short_id}: {e}")

        await asyncio.sleep(10)
        updated_devices = await client.get_devices()
        results = []
        for d in devices:
            short_id = d.device_url.split('/')[-1]
            if short_id in CONFORT_TEMPS:
                for ud in updated_devices:
                    if ud.device_url == d.device_url:
                        res_target = ud.states.get("core:TargetTemperatureState").value
                        results.append(f"<b>{d.label}</b>\n└ Reçu: {res_target}°C ✅")
        return "\n".join(results)

# --- GESTIONNAIRES DE COMMANDES ---

async def temp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la commande /temp [valeur]"""
    if not context.args:
        await update.message.reply_text("❌ Précisez une température. Exemple : <code>/temp 19.5</code>", parse_mode='HTML')
        return

    try:
        val = float(context.args[0].replace(',', '.'))
        if not (7 <= val <= 28):
            await update.message.reply_text("⚠️ La température doit être entre 7°C et 28°C.")
            return
        
        msg = await update.message.reply_text(f"🚀 Forçage manuel à {val}°C en cours...")
        report = await apply_heating_mode("TEMP", custom_temp=val)
        await msg.edit_text(f"<b>FORÇAGE RÉUSSI ({val}°C)</b>\n\n{report}", parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Valeur incorrecte. Exemple : /temp 19")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 MAISON (Prog)", callback_data="HOME")],
        [InlineKeyboardButton("❄️ ABSENCE (16°C)", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ÉTAT ACTUEL", callback_data="LIST")]
    ]
    text = (f"<b>PILOTAGE v{VERSION}</b>\n\n"
            "• Utilisez les boutons pour les modes rapides.\n"
            "• Écrivez <code>/temp 19</code> pour forcer une température spécifique.")
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "LIST":
        # (Fonction get_states_fast de la version précédente à inclure ici)
        from pyoverkiz.client import OverkizClient
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
            await client.login()
            devices = await client.get_devices()
            res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_TEMPS:
                    t = d.states.get("core:TargetTemperatureState").value
                    m = d.states.get("core:OperatingModeState") or d.states.get("io:TowelDryerOperatingModeState")
                    res.append(f"<b>{d.label}</b>\n└ {m.value if m else '?'}: {t}°C")
            await query.edit_message_text(f"<b>ÉTAT ACTUEL</b>\n\n" + "\n".join(res), parse_mode='HTML')
    else:
        await query.edit_message_text("⏳ Action en cours...")
        temp = 16.0 if query.data == "ABS_16" else None
        mode = "HOME" if query.data == "HOME" else "ABSENCE"
        report = await apply_heating_mode(mode, custom_temp=temp)
        await query.edit_message_text(f"<b>RAPPORT ACTION</b>\n\n{report}", parse_mode='HTML')

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("temp", temp_command)) # La nouvelle commande !
    application.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== DEMARRAGE v{VERSION} ===")
    application.run_polling()

if __name__ == "__main__":
    main()
