import os, asyncio, threading, httpx, psycopg2, time, sys, json
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.32 (HC/HP Tracking)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

# Radiateurs
CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 18, "eco": 15},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.0,"eco": 16.0}
}

def log_koyeb(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- LOGIQUE HEURES CREUSES ---
def get_tarif_type():
    """Détermine si l'heure actuelle est en Heures Creuses."""
    now = datetime.now().time()
    # Plages HC : 01:56 - 07:56 et 14:26 - 16:26
    hc_slots = [
        (datetime.strptime("01:56", "%H:%M").time(), datetime.strptime("07:56", "%H:%M").time()),
        (datetime.strptime("14:26", "%H:%M").time(), datetime.strptime("16:26", "%H:%M").time())
    ]
    for start, end in hc_slots:
        if start <= now <= end:
            return "HC"
    return "HP"

# --- MODULE BEC ---
async def manage_bec(action="GET"):
    async with httpx.AsyncClient() as client:
        # Auth
        r_auth = await client.post(f"{ATLANTIC_API}/users/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}"},
            data={"grant_type": "password", "scope": "openid", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS}, timeout=12)
        if r_auth.status_code != 200: return "❌ Erreur Auth"
        token = r_auth.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Récupération Setup
        r_setup = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=headers)
        setup = r_setup.json()[0]
        setup_id = setup.get("id")
        aqueo = next((d for d in setup.get("devices", []) if "aqueo" in str(d.get("name","")).lower()), None)
        dev_id = aqueo.get("deviceId")

        if action == "GET":
            r_caps = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=headers)
            caps = {c['capabilityId']: c['value'] for c in r_caps.json()}
            
            conso_kwh = float(caps.get(168, 0)) / 1000
            puissance = int(caps.get(164, 0))
            is_heating = "🔥" if puissance > 0 else "💤"
            tarif = get_tarif_type()
            
            # Enregistrement avec distinction HC/HP
            if puissance > 0:
                with open("conso_history.csv", "a") as f:
                    # Format: Date;Heure;Puissance;Tarif
                    f.write(f"{datetime.now().strftime('%Y-%m-%d;%H:%M')};{puissance};{tarif}\n")

            msg = [
                f"💧 <b>BALLON : {aqueo.get('name')}</b>",
                f"⚡ État : <b>{is_heating} {puissance} W</b>",
                f"🕒 Tarif actuel : <b>{tarif}</b>",
                f"📊 Index : <b>{conso_kwh:.2f} kWh</b>",
                f"✈️ Absence : {'OUI' if setup.get('absence') else 'NON'}"
            ]
            return "\n".join(msg)

        if action == "ABSENCE":
            start = int(time.time()); end = start + (365 * 24 * 3600)
            payload = {"id": setup_id, "absence": {"startDate": start, "endDate": end}, "name": setup.get("name"), "type": setup.get("type")}
            await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}", json=payload, headers=headers)
            return "✅ Ballon mis en Absence"

        if action == "HOME":
            payload = {"id": setup_id, "absence": {}, "name": setup.get("name"), "type": setup.get("type")}
            await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}", json=payload, headers=headers)
            return "✅ Ballon mis en mode Maison"

    return "Erreur"

# --- TELEGRAM ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 RADS HOME", callback_data="HOME"), InlineKeyboardButton("❄️ RADS ABS", callback_data="ABSENCE")],
        [InlineKeyboardButton("💧 ÉTAT BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🏡 BALLON HOME", callback_data="BEC_HOME"), InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data.startswith("BEC_"):
        action = query.data.replace("BEC_", "")
        res = await manage_bec(action)
        await query.edit_message_text(res, parse_mode='HTML', reply_markup=get_keyboard())
    elif query.data in ["HOME", "ABSENCE"]:
        # ... (Logique Overkiz radiateurs identique au précédent)
        pass

# --- SERVEUR & MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    log_koyeb(f"DÉMARRAGE v{VERSION}"); app.run_polling()

if __name__ == "__main__": main()
