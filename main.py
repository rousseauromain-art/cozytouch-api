import os
import asyncio
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.enums import Server
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURATION ---
OVERKIZ_EMAIL = os.getenv("OVERKIZ_USER")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SERVER = SUPPORTED_SERVERS[Server.ATLANTIC_COZYTOUCH]

# --- 1. FONCTIONS DE RÉCUPÉRATION / ACTION ---

async def get_devices_listing():
    """Version corrigée sans erreur d'attribut"""
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        listing = []
        for d in devices:
            # On filtre les éléments qui n'ont pas de définition (souvent les ponts ou capteurs vides)
            if not d.definition:
                continue
                
            # On ignore le bridge Cozytouch et les objets "pod"
            if "pod" in d.device_url:
                continue
            
            # Récupération sécurisée des commandes
            cmds = [c.command_name for c in d.definition.commands]
            
            if "setHolidays" in cmds:
                listing.append(f"🌡️ RADIATEUR : {d.label}")
            elif any("Towel" in c or "OperatingMode" in c for c in cmds) or "Adelis" in d.label:
                listing.append(f"🧼 SÈCHE-SERVIETTE : {d.label}")
                
        return "\n".join(listing) if listing else "Aucun appareil pilotable trouvé."

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        results = []
        for device in devices:
            cmds = [c.command_name for c in device.definition.commands]
            
            # Correction pour les Radiateurs (ONIRIS)
            if "setHolidays" in cmds:
                if target_mode == "ABSENCE":
                    # On envoie les paramètres sous forme de LISTE [valeur]
                    await client.execute_command(device.device_url, "setHolidaysTargetTemperature", [10.0])
                    await client.execute_command(device.device_url, "setHolidays", ["on"])
                    results.append(f"❄️ {device.label} -> 10°C")
                else:
                    await client.execute_command(device.device_url, "setHolidays", ["off"])
                    results.append(f"🏠 {device.label} -> Planning")
            
            # Correction pour le sèche-serviette (ADELIS / I2G_Actuator)
            elif "setOperatingMode" in cmds:
                # 'away' pour absence, 'internal' pour planning/maison
                mode = "away" if target_mode == "ABSENCE" else "internal"
                await client.execute_command(device.device_url, "setOperatingMode", [mode])
                results.append(f"🧼 {device.label} -> {mode}")
                
        return "\n".join(results)

# --- 2. COMMANDES DU BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Répond au /start"""
    keyboard = [
        [InlineKeyboardButton("❄️ Mode Absence (10°C)", callback_data="ABSENCE")],
        [InlineKeyboardButton("🏠 Mode Maison (Planning)", callback_data="HOME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Contrôle du chauffage Romain :\n(Utilise /liste pour tester)", reply_markup=reply_markup)

async def liste_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Répond au /liste"""
    await update.message.reply_text("🔍 Recherche de tes équipements...")
    try:
        res = await get_devices_listing()
        await update.message.reply_text(f"Équipements détectés :\n\n{res}")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les clics sur les boutons"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"🔄 Application du mode {query.data}...")
    try:
        status = await apply_heating_mode(query.data)
        await query.edit_message_text(text=f"Terminé !\n{status}")
    except Exception as e:
        await query.edit_message_text(text=f"❌ Erreur : {e}")

# --- 3. LANCEMENT ---

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # On enregistre les handlers après avoir défini les fonctions
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", liste_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot démarré...")
    app.run_polling()
