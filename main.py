import os, asyncio, threading, httpx, psycopg2, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.models import Command

VERSION = "15.3 (BEC Full Debug Planning)"

# =============================================================================
# CONFIGURATION
# =============================================================================
TOKEN            = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL    = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL           = os.getenv("DATABASE_URL")
BEC_USER         = os.getenv("BEC_EMAIL")
BEC_PASS         = os.getenv("BEC_PASSWORD")
SHELLY_TOKEN     = os.getenv("SHELLY_TOKEN")
SHELLY_ID        = os.getenv("SHELLY_ID")
SHELLY_SERVER    = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

ATLANTIC_API  = "https://apis.groupe-atlantic.com"
CLIENT_BASIC  = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
MY_SERVER     = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Transitions HC/HP (minutes depuis minuit)
# Format : (heure_debut_minutes, est_heure_creuse_après_transition)
# 01:56 → début HC | 07:56 → fin HC | 14:26 → début HC | 16:26 → fin HC
HC_TRANSITIONS = [
    ( 1 * 60 + 56, True),   # 01:56 → HC commence
    ( 7 * 60 + 56, False),  # 07:56 → HP commence
    (14 * 60 + 26, True),   # 14:26 → HC commence
    (16 * 60 + 26, False),  # 16:26 → HP commence
]

# Radiateurs
CONFORT_VALS = {
    "14253355#1": {"name": "Salon",          "temp": 19.5, "eco": 16.0},
    "190387#1":   {"name": "Chambre",         "temp": 19.0, "eco": 16.0},
    "1640746#1":  {"name": "Bureau",          "temp": 18.0, "eco": 15.0},
    "4326513#1":  {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0},
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# =============================================================================
# HEURES CREUSES
# =============================================================================
def is_heure_creuse(dt: datetime = None) -> bool:
    if dt is None:
        dt = datetime.now()
    m = dt.hour * 60 + dt.minute
    # HC : 01:56-07:56 et 14:26-16:26
    return (1*60+56 <= m < 7*60+56) or (14*60+26 <= m < 16*60+26)

def get_hc_label() -> str:
    now = datetime.now()
    m = now.hour * 60 + now.minute
    if is_heure_creuse(now):
        # Trouver fin du slot courant
        ends = [7*60+56, 16*60+26]
        fin = min((e for e in ends if e > m), default=7*60+56)
        return f"🟢 HC jusqu'à {fin//60:02d}h{fin%60:02d}"
    # Trouver prochain début HC
    starts = [1*60+56, 14*60+26]
    prochains = [s for s in starts if s > m]
    nxt = min(prochains) if prochains else min(starts)
    return f"🔴 HP — prochain HC à {nxt//60:02d}h{nxt%60:02d}"

def minutes_until_next_transition() -> int:
    """Retourne le nombre de secondes avant la prochaine transition HC/HP."""
    now = datetime.now()
    m = now.hour * 60 + now.minute
    all_transitions = sorted(t for t, _ in HC_TRANSITIONS)
    futures = [t for t in all_transitions if t > m]
    nxt = min(futures) if futures else (min(all_transitions) + 24 * 60)
    return (nxt - m) * 60 - now.second

# =============================================================================
# BASE DE DONNÉES
# =============================================================================
def init_db():
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT, temp_radiateur FLOAT,
                temp_shelly FLOAT, consigne FLOAT
            );
            -- Table transitions HC/HP : index relevé à chaque changement de tarif
            CREATE TABLE IF NOT EXISTS bec_transitions (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                index_kwh FLOAT NOT NULL,
                heure_creuse BOOLEAN NOT NULL  -- TRUE = période qui COMMENCE ici
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        log("DB initialisée")
    except Exception as e:
        log(f"DB init ERR: {e}")

def save_transition(index_kwh: float, heure_creuse: bool):
    """Enregistre l'index au moment d'une transition tarifaire."""
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bec_transitions (index_kwh, heure_creuse) VALUES (%s, %s)",
            (index_kwh, heure_creuse)
        )
        conn.commit()
        cur.close()
        conn.close()
        log(f"Transition enregistrée : {index_kwh:.3f} kWh HC={heure_creuse}")
    except Exception as e:
        log(f"Transition save ERR: {e}")

def get_conso_stats(jours: int = 7):
    """
    Calcule la conso HC et HP sur N jours en faisant la diff
    entre transitions consécutives.
    Retourne (conso_hc_kwh, conso_hp_kwh, nb_periodes)
    """
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT timestamp, index_kwh, heure_creuse
            FROM bec_transitions
            WHERE timestamp > NOW() - INTERVAL '%s days'
            ORDER BY timestamp ASC
        """, (jours,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if len(rows) < 2:
            return None

        conso_hc = 0.0
        conso_hp = 0.0
        nb = 0

        for i in range(len(rows) - 1):
            _, idx_start, hc_start = rows[i]
            _, idx_end, _          = rows[i + 1]
            diff = idx_end - idx_start
            if diff < 0:  # Compteur remis à zéro, ignorer
                continue
            if hc_start:
                conso_hc += diff
            else:
                conso_hp += diff
            nb += 1

        return conso_hc, conso_hp, nb
    except Exception as e:
        log(f"Conso stats ERR: {e}")
        return None

# =============================================================================
# SHELLY
# =============================================================================
async def get_shelly_temp():
    if not SHELLY_TOKEN:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://{SHELLY_SERVER}/device/status",
                data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10
            )
            return r.json()['data']['device_status']['temperature:0']['tC']
    except:
        return None

# =============================================================================
# RADIATEURS (Overkiz)
# =============================================================================
async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            full_id = d.device_url.split('#')[0].split('/')[-1] + "#1"
            if full_id in CONFORT_VALS:
                name = CONFORT_VALS[full_id]["name"]
                if name not in data:
                    data[name] = {"temp": None, "target": None}
                states = {s.name: s.value for s in d.states}
                t = states.get("core:TemperatureState")
                c = states.get("io:EffectiveTemperatureSetpointState") or states.get("core:TargetTemperatureState")
                if t is not None: data[name]["temp"] = t
                if c is not None: data[name]["target"] = c
        return data, shelly_t

async def apply_heating_mode(target_mode):
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as client:
        await client.login()
        devices = await client.get_devices()
        results = []
        for d in devices:
            sid = d.device_url.split('/')[-1]
            if sid in CONFORT_VALS:
                info = CONFORT_VALS[sid]
                t_val = info["temp"] if target_mode == "HOME" else info["eco"]
                mode_cmd = "setOperatingMode" if "Heater" in d.widget else "setTowelDryerOperatingMode"
                m_val = "internal" if target_mode == "HOME" else ("basic" if "Heater" in d.widget else "external")
                try:
                    await client.execute_commands(d.device_url, [
                        Command("setTargetTemperature", [t_val]),
                        Command(mode_cmd, [m_val])
                    ])
                    results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
                except Exception as e:
                    log(f"Erreur {info['name']}: {e}")
                    results.append(f"❌ <b>{info['name']}</b>")
        return "\n".join(results)

async def perform_record():
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        for name, vals in data.items():
            if vals["temp"] is not None:
                cur.execute(
                    "INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne) VALUES (%s,%s,%s,%s)",
                    (name, vals["temp"], (shelly_t if name == "Bureau" else None), vals["target"])
                )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"RECORD ERR: {e}")

def find_water_heater(devices: list) -> dict | None:
    """
    Trouve le chauffe-eau dans la liste des devices.
    Cherche d'abord par mots-clés, sinon prend le premier device.
    Compatible avec les noms : 'Chauffe-eau', 'Aqueo', 'Sauter Phazy', etc.
    """
    keywords = ["chauffe", "aqueo", "water", "ballon", "phazy", "sauter",
                "calypso", "aeromax", "explorer"]
    for d in devices:
        name = str(d.get("name", "")).lower()
        if any(k in name for k in keywords):
            return d
    # Fallback : premier device si un seul
    if len(devices) == 1:
        return devices[0]
    return None


def decode_planning(cap150_raw) -> list[str]:
    """
    Décode cap150 : liste de slots [debut_min, fin_min, flag, mode]
    Basé sur les screenshots : plages visibles ~01h56→06h30 et ~14h26→17h00
    Format confirmé : valeurs en minutes depuis minuit.
    255 = pas de plage active sur ce slot.
    """
    import json as _j
    jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim", "J8", "J9", "J10"]
    mode_map = {0: "Manuel", 3: "Eco+", 4: "Prog/Chauffe"}
    lines = []

    try:
        if isinstance(cap150_raw, str):
            cap150_raw = _j.loads(cap150_raw)
    except:
        return [f"  Erreur décodage: {cap150_raw}"]

    for i, slot in enumerate(cap150_raw):
        try:
            deb_m = int(slot[0])
            fin_m = int(slot[1])
            flag  = slot[2]
            mode  = int(slot[3])

            if deb_m == 0 and fin_m == 255:
                # Pas de plage horaire définie → toute la journée autorisée
                mode_s = mode_map.get(mode, f"mode{mode}")
                lines.append(f"  {jours[i]}: toute la journée [{mode_s}]")
            elif deb_m == 255 and fin_m == 255:
                lines.append(f"  {jours[i]}: inactif")
            else:
                deb_h = f"{deb_m//60:02d}h{deb_m%60:02d}"
                fin_h = f"{fin_m//60:02d}h{fin_m%60:02d}"
                mode_s = mode_map.get(mode, f"mode{mode}")
                lines.append(f"  {jours[i]}: {deb_h} → {fin_h} [{mode_s}]")
        except Exception as e:
            lines.append(f"  Slot{i}: {slot} (err: {e})")

    return lines if lines else ["  (vide)"]



# =============================================================================
async def bec_authenticate():
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{ATLANTIC_API}/users/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "password", "scope": "openid",
                  "username": f"GA-PRIVATEPERSON/{BEC_USER}",
                  "password": BEC_PASS},
            timeout=12,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
        log(f"BEC Auth error {r.status_code}: {r.text[:150]}")
        return None

async def bec_get_index() -> float | None:
    """Récupère uniquement l'index kWh (pour les transitions)."""
    token = await bec_authenticate()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=headers)
        if r.status_code != 200:
            return None
        setup = r.json()[0]
        aqueo = find_water_heater(setup.get("devices", []))
        if not aqueo:
            return None
        dev_id = aqueo.get("deviceId")
        r2 = await client.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=headers)
        if r2.status_code != 200:
            return None
        caps = {c["capabilityId"]: c["value"] for c in r2.json()}
        log(f"BEC caps: {caps}")
        # capId=59 = index total énergie en Wh (validé : 566581 Wh = 566.6 kWh)
        index_wh = float(caps.get(59, 0))
        return index_wh / 1000

async def manage_bec(action="GET"):
    if not BEC_USER or not BEC_PASS:
        return "❌ BEC_EMAIL ou BEC_PASSWORD manquants"

    token = await bec_authenticate()
    if not token:
        return "❌ Auth Magellan échouée"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=headers)
            if r.status_code != 200:
                return f"❌ Setup échoué ({r.status_code})"
            setup = r.json()[0]
            setup_id = setup.get("id")

            aqueo = find_water_heater(setup.get("devices", []))
            if not aqueo:
                noms = [d.get("name") for d in setup.get("devices", [])]
                log(f"BEC devices: {noms}")
                return f"❓ Chauffe-eau non trouvé. Devices: {noms}"

            dev_id = aqueo.get("deviceId")

            # --- GET (debug planning complet) ---
            if action == "GET":
                r2 = await client.get(
                    f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}",
                    headers=headers
                )
                caps = {c["capabilityId"]: c["value"] for c in r2.json()}
                log(f"BEC caps full: {caps}")

                import json as _json

                # Valeurs principales
                index_wh    = float(caps.get(59, 0))
                puissance_w = int(float(caps.get(164, 0)))
                temp_cible  = float(caps.get(22, 0))
                index_kwh   = index_wh / 1000
                hc          = is_heure_creuse()
                hc_label    = get_hc_label()

                # Mode chauffe (87) : 0=manuel, 3=eco+, 4=prog HC/HP
                mode_map   = {0: "Manuel", 3: "Eco+", 4: "Prog HC/HP"}
                mode_val   = int(float(caps.get(87, 0)))
                mode_label = mode_map.get(mode_val, f"Inconnu({mode_val})")

                # Résistance (99), Boost (165)
                resistance = "🔴 OFF" if str(caps.get(99, "0")) == "0" else "🟢 ON"
                boost      = "🟢 ON" if str(caps.get(165, "0")) not in ("0", "false") else "OFF"

                # Mode absence (227) : 0=normal, 1=activé, 2=en attente
                abs_map   = {0: "🏡 Normal", 1: "✈️ Activé", 2: "⏳ En attente"}
                abs_val   = int(float(caps.get(227, 0)))
                abs_label = abs_map.get(abs_val, f"Inconnu({abs_val})")

                # Timestamps absence (222/226) : [timestamp_debut, timestamp_fin]
                try:
                    abs_ts  = caps.get(222, caps.get(226, "[0,0]"))
                    ts_list = _json.loads(str(abs_ts)) if isinstance(abs_ts, str) else abs_ts
                    if ts_list and int(ts_list[0]) > 0:
                        deb = datetime.fromtimestamp(int(ts_list[0])).strftime("%d/%m %Hh%M")
                        fin = datetime.fromtimestamp(int(ts_list[1])).strftime("%d/%m %Hh%M")
                        abs_dates = f"{deb} → {fin}"
                    else:
                        abs_dates = "aucune période"
                except:
                    abs_dates = str(caps.get(222, "?"))

                # Chauffe en cours
                if puissance_w > 0:
                    chauffe = f"🔥 CHAUFFE ({puissance_w}W) — {'✅ HC' if hc else '⚠️ HP'}"
                else:
                    chauffe = "💤 En veille"

                # Planning cap150
                planning_lines = decode_planning(caps.get(150, []))

                lines = [
                    f"💧 <b>{aqueo.get('name', 'Aqueo')}</b> (id={dev_id})",
                    "",
                    "⚡ <b>ÉTAT</b>",
                    f"  {chauffe}",
                    f"  Consigne : <b>{temp_cible:.0f}°C</b>  Mode : <b>{mode_label}</b>",
                    f"  Résistance : {resistance}  Boost : {boost}",
                    f"  {hc_label}",
                    "",
                    "📊 <b>CONSO</b>",
                    f"  Index total (cap59) : <b>{index_kwh:.3f} kWh</b>",
                    f"  Index partiel (cap168) : {float(caps.get(168,0))/1000:.3f} kWh",
                    "",
                    "✈️ <b>ABSENCE</b>",
                    f"  État : {abs_label}",
                    f"  Période : {abs_dates}",
                    "",
                    "📅 <b>PLANNING cap150</b>",
                ] + planning_lines + [
                    "",
                    "🔧 <b>CAPS NON DOCUMENTÉS</b>",
                    f"  cap188={caps.get(188,'?')}  cap218={caps.get(218,'?')}",
                    f"  cap223={caps.get(223,'?')}  cap224={caps.get(224,'?')}",
                    f"  cap225={caps.get(225,'?')}  cap228={caps.get(228,'?')}",
                    f"  cap230={caps.get(230,'?')}",
                ]
                return "\n".join(lines)

            # --- STATS CONSO HC/HP ---
            if action == "STATS":
                stats = get_conso_stats(7)
                if not stats:
                    return (
                        "⚠️ Pas encore assez de données.\n"
                        "Les relevés sont automatiques à chaque transition HC/HP\n"
                        "(01h56, 07h56, 14h26, 16h26).\n"
                        "Revenez dans quelques heures."
                    )
                conso_hc, conso_hp, nb = stats
                total = conso_hc + conso_hp
                pct_hc = (conso_hc / total * 100) if total > 0 else 0
                return "\n".join([
                    "📊 <b>CONSO BALLON — 7 JOURS</b>",
                    f"🟢 Heures Creuses : <b>{conso_hc:.2f} kWh</b> ({pct_hc:.0f}%)",
                    f"🔴 Heures Pleines : <b>{conso_hp:.2f} kWh</b> ({100-pct_hc:.0f}%)",
                    f"⚡ Total : <b>{total:.2f} kWh</b>",
                    f"<i>Basé sur {nb} périodes mesurées</i>",
                ])

            # --- ABSENCE ---
            # --- ABSENCE ---
            if action == "ABSENCE":
                now_ts = int(datetime.now().timestamp())
                payload = {"absence": {"startDate": now_ts, "endDate": now_ts + 30 * 24 * 3600}}
                for k in ("address", "area", "currency", "mainHeatingEnergy", "mainDHWEnergy",
                          "name", "numberOfPersons", "numberOfRooms", "setupBuildingDate", "type"):
                    if k in setup: payload[k] = setup[k]
                r = await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}",
                                     json=payload, headers=headers)
                log(f"BEC Absence: {r.status_code}")
                return "✅ Mode absence activé (30j)" if r.status_code in (200, 204) else f"❌ {r.status_code}"

            # --- RETOUR MAISON ---
            if action == "HOME":
                payload = {"absence": {}}
                for k in ("address", "area", "currency", "mainHeatingEnergy", "mainDHWEnergy",
                          "name", "numberOfPersons", "numberOfRooms", "setupBuildingDate", "type"):
                    if k in setup: payload[k] = setup[k]
                r = await client.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}",
                                     json=payload, headers=headers)
                log(f"BEC Home: {r.status_code}")
                return "✅ Ballon remis en mode normal" if r.status_code in (200, 204) else f"❌ {r.status_code}"

        except Exception as e:
            log(f"BEC exception: {e}")
            return f"⚠️ Erreur: {str(e)}"

    return "❓ Action inconnue"

# =============================================================================
# INTERFACE TELEGRAM
# =============================================================================
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MAISON",   callback_data="HOME"),
         InlineKeyboardButton("❄️ ABSENCE",  callback_data="ABSENCE")],
        [InlineKeyboardButton("🔍 ÉTAT",     callback_data="LIST"),
         InlineKeyboardButton("📊 STATS 7J", callback_data="REPORT")],
        [InlineKeyboardButton("💧 BALLON ÉTAT",  callback_data="BEC_GET"),
         InlineKeyboardButton("📈 CONSO HC/HP",  callback_data="BEC_STATS")],
        [InlineKeyboardButton("🏡 BALLON MAISON",  callback_data="BEC_HOME"),
         InlineKeyboardButton("✈️ BALLON ABSENCE", callback_data="BEC_ABSENCE")],
    ])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data in ["HOME", "ABSENCE"]:
            await query.edit_message_text(f"⏳ Radiateurs {query.data}...")
            report = await apply_heating_mode(query.data)
            await query.edit_message_text(
                f"<b>RÉSULTAT {query.data}</b>\n\n{report}",
                parse_mode='HTML', reply_markup=get_keyboard()
            )

        elif query.data == "LIST":
            await query.edit_message_text("🔍 Lecture...")
            data, shelly_t = await get_current_data()
            lines = []
            for n, v in data.items():
                lines.append(f"📍 <b>{n}</b>: {v['temp']}°C (Cible: {v['target']}°C)")
                if n == "Bureau" and shelly_t:
                    lines.append(f"   └ 🌡️ <i>Shelly : {shelly_t}°C</i>")
            lines.append(f"\n{get_hc_label()}")
            await query.edit_message_text(
                "🌡️ <b>ÉTAT ACTUEL</b>\n\n" + "\n".join(lines),
                parse_mode='HTML', reply_markup=get_keyboard()
            )

        elif query.data == "REPORT":
            conn = None
            try:
                conn = psycopg2.connect(DB_URL)
                cur = conn.cursor()
                cur.execute("""
                    SELECT AVG(temp_radiateur), AVG(temp_shelly),
                           AVG(temp_shelly - temp_radiateur), COUNT(*)
                    FROM temp_logs
                    WHERE room = 'Bureau'
                    AND timestamp > NOW() - INTERVAL '7 days'
                    AND temp_shelly IS NOT NULL;
                """)
                s = cur.fetchone(); cur.close()
                msg = (f"📊 <b>BILAN 7J (Bureau)</b>\n"
                       f"Rad: {s[0]:.1f}°C / Shelly: {s[1]:.1f}°C\n"
                       f"<b>Δ: {s[2]:+.1f}°C</b>\n<i>{s[3]} mesures.</i>"
                       ) if s and s[3] > 0 else "⚠️ Pas de données."
            except Exception as e:
                log(f"SQL ERR: {e}"); msg = "⚠️ Erreur SQL"
            finally:
                if conn: conn.close()
            await query.message.reply_text(msg, parse_mode='HTML')

        elif query.data.startswith("BEC_"):
            action = query.data.replace("BEC_", "")
            await query.edit_message_text(f"⏳ Ballon {action}...")
            res = await manage_bec(action)
            msg = res[:4000] if len(res) > 4000 else res
            await query.edit_message_text(
                f"<b>BALLON</b>\n\n{msg}",
                parse_mode='HTML', reply_markup=get_keyboard()
            )

    except Exception as e:
        log(f"Handler ERR: {e}")
        await query.edit_message_text(f"⚠️ Erreur : {str(e)}", reply_markup=get_keyboard())

# =============================================================================
# BACKGROUND : relevés aux transitions HC/HP uniquement
# =============================================================================
async def background_transition_logger():
    """
    Attend chaque transition HC/HP et enregistre l'index à ce moment précis.
    4 relevés par jour suffisent pour calculer la conso HC vs HP.
    """
    while True:
        wait_sec = minutes_until_next_transition()
        log(f"Prochain relevé BEC dans {wait_sec//60}min {wait_sec%60}s")
        await asyncio.sleep(wait_sec + 30)  # +30s pour être sûr d'être dans le bon slot

        index = await bec_get_index()
        if index is not None:
            hc = is_heure_creuse()
            save_transition(index, hc)
            log(f"Transition enregistrée : {index:.3f} kWh — {'HC' if hc else 'HP'}")
        else:
            log("BEC transition : impossible de lire l'index")

async def background_rad_logger():
    """Enregistrement horaire des radiateurs."""
    while True:
        await asyncio.sleep(3600)
        if DB_URL:
            await perform_record()

# =============================================================================
# MAIN
# =============================================================================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def main():
    init_db()
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(),
        daemon=True
    ).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(
        "start",
        lambda u, c: u.message.reply_text(f"🚀 v{VERSION}", reply_markup=get_keyboard())
    ))
    app.add_handler(CallbackQueryHandler(button_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(background_transition_logger())
    loop.create_task(background_rad_logger())
    log(f"DÉMARRAGE v{VERSION}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
