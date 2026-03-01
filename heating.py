"""Module radiateurs — Overkiz, Shelly, PostgreSQL."""
import httpx, psycopg2
from pyoverkiz.client import OverkizClient
from pyoverkiz.models import Command
from config import (OVERKIZ_EMAIL, OVERKIZ_PASSWORD, MY_SERVER, DB_URL,
                    SHELLY_TOKEN, SHELLY_ID, SHELLY_SERVER, CONFORT_VALS, log)


def init_db():
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS temp_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT, temp_radiateur FLOAT, temp_shelly FLOAT, consigne FLOAT
            );
            CREATE TABLE IF NOT EXISTS bec_transitions (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                index_kwh FLOAT NOT NULL,
                heure_creuse BOOLEAN NOT NULL,
                temp_eau FLOAT
            );
        """)
        conn.commit()
        # Migration : ajoute temp_eau si DB existante sans la colonne
        cur.execute("ALTER TABLE bec_transitions ADD COLUMN IF NOT EXISTS temp_eau FLOAT")
        conn.commit(); cur.close(); conn.close()
        log("DB initialisée")
    except Exception as e:
        log(f"DB init ERR: {e}")

def get_rad_stats():
    """Delta moyen Shelly-Radiateur sur 7 jours pour Bureau."""
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""SELECT AVG(temp_shelly - temp_radiateur), COUNT(*)
                       FROM temp_logs WHERE room='Bureau'
                       AND timestamp > NOW() - INTERVAL '7 days'
                       AND temp_shelly IS NOT NULL""")
        row = cur.fetchone(); cur.close(); conn.close()
        return row
    except Exception as e:
        log(f"Stats ERR: {e}"); return None


async def get_shelly_temp():
    if not SHELLY_TOKEN:
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"https://{SHELLY_SERVER}/device/status",
                             data={"id": SHELLY_ID, "auth_key": SHELLY_TOKEN}, timeout=10)
            return r.json()["data"]["device_status"]["temperature:0"]["tC"]
    except:
        return None


async def get_current_data():
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
        await c.login()
        devices  = await c.get_devices()
        shelly_t = await get_shelly_temp()
        data = {}
        for d in devices:
            fid = d.device_url.split("#")[0].split("/")[-1] + "#1"
            if fid in CONFORT_VALS:
                name = CONFORT_VALS[fid]["name"]
                if name not in data:
                    data[name] = {"temp": None, "target": None}
                st = {s.name: s.value for s in d.states}
                t  = st.get("core:TemperatureState")
                tg = (st.get("io:EffectiveTemperatureSetpointState")
                      or st.get("core:TargetTemperatureState"))
                if t  is not None: data[name]["temp"]   = t
                if tg is not None: data[name]["target"] = tg
        return data, shelly_t

async def apply_heating_mode(target_mode: str) -> str:
    async with OverkizClient(OVERKIZ_EMAIL, OVERKIZ_PASSWORD, server=MY_SERVER) as c:
        await c.login()
        devices = await c.get_devices()
        results = []
        for d in devices:
            sid = d.device_url.split("/")[-1]
            if sid not in CONFORT_VALS:
                continue
            info  = CONFORT_VALS[sid]
            t_val = info["temp"] if target_mode == "HOME" else info["eco"]
            is_h  = "Heater" in d.widget
            m_cmd = "setOperatingMode" if is_h else "setTowelDryerOperatingMode"
            m_val = "internal" if target_mode == "HOME" else ("basic" if is_h else "external")
            try:
                await c.execute_commands(d.device_url, [
                    Command("setTargetTemperature", [t_val]),
                    Command(m_cmd, [m_val])
                ])
                results.append(f"✅ <b>{info['name']}</b> : {t_val}°C")
            except Exception as e:
                log(f"Rad {info['name']} ERR: {e}")
                results.append(f"❌ <b>{info['name']}</b>")
        return "\n".join(results)

async def perform_record():
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        for name, v in data.items():
            if v["temp"] is not None:
                cur.execute(
                    "INSERT INTO temp_logs (room,temp_radiateur,temp_shelly,consigne)"
                    " VALUES (%s,%s,%s,%s)",
                    (name, v["temp"], (shelly_t if name == "Bureau" else None), v["target"])
                )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"RECORD ERR: {e}")
