import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.5 (Fix Dump & No Crash)"

TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

DEVICE_NAMES = {
    "14253355#1": "Salon",
    "1640746#1": "Chambre",
    "190387#1": "Bureau",
    "4326513#1": "Sèche-Serviette"
}

# --- KEEP ALIVE KOYEB ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LE SCANNEUR DE TOUS LES CHAMPS ---
async def get_detailed_listing():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        res = []
        
        print("\n" + "!"*60)
        print("!!! DUMP DES VALEURS BRUTES DE L'API !!!")
        
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                print(f"\n[APPAREIL: {DEVICE_NAMES[sid]} ({sid})]")
                states_dict = {s.name: s.value for s in d.states}
                
                # ON LOG TOUT DANS LA CONSOLE SANS EXCEPTION
                for name, val in sorted(states_dict.items()):
                    print(f"  {name} ===> {val}")

                # Construction du message Telegram simplifié pour le test
                target = states_dict.get("io:EffectiveTemperatureSetpointState", "?")
                
                # On essaie d'afficher ce qu'on peut en attendant ton retour logs
                ambient = states_dict.get("core:TemperatureState", "Inconnu")
                
                line = f"<b>{DEVICE_NAMES[sid]}</b>\n"
                line += f"└ Consigne: {target}°C\n"
                line += f"└ Champ 'core:TemperatureState': {ambient}"
                res.append(line)
        
        print("\n" + "!"*60 + "\n")
        return "\n\n".join(res)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔍 SCAN COMPLET", callback_data="LIST")]]
    await update.message.reply_text("Mode Analyse v5.5", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Scan en cours... Regarde tes logs Koyeb !")
    
    report = await get_detailed_listing()
    
    await query.edit_message_text(f"<b>RÉSULTATS</b>\n\n{report}", parse_mode='HTML')
    await context.bot.send_message(
        chat_id=query.message.chat_id, 
        text="Menu :", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 NOUVEAU SCAN", callback_data="LIST")]])
    )

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== SCANNER READY v{VERSION} ===")
    app.run_polling()

if __name__ == "__main__":
    main()
