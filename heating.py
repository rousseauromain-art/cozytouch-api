"""Module radiateurs — Overkiz, Shelly, PostgreSQL."""
import httpx, psycopg2
from datetime import datetime, timedelta
from pyoverkiz.client import OverkizClient
from pyoverkiz.models import Command
from config import (OVERKIZ_EMAIL, OVERKIZ_PASSWORD, MY_SERVER, DB_URL,
                    SHELLY_TOKEN, SHELLY_ID, SHELLY_SERVER, CONFORT_VALS, log)

# Pièces à monitorer spécifiquement (avec Shelly)
SALON_ROOM = "Salon"
# Jours de télétravail de l'amie (lundi=0, ..., vendredi=4)
TELETRAVAIL_JOURS = {3, 4}  # Jeudi=3, Vendredi=4


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
                room TEXT,
                temp_radiateur FLOAT,
                temp_shelly FLOAT,
                consigne FLOAT,
                heure_creuse BOOLEAN
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
        # Migrations douces
        for col, typ in [("temp_eau", "FLOAT"), ("heure_creuse", "BOOLEAN")]:
            cur.execute(f"ALTER TABLE bec_transitions ADD COLUMN IF NOT EXISTS {col} {typ}")
            cur.execute(f"ALTER TABLE temp_logs ADD COLUMN IF NOT EXISTS heure_creuse BOOLEAN")
        conn.commit(); cur.close(); conn.close()
        log("DB initialisée")
    except Exception as e:
        log(f"DB init ERR: {e}")


# ---------------------------------------------------------------------------
# STATS RADIATEURS
# ---------------------------------------------------------------------------
def get_rad_stats():
    """Delta moyen Shelly-Radiateur 7 jours pour Bureau."""
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


def get_salon_stats() -> str:
    """Analyse thermique salon sur 7 jours :
    - Évolution horaire moyenne (pour trouver le meilleur moment de chauffe)
    - Comparaison HC vs HP
    - Détection Jeudi/Vendredi (télétravail)
    """
    if not DB_URL:
        return "❌ DB non configurée"
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()

        # 1. Température moyenne par heure de la journée
        cur.execute("""
            SELECT EXTRACT(HOUR FROM timestamp)::int AS heure,
                   AVG(temp_shelly) AS t_amb,
                   AVG(temp_radiateur) AS t_rad,
                   COUNT(*) AS n
            FROM temp_logs
            WHERE room = %s
              AND timestamp > NOW() - INTERVAL '7 days'
              AND temp_shelly IS NOT NULL
            GROUP BY heure ORDER BY heure
        """, (SALON_ROOM,))
        hourly = cur.fetchall()

        # 2. Écart HC vs HP
        cur.execute("""
            SELECT heure_creuse,
                   AVG(temp_shelly) AS t_amb,
                   COUNT(*) AS n
            FROM temp_logs
            WHERE room = %s
              AND timestamp > NOW() - INTERVAL '7 days'
              AND temp_shelly IS NOT NULL
              AND heure_creuse IS NOT NULL
            GROUP BY heure_creuse
        """, (SALON_ROOM,))
        hc_hp = {row[0]: row for row in cur.fetchall()}

        # 3. Télétravail Jeu/Ven vs reste
        cur.execute("""
            SELECT EXTRACT(DOW FROM timestamp)::int AS dow,
                   AVG(temp_shelly) AS t_amb,
                   COUNT(*) AS n
            FROM temp_logs
            WHERE room = %s
              AND timestamp > NOW() - INTERVAL '14 days'
              AND temp_shelly IS NOT NULL
            GROUP BY dow ORDER BY dow
        """, (SALON_ROOM,))
        by_day = cur.fetchall()

        # 4. Chute température 06h26→07h30 (fin HC → réveil)
        cur.execute("""
            SELECT
                AVG(t_0626.temp_shelly) AS t_fin_hc,
                AVG(t_0730.temp_shelly) AS t_reveil,
                COUNT(*) AS n
            FROM (
                SELECT DATE(timestamp) AS jour, AVG(temp_shelly) AS temp_shelly
                FROM temp_logs WHERE room=%s AND temp_shelly IS NOT NULL
                AND EXTRACT(HOUR FROM timestamp) = 6
                AND EXTRACT(MINUTE FROM timestamp) BETWEEN 20 AND 40
                GROUP BY jour
            ) t_0626
            JOIN (
                SELECT DATE(timestamp) AS jour, AVG(temp_shelly) AS temp_shelly
                FROM temp_logs WHERE room=%s AND temp_shelly IS NOT NULL
                AND EXTRACT(HOUR FROM timestamp) = 7
                AND EXTRACT(MINUTE FROM timestamp) BETWEEN 20 AND 40
                GROUP BY jour
            ) t_0730 ON t_0626.jour = t_0730.jour
        """, (SALON_ROOM, SALON_ROOM))
        inertie = cur.fetchone()

        cur.close(); conn.close()

        if not hourly:
            return "📊 <b>SALON</b> — pas encore assez de données (reviens dans quelques jours)"

        # Courbe horaire compacte : grouper par blocs de 3h
        lines = ["📊 <b>SALON 7J</b>  <code>H=heure T=ambiance R=radiateur</code>", ""]
        hc_zones = set(range(1, 7)) | set(range(14, 17))
        for h, t_amb, t_rad, n in hourly:
            hc = "🟢" if h in hc_zones else "🔴"
            t_r = f"{t_rad:.0f}" if t_rad else "—"
            flag = "⬆️" if t_amb and t_amb >= 20 else ("🥶" if t_amb and t_amb < 17 else "  ")
            lines.append(f"<code>{h:02d}h{hc} {t_amb:.1f}° R{t_r}° {flag}</code>")

        # HC vs HP résumé
        if hc_hp:
            lines.append("")
            hc_row = hc_hp.get(True); hp_row = hc_hp.get(False)
            t_hc = f"{hc_row[1]:.1f}°" if hc_row else "—"
            t_hp = f"{hp_row[1]:.1f}°" if hp_row else "—"
            lines.append(f"🟢HC={t_hc}  🔴HP={t_hp}")

        # Inertie fin HC → réveil
        if inertie and inertie[2] and inertie[2] > 0 and inertie[0] and inertie[1]:
            chute = inertie[0] - inertie[1]
            verdict = "✅ok" if abs(chute) < 1.5 else "⚠️relance HP utile"
            lines.append(f"🔁 06h26→07h30 : {inertie[0]:.1f}°→{inertie[1]:.1f}° ({chute:+.1f}°) {verdict}")

        # Télétravail
        if by_day:
            jours_noms = ["Di","Lu","Ma","Me","Je","Ve","Sa"]
            parts = []
            for dow, t_amb, n in by_day:
                dow_i = int(dow)
                ttt = "💻" if dow_i - 1 in TELETRAVAIL_JOURS else ""
                parts.append(f"{jours_noms[dow_i]}{ttt}:{t_amb:.0f}°")
            lines.append("📅 " + "  ".join(parts))

        return "\n".join(lines)

    except Exception as e:
        log(f"Salon stats ERR: {e}")
        return f"⚠️ {e}"


# ---------------------------------------------------------------------------
# SHELLY + OVERKIZ
# ---------------------------------------------------------------------------
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


async def perform_record(heure_creuse: bool = False):
    """Enregistrement horaire températures radiateurs + Shelly."""
    try:
        data, shelly_t = await get_current_data()
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        for name, v in data.items():
            if v["temp"] is not None:
                cur.execute(
                    "INSERT INTO temp_logs (room, temp_radiateur, temp_shelly, consigne, heure_creuse)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (name, v["temp"],
                     shelly_t if name == SALON_ROOM else None,
                     v["target"], heure_creuse)
                )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        log(f"RECORD ERR: {e}")
