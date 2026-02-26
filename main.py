import os, asyncio, threading, httpx, time, sys, json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "13.35 (Retour État Stable)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
BEC_USER = os.getenv("BEC_EMAIL")
BEC_PASS = os.getenv("BEC_PASSWORD")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5, "eco": 16.0},
    "190387#1": {"name": "Chambre", "temp": 19.0, "eco": 16.0},
    "1640746#1": {"name": "Bureau", "temp": 17.5, "eco": 14.5},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0}
}

# --- LOGIQUE TARIFAIRE ---
def get_tarif():
    now = datetime.now().time()
    # HC: 14h26-16h26 et 01h56-07h56
    hchp_slots = [
        (datetime.strptime("01:56", "%H:%M").time(), datetime.strptime("07:56", "%H:%M").time()),
        (datetime.strptime("14:26", "%H:%M").time(), datetime.strptime("16:26", "%H:%M").time())
    ]
    for start, end in hchp_slots:
        if start <= now <= end: return "HC"
    return "HP"

# --- MODULE BALLON ---
async def manage_bec(action="GET"):
    async with httpx.AsyncClient() as client:
        r_auth = await client.post(f"{ATLANTIC_API}/users/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}"},
            data={"grant_type": "password", "scope": "openid", "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS})
        token = r_auth.json().get("access_token")
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        r_setup = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
        setup = r_setup.json()[0]
        aqueo = next((d for d in setup["devices"] if "aqueo" in str(d.get("name","")).lower()), None)
        
        if action == "GET":
            r_caps = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={aqueo['deviceId']}", headers=h)
            caps = {c['capabilityId']: c['value'] for c in r_caps.json()}
            pui = int(caps.get(164, 0))
            tarif = get_tarif()
            
            # Tracking conso
            if pui > 0:
                with open("conso.csv", "a") as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')};{pui};{tarif}\n")
            
            return (f"💧 <b>{aqueo['name']}</b>\n"
                    f"⚡ État : {pui}W ({'🔥' if pui>0 else '💤'})\n"
                    f"🕒 Tarif : <b>{tarif}</b>\n"
                    f"📊 Index : {float(caps.get(168,0))/1000:.2f} kWh\n"
                    f"✈️ Absence : {'OUI' if setup.get('absence') else 'NON'}")

        if action in ["HOME", "ABSENCE"]:
            payload = {"id": setup["id"], "name": setup["name"], "type": setup["type"]}
            if action == "ABSENCE":
                start = int(time.time())
                payload["absence"] = {"startDate": start, "endDate": start + (365*24*3600)}
            else:
                payload["absence"] = {}
            await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup['id']}", json=payload, headers=h)
            return f"✅ Ballon : Mode {action} OK"

# --- INTERFACE ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 RADS MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ RADS ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT RADS", callback_data="LIST")],
        [InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 ÉTAT BALLON", callback_data="BEC_GET")],
        [InlineKeyboardButton("🏡 BALLON HOME", callback_data="BEC_HOME"), InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    
    # Ballon
    if query.data.startswith("BEC_"):
        res = await manage_bec(query.data.replace("BEC_", ""))
        await query.edit_message_text(res, parse_mode='HTML', reply_markup=get_keyboard())
    
    # Radiateurs
    elif query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Action {query.data}...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as c:
            await c.login(); devices = await c.get_devices(); res = []
            for d in devices:
                sid = d.device_url.split('/')[-1]
                if sid in CONFORT_VALS:
                    conf = CONFORT_VALS[sid]
                    t_val = conf["temp"] if query.data == "HOME" else conf["eco"]
                    op_mode = "internal" if query.data == "HOME" else ("basic" if "Heater" in d.widget else "external")
                    cmd_name = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                    await c.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(cmd_name, [op_mode])])
                    res.append(f"✅ {conf['name']}")
            await query.edit_message_text(f"<b>RÉSULTAT {query.data}:</b>\n"+"\n".join(res), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data == "LIST":
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as c:
            await c.login(); devices = await c.get_devices(); lines = []
            for d in devices:
                bid = d.device_url.split('#')[0].split('/')[-1] + "#1"
                if bid in CONFORT_VALS:
                    st = {s.name: s.value for s in d.states}
                    lines.append(f"📍 <b>{CONFORT_VALS[bid]['name']}</b>: {st.get('core:TemperatureState')}°C")
            await query.edit_message_text("🌡️ <b>ÉTAT RADIATEURS</b>\n\n"+"\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

# --- SERVEUR & MAIN ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__": main()
    
