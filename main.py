import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "5.6 (Full Verbose Debug)"

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

# --- SCANNER TOTAL ---
async def get_total_dump():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        
        print("\n" + "#"*80)
        print("### DUMP STRATOSPHÉRIQUE - TOUTES LES DONNÉES DISPONIBLES ###")
        print("#"*80)

        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in DEVICE_NAMES:
                name = DEVICE_NAMES[sid]
                print(f"\n\n>>> APPAREIL : {name} ({sid}) <<<")
                print(f"    Label      : {d.label}")
                print(f"    Widget     : {d.widget}")
                print(f"    Protocol   : {d.protocol}")
                print(f"    CONTENU DES ETATS (STATES) :")
                
                # On récupère tous les états, même ceux qui n'ont pas de valeur
                try:
                    sorted_states = sorted(d.states, key=lambda x: x.name)
                    for s in sorted_states:
                        # Affichage du nom, de la valeur et du type
                        print(f"      [STATE] {s.name:45} | Valeur: {s.value} (Type: {type(s.value).__name__})")
                except Exception as e:
                    print(f"      [!] Erreur lecture states: {e}")

                # On cherche aussi dans les capteurs liés (sensors) si existants
                if hasattr(d, 'sensors') and d.sensors:
                    print(f"    CAPTEURS LIES :")
                    for sensor in d.sensors:
                        print(f"      [SENSOR] {sensor}")

        print("\n" + "#"*80 + "\n")
        return "Dump terminé dans les logs Koyeb."

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🚀 LANCER LE DUMP TOTAL", callback_data="DUMP")]]
    await update.message.reply_text(f"Diagnostic Atlantic v{VERSION}", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🔄 Extraction en cours... Patience...")
    
    try:
        await get_total_dump()
        await query.edit_message_text("✅ Dump envoyé dans les logs Koyeb !\n\nVa voir la console Koyeb pour trouver tes températures.")
    except Exception as e:
        print(f"CRASH DUMP: {e}")
        await query.edit_message_text(f"❌ Erreur lors du dump : {e}")

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print(f"=== READY v{VERSION} ===")
    app.run_polling()

if __name__ == "__main__":
    main()
