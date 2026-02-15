import os, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "7.2 (Full Commands & Attribute Fix)"

# --- CONFIGURATION ---
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

CONFORT_TEMPS = {
    "14253355#1": 19.5,
    "1640746#1": 19.0,
    "190387#1": 19.0,
    "4326513#1": 19.5
}

# --- SERVEUR ANTI-INTERRUPTION KOYEB ---
class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        print(f"[HEALTH] Check reçu à {self.date_time_string()}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Healthy")

def run_web_server():
    server = HTTPServer(('0.0.0.0', 8000), KeepAliveServer)
    server.serve_forever()

# --- LOGIQUE D'EXTRACTION ---
async def get_detailed_listing():
    print(f"\n" + "!"*60)
    print(f"--- DÉBUT DU SCAN INTEGRAL v{VERSION} ---")
    print(f"!"*60)
    
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        
        print(f"[DEBUG] {len(all_devices)} entités logicielles détectées.")
        
        # 1. TENTATIVE DE REFRESH ET LISTING DES COMMANDES
        for d in all_devices:
            # Correction : on utilise .widget et .command_name
            available_commands = [c.command_name for c in d.definition.commands]
            
            if "refreshTemperature" in available_commands:
                try:
                    print(f"[REFRESH] Envoi à {d.label} ({d.widget})")
                    await client.execute_commands(d.device_url, [Command("refreshTemperature")])
                except Exception as e:
                    print(f"  [!] Erreur refresh sur {d.label}: {e}")
        
        await asyncio.sleep(2)

        temp_map = {}
        res_telegram = []
        
        print("\n--- DUMP COMPLET DES 27 ENTITÉS ---")
        for d in all_devices:
            states = {s.name: s.value for s in d.states}
            available_commands = [c.command_name for c in d.definition.commands]
            base_url = d.device_url.split('#')[0]
            sid = d.device_url.split('/')[-1]
            
            # LOGS SANS FILTRE DANS KOYEB
            print(f"\nENTITÉ : {d.label}")
            print(f"  └ URL    : {d.device_url}")
            print(f"  └ Widget : {d.widget}")
            print(f"  └ OID    : {d.oid}")
            print(f"  └ COMMANDES DISPONIBLES : {available_commands}")
            print(f"  └ ÉTATS (STATES) :")
            for s_name, s_val in states.items():
                print(f"    -> {s_name}: {s_val}")
                
                # Capture des températures pour le mapping
                if s_name == "core:TemperatureState" and s_val is not None:
                    temp_map[base_url] = s_val

            # Préparation du rapport Telegram pour les radiateurs identifiés
            if sid in DEVICE_NAMES:
                name = DEVICE_NAMES[sid]
                eff = states.get("io:EffectiveTemperatureSetpointState", "?")
                ambient = states.get("core:TemperatureState")
                if ambient is None:
                    ambient = temp_map.get(base_url, "??")
                
                rate = states.get("io:CurrentWorkingRateState", 0)
                icon = "🔥" if (isinstance(rate, (int, float)) and rate > 0) else "❄️"
                
                res_telegram.append(
                    f"<b>{name}</b> {icon}\n"
                    f"└ 🌡️ Ambiante: <b>{ambient}°C</b>\n"
                    f"└ 🎯 Consigne: <b>{eff}°C</b>"
                )
        
        print(f"\n" + "!"*60)
        print("--- FIN DU SCAN INTEGRAL ---")
        print(f"!"*60 + "\n")
        
        return "\n\n".join(res_telegram)

# ---
