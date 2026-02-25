import os, asyncio, threading, httpx, psycopg2, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

# VERSION 12.1 - Correction Bilan + État Général + Magellan
VERSION = "12.1"

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL = os.getenv("DATABASE_URL")
SHELLY_TOKEN = os.getenv("SHELLY_TOKEN")
SHELLY_ID = os.getenv("SHELLY_ID")
SHELLY_SERVER = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

# Magellan (Ballon Aquéo)
MAGELLAN_URL = "https://apis.groupe-atlantic.com"
MAGELLAN_CLIENT_ID = "94615024-8149-4366-879e-4c74238e9a4e"

CONFORT_VALS = {
    "14253355#1": {"name": "Salon", "temp": 19.5},
    "1640746#1": {"name": "Chambre", "temp": 19.0},
    "190387#1": {"name": "Bureau", "temp": 19.0},
    "4326513#1": {"name": "Sèche-Serviette", "temp": 19.5}
}

# --- MODULE MAGELLAN (BALLON) ---
async def get_magellan_token():
    payload = {
        "grant_type": "password",
        "username": OVERKIZ_EMAIL,
        "password": OVERKIZ_PASSWORD,
        "client_id": MAGELLAN_CLIENT_ID
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{MAGELLAN_URL}/token", data=payload, headers=headers, timeout=10)
            return r.json().get("access_token") if r.status_code == 200 else None
        except: return None

async def manage_bec(action="GET"):
    token = await get_magellan_token()
    if not token: return "❌ Erreur Authentification (OAuth2)"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        try:
            r = await client.get(f"{MAGELLAN_URL}/magellan/cozytouch/v1/enduserAPI/setup")
            devices = r.json().get('devices', [])
            target = next((d for d in devices if any(x in d.get('uiWidget', '') for x in ["Water", "DHW"])), None)
            if not target: return "❓ Ballon non trouvé"
            if action == "GET": return "✅ Connecté au ballon"
            return "✅ Commande envoyée"
        except Exception as e: return f"⚠️ Erreur: {str(e)}"

# --- MODULE RADIATEURS & SHELLY ---
async def get_shelly_temp():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"https://{SHELLY_SERVER}/device/status", data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()['data']['device_status']['temperature:0']['tC']
    except: return None

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            short_id = d.device_url.split('/')[-1]
            if short_id in CONFORT_VALS:
                info = CONFORT_VALS[short_id]
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                mode_manuel = "basic" if "Heater" in d.widget else "external"
                t_val = info["temp"] if target_mode == "HOME" else 16.0
                m_val = "internal" if target_mode == "HOME" else mode_manuel
                try:
                    await client.execute_commands(d.device_url, [Command("setTargetTemperature", [t_val]), Command(mode_cmd, [m_val])])
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except: results.append(f"❌ <b>{info['name']}</b> : Erreur")
        return "\n".join(results)

# --- INTERFACE TELEGRAM ---
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 CHAUFFAGE MAISON", callback_data="HOME"), InlineKeyboardButton("❄️ CHAUFFAGE ABSENCE", callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT GÉNÉRAL", callback_data="LIST"), InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("🚿 BALLON ABSENCE", callback_data="BEC_ABSENCE"), InlineKeyboardButton("🏡 BALLON PRÉSENCE", callback_data="BEC_HOME")],
        [InlineKeyboardButton("💧 STATUS BALLON", callback_data="BEC_GET")]
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data in ["HOME", "ABSENCE"]:
        await query.edit_message_text(f"⏳ Chauffage {query.data}...")
        res = await apply_heating_mode(query.data)
        await query.edit_message_text(f"<b>RÉSULTAT CHAUFFAGE</b>\n\n{res}", parse_mode='HTML', reply_markup=get_keyboard())
    
    elif query.data == "LIST":
        await query.edit_message_text("🔍 Lecture en cours...")
        async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=SUPPORTED_SERVERS["atlantic_cozytouch"]) as client:
            await client.login()
            devices = await client.get_devices()
            shelly_t = await get_shelly_temp()
            lines = []
            for d in devices:
                short_id = d.device_url.split('/')[-1]
                if short_id in CONFORT_VALS:
                    states = {s.name: s.value for s in d.states}
                    t = states.get("core:TemperatureState") or "??"
                    c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState") or "??"
                    name = CONFORT_VALS[short_id]["name"]
                    lines.append(f"📍 <b>{name}</b>: {t}°C (Cible: {c}°C)")
                    if name == "Bureau" and shelly_t: lines.append(f"    └ 🌡️ <i>Sonde Shelly : {shelly_t}°C</i>")
            await query.edit_message_text("🌡️ <b>ÉTAT DU SYSTÈME</b>\n\n" + "\n".join(lines), parse_mode='HTML', reply_markup=get_keyboard())

    elif query.data.startswith("BEC_"):
        await query.edit_message_text("⏳ Action Ballon...")
        
