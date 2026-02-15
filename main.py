import os, asyncio, threading, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "8.0 (Shelly & Clean)"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
MY_SERVER = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Shelly Cloud Config (à remplir dans tes variables d'env Koyeb)
SHELLY_AUTH_KEY = os.getenv("SHELLY_AUTH_KEY")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-61-eu.shelly.cloud")

ROOMS = {
    "io://2091-1547-6688/14253355": "Salon",
    "io://2091-1547-6688/1640746": "Chambre",
    "io://2091-1547-6688/190387": "Bureau",
    "io://2091-1547-6688/4326513": "Sèche-Serviette"
}

CONFORT_TEMPS = {
    "14253355#1": 19.5, "1640746#1": 19.0, "190387#1": 19.0, "4326513#1": 19.5
}

class KeepAliveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

def run_web_server():
    HTTPServer(('0.0.0.0', 8000), KeepAliveServer).serve_forever()

async def get_shelly_temp():
    """Interroge l'API Cloud de Shelly pour le GT3"""
    if not SHELLY_AUTH_KEY or not SHELLY_ID:
        return None
    import httpx
    url = f"https://{SHELLY_SERVER}/device/status"
    data = {"id": SHELLY_ID, "auth_key": SHELLY_AUTH_KEY}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data)
            res = r.json()
            # Structure typique Shelly Plus/Pro/Gen3 : status['temperature:0']['tC']
            return res['data']['device_status']['temperature:0']['tC']
    except:
        return None

async def get_detailed_listing():
    print(f"\n--- SCAN v{VERSION} ---")
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        all_devices = await client.get_devices()
        results = {name: {"temp": None, "target": None} for name in ROOMS.values()}
        
        for d in all_devices:
            states = {s.name: s.value for s in d.states}
            # DEBUG MAX toujours présent
            print(f"DEBUG: {d.label} | {d.device_url} | States: {states}")
            
            base_url = d.device_url.split('#')[0]
            if base_url in ROOMS:
                room_name = ROOMS[base_url]
                t_val = states.get("core:TemperatureState")
                if t_val and t_val > 0: results[room_name]["temp"] = t_val
                if "io:EffectiveTemperatureSetpointState" in states:
                    results[room_name]["target"] = states["io:EffectiveTemperatureSetpointState"]

        # Récupération Shelly pour le Bureau
        shelly_t = await get_shelly_temp()
        
        report = []
        for room, data in results.items():
            t_amb = f"<b>{data['temp']}°C</b>" if data['temp'] else "--"
            t_set = f"<b>{data['target']}°C</b>" if data['target'] else "--"
            line = f"📍 {room}: {t_amb} (Consigne: {t_set})"
            if room == "Bureau" and shelly_t:
                line += f"\n   └ 🌡️ Shelly GT3: <b>{shelly_t}°C</b>"
            report.append(line)

        return "\n".join(report)

async def apply_heating_mode(target_mode, custom_temp=None):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_TEMPS:
                is_towel = "Towel" in getattr(d, 'widget', '')
                mode_cmd = "setTowelDryerOperatingMode" if is_towel else "setOperatingMode"
                manuel_val = "external" if is_towel else "basic"
                val = custom_temp if custom_temp else (CONFORT_TEMPS.get(sid, 19.0) if target_mode == "HOME" else 16.0)
                
                print(f"[CMD] {sid} -> {val}°C")
                await client.execute_commands(d.device_url, [
                    Command(name="setTargetTemperature", parameters=[val]),
                    Command(name=mode_cmd, parameters=["internal" if target_mode == "HOME" else manuel_val])
                ])

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ ABSENCE", callback_data="ABS_16")],
        [InlineKeyboardButton("🔍 ACTUALISER", callback_data="LIST")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = await query.edit_message_text("⏳")
    if query.data != "LIST":
        await apply_heating_mode("HOME" if query.data == "HOME" else "ABS", custom_temp=16.0 if "ABS" in query.data else None)
    report = await get_detailed_listing()
    await m.edit_text(f"<b>ÉTAT v{VERSION}</b>\n\n{report}", parse_mode='HTML', reply_markup=get_keyboard())

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Bot Ready", reply_markup=get_keyboard())))
    app.add_handler(CallbackQueryHandler(button_handler))
    # drop_pending_updates aide à éviter le conflit au reboot
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
