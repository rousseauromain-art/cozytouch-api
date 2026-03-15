"""Module BEC — Ballon eau chaude Atlantic/Sauter via API Magellan."""
import asyncio, json, httpx, psycopg2
from datetime import datetime
from config import ATLANTIC_API, CLIENT_BASIC, BEC_USER, BEC_PASS, DB_URL, HC_TRANSITIONS, log

# cap237-243 = consigne quantité par jour (Lun→Dim)
# Formule confirmée : % affiché app = 3×T − 90  ↔  T = (%+90)/3
# Exemple : 60% → 50°C, 80% → 56.7°C, 100% → 63.3°C
CAPS_QTITE = [237, 238, 239, 240, 241, 242, 243]

def pct_to_temp(pct: int) -> float:
    """% app → °C. 100% = 65°C (max cap252). Autres : T=(pct+90)/3."""
    return 65.0 if pct >= 100 else round((pct + 90) / 3, 1)

def temp_to_pct(t: float) -> int:
    """°C → % app, plafonné à 100% (65°C donne 105 sans plafond)."""
    return min(round(3 * t - 90), 100)


# ---------------------------------------------------------------------------
# HEURES CREUSES
# ---------------------------------------------------------------------------
def is_heure_creuse(dt: datetime = None) -> bool:
    if dt is None:
        dt = datetime.now()
    m = dt.hour * 60 + dt.minute
    return (0*60+56 <= m < 6*60+26) or (14*60+26 <= m < 16*60+56)

def get_hc_label() -> str:
    now = datetime.now()
    m   = now.hour * 60 + now.minute
    if is_heure_creuse(now):
        ends = [6*60+26, 16*60+56]
        fin  = min((e for e in ends if e > m), default=6*60+26)
        return f"🟢 HC jusqu'à {fin//60:02d}h{fin%60:02d}"
    starts    = [0*60+56, 14*60+26]
    prochains = [s for s in starts if s > m]
    nxt = min(prochains) if prochains else min(starts)
    return f"🔴 HP — prochain HC à {nxt//60:02d}h{nxt%60:02d}"

def minutes_until_next_transition() -> int:
    now = datetime.now()
    m   = now.hour * 60 + now.minute
    all_t   = sorted(t for t, _ in HC_TRANSITIONS)
    futures = [t for t in all_t if t > m]
    nxt = min(futures) if futures else (min(all_t) + 24 * 60)
    return (nxt - m) * 60 - now.second


# ---------------------------------------------------------------------------
# DB — transitions HC/HP (relevé à chaque début de HC)
# ---------------------------------------------------------------------------
def save_transition(index_kwh: float, heure_creuse: bool, temp_eau: float | None = None):
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO bec_transitions (index_kwh, heure_creuse, temp_eau) VALUES (%s,%s,%s)",
            (index_kwh, heure_creuse, temp_eau)
        )
        conn.commit(); cur.close(); conn.close()
        log(f"Transition BEC : {index_kwh:.3f} kWh HC={heure_creuse} T={temp_eau}°C")
    except Exception as e:
        log(f"Transition save ERR: {e}")

def get_transitions_log(limit: int = 20):
    """Retourne les N derniers relevés avec calcul conso inter-période."""
    if not DB_URL:
        return None, None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        # Les 20 derniers dans l'ordre chronologique
        cur.execute("""
            SELECT timestamp, index_kwh, heure_creuse, temp_eau FROM (
                SELECT * FROM bec_transitions ORDER BY timestamp DESC LIMIT %s
            ) sub ORDER BY timestamp ASC
        """, (limit,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if not rows:
            return None, None

        # Calcul conso : delta attribué au statut du DEBUT de période (ligne i-1)
        # Ex: 00h56(HC)→06h26(HP) : +1.514kWh consommés PENDANT HC, pas HP
        entries = []
        for i, (ts, idx, hc, temp) in enumerate(rows):
            delta = None
            delta_hc = hc  # statut par défaut
            if i > 0:
                prev_idx = rows[i-1][1]
                prev_hc  = rows[i-1][2]  # statut au DEBUT de la période
                d = idx - prev_idx
                delta = d if d >= 0 else None
                delta_hc = prev_hc       # conso = période précédente
            entries.append({
                "ts": ts, "idx": idx, "hc": hc,
                "temp": temp, "delta": delta, "delta_hc": delta_hc
            })

        # Totaux basés sur le statut du début de période
        hc_k = hp_k = 0.0
        for e in entries:
            if e["delta"] is None:
                continue
            if e["delta_hc"]:
                hc_k += e["delta"]
            else:
                hp_k += e["delta"]

        return entries, (hc_k, hp_k)
    except Exception as e:
        log(f"Transitions ERR: {e}"); return None, None

def save_mode_change(mode: str):
    """Enregistre un changement de mode BEC (HOME/ABSENCE) pour la surveillance.
    Appelé après chaque écriture réussie sur cap237-243."""
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bec_mode_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL
            )
        """)
        cur.execute("INSERT INTO bec_mode_log (mode) VALUES (%s)", (mode,))
        conn.commit(); cur.close(); conn.close()
        log(f"Mode BEC enregistré : {mode}")
    except Exception as e:
        log(f"save_mode_change ERR: {e}")


def get_absence_days() -> int | None:
    """Retourne le nombre de jours depuis le dernier passage en mode ABSENCE.
    Retourne 0 si le dernier mode enregistré est HOME.
    Retourne None si aucun historique (pas d'alerte).
    """
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        # Créer la table si elle n'existe pas encore
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bec_mode_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL
            )
        """)
        conn.commit()
        # Dernier changement de mode
        cur.execute("""
            SELECT mode, timestamp FROM bec_mode_log
            ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            return None  # pas d'historique → pas d'alerte

        last_mode, last_ts = row
        if last_mode == "HOME":
            return 0  # mode maison actif → pas d'alerte
        # ABSENCE, ABSENCE_AUTO_70 → compter les jours

        from datetime import datetime
        delta = datetime.now() - last_ts.replace(tzinfo=None)
        return delta.days
    except Exception as e:
        log(f"get_absence_days ERR: {e}"); return None

def reset_transitions():
    """Vide la table bec_transitions."""
    if not DB_URL:
        return False
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("TRUNCATE TABLE bec_transitions")
        conn.commit(); cur.close(); conn.close()
        log("bec_transitions réinitialisée")
        return True
    except Exception as e:
        log(f"Reset ERR: {e}"); return False


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def find_water_heater(devices: list) -> dict | None:
    kw = ["chauffe","aqueo","water","ballon","phazy","sauter","calypso","aeromax","explorer"]
    for d in devices:
        if any(k in str(d.get("name","")).lower() for k in kw):
            return d
    return devices[0] if len(devices) == 1 else None

def decode_hc_schedule(raw) -> str:
    """Décode cap245-251 : plages HC en minutes depuis minuit."""
    try:
        slots = json.loads(str(raw)) if isinstance(raw, str) else raw
        plages = [f"{s[0]//60:02d}h{s[0]%60:02d}→{s[1]//60:02d}h{s[1]%60:02d}"
                  for s in slots if s and s != [0,0] and s[0] != s[1]]
        return "  ".join(plages) if plages else "—"
    except:
        return str(raw)

def decode_quantite_semaine(caps: dict) -> list[str]:
    """Décode cap237-243 → % quantité par jour (formule : % = 3T-90)."""
    jours = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]
    lines = []
    for i, cap_id in enumerate(CAPS_QTITE):
        val = caps.get(cap_id)
        if val is None:
            continue
        try:
            slots = json.loads(str(val)) if isinstance(val, str) else val
            t = float(slots[0][1]) if isinstance(slots, list) else float(val)
            pct = min(round(3 * t - 90), 100)  # 65°C → 100% (plafonné)
            lines.append(f"  {jours[i]}: <b>{pct}%</b> ({t:.0f}°C)")
        except:
            lines.append(f"  {jours[i]}: {val}")
    return lines


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------
async def bec_authenticate():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{ATLANTIC_API}/users/token",
            headers={"Authorization": f"Basic {CLIENT_BASIC}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "password", "scope": "openid",
                  "username": f"GA-PRIVATEPERSON/{BEC_USER}", "password": BEC_PASS},
            timeout=12)
        if r.status_code == 200:
            return r.json().get("access_token")
        log(f"BEC Auth {r.status_code}: {r.text[:100]}")
        return None

async def bec_get_index() -> tuple[float | None, float | None]:
    """Relevé à chaque transition : retourne (index_kWh, temp_haut_ballon)."""
    token = await bec_authenticate()
    if not token: return None, None
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
        if r.status_code != 200: return None, None
        dev = find_water_heater(r.json()[0].get("devices", []))
        if not dev: return None, None
        r2 = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev['deviceId']}", headers=h)
        if r2.status_code != 200: return None, None
        caps = {x["capabilityId"]: x["value"] for x in r2.json()}
        idx  = float(caps.get(59, 0)) / 1000
        # cap266 = haut du ballon, sinon cap265 = milieu
        t_raw = caps.get(266, caps.get(265))
        temp  = float(t_raw) if t_raw is not None else None
        return idx, temp


# ---------------------------------------------------------------------------
# WRITE CAPABILITY
# ---------------------------------------------------------------------------
async def write_capability(c: httpx.AsyncClient, h: dict, dev_id: int,
                           cap_id: int, value) -> bool:
    """Écrit une cap avec retry x2 et poll état jusqu'à 15s."""
    for attempt in range(2):
        try:
            r = await c.post(f"{ATLANTIC_API}/magellan/executions/writecapability",
                             json={"capabilityId": cap_id, "deviceId": dev_id, "value": str(value)},
                             headers=h, timeout=15)
            if r.status_code != 201:
                log(f"writecap {cap_id} attempt {attempt+1} → HTTP {r.status_code}")
                if attempt == 0:
                    await asyncio.sleep(3)
                continue
            exec_id = r.json()
            for _ in range(15):  # poll jusqu'à 30s
                await asyncio.sleep(2)
                r2 = await c.get(f"{ATLANTIC_API}/magellan/executions/{exec_id}",
                                 headers=h, timeout=10)
                state = r2.json().get("state", 0) if r2.status_code == 200 else 0
                if state == 3:
                    return True
                if state not in (1, 2):
                    break
        except Exception as e:
            log(f"writecap {cap_id} attempt {attempt+1} exception: {e}")
            if attempt == 0:
                await asyncio.sleep(3)
    return False


# ---------------------------------------------------------------------------
# ACTION PRINCIPALE
# ---------------------------------------------------------------------------
async def manage_bec(action="GET"):
    if not BEC_USER or not BEC_PASS:
        return "❌ BEC_EMAIL ou BEC_PASSWORD manquants"
    token = await bec_authenticate()
    if not token:
        return "❌ Auth Magellan échouée"
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as c:
        try:
            r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
            if r.status_code != 200:
                return f"❌ Setup {r.status_code}"
            setup    = r.json()[0]
            setup_id = setup.get("id")
            dev      = find_water_heater(setup.get("devices", []))
            if not dev:
                return f"❓ Non trouvé. Devices: {[d.get('name') for d in setup.get('devices',[])]}"
            dev_id = dev.get("deviceId")

            # ── GET : état complet ─────────────────────────────────────────
            if action == "GET":
                r2   = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=h)
                caps = {x["capabilityId"]: x["value"] for x in r2.json()}
                log(f"BEC caps: {caps}")

                nom_w  = int(float(caps.get(164, 0)))
                res99  = str(caps.get(99, "0"))
                temp_c = float(caps.get(22, 0))
                idx    = float(caps.get(59, 0)) / 1000
                hc     = is_heure_creuse()
                mode   = {0:"Manuel",3:"Eco+",4:"Prog HC/HP"}.get(int(float(caps.get(87,0))), "?")
                boost  = "🟢 ON" if str(caps.get(165,"0")) not in ("0","false") else "OFF"

                chauffe = (f"🔥 CHAUFFE ({nom_w}W) — {'✅ HC' if hc else '⚠️ HP'}"
                           if res99 != "0" else "💤 En veille")
                resist  = ("🟢 ON — chauffe active" if res99 != "0"
                           else f"🔴 OFF  (nominale : {nom_w}W)")

                def ft(v): return f"{float(v):.1f}°C" if v is not None else "—"
                def fv(v): return f"{float(v):.0f}L"  if v is not None else "—"

                t_haut = caps.get(266); t_mil = caps.get(265); t_bas = caps.get(267)
                v40    = caps.get(268); v40tot = caps.get(270); pct_v = caps.get(271)

                absent = {0:"🏡 Normal",1:"✈️ Activé",2:"⏳ En attente"}.get(
                    int(float(caps.get(227, 0))), "?")
                try:
                    ts  = caps.get(222, "[0,0]")
                    tsl = json.loads(str(ts)) if isinstance(ts, str) else ts
                    dates = (f"{datetime.fromtimestamp(int(tsl[0])).strftime('%d/%m %Hh%M')}"
                             f"→{datetime.fromtimestamp(int(tsl[1])).strftime('%d/%m %Hh%M')}"
                             ) if tsl and int(tsl[0]) > 0 else "aucune"
                except: dates = "?"

                hc_sched = decode_hc_schedule(caps.get(245))
                qtite_lines = decode_quantite_semaine(caps)

                return "\n".join([
                    f"💧 <b>{dev.get('name','Chauffe-eau')}</b>",
                    "", "⚡ <b>ÉTAT</b>",
                    f"  {chauffe}",
                    f"  Consigne : <b>{temp_c:.0f}°C</b>  Mode : <b>{mode}</b>",
                    f"  Résistance : {resist}  |  Boost : {boost}",
                    f"  {get_hc_label()}",
                    "", "🌡️ <b>TEMPÉRATURES EAU</b>",
                    f"  Haut:{ft(t_haut)}  Mil:{ft(t_mil)}  Bas:{ft(t_bas)}",
                    "", "💦 <b>DISPONIBILITÉ</b>",
                    f"  V40 : <b>{fv(v40)}</b> / {fv(v40tot)}  →  <b>{float(pct_v or 0):.0f}%</b>",
                    "", "📅 <b>PLAGES HC</b>",
                    f"  {hc_sched}",
                    "", "💧 <b>QUANTITÉ PAR JOUR</b>",
                ] + qtite_lines + [
                    "", "📊 <b>CONSO</b>",
                    f"  Index : <b>{idx:.3f} kWh</b>",
                    "", "✈️ <b>ABSENCE</b>",
                    f"  {absent}  |  {dates}",
                ])

            # ── STATS : bilan conso HC/HP ──────────────────────────────────
            if action == "STATS":
                entries, totaux = get_transitions_log(20)
                if not entries:
                    return "⚠️ Aucun relevé en base. Les relevés sont pris à chaque transition HC/HP (4×/jour)."

                lines = ["📋 <b>20 DERNIERS RELEVÉS BEC</b>", ""]
                lines.append("<code>Date  Heure  État   Index    Δ(HC/HP période)  T°C</code>")
                lines.append("<code>──────────────────────────────────────────────────</code>")

                for e in entries:
                    ts     = e["ts"]
                    hc_lbl = "🟢HC" if e["hc"] else "🔴HP"
                    idx    = f"{e['idx']:.3f}"
                    temp   = f"{e['temp']:.1f}°C" if e["temp"] is not None else "  — "
                    date   = ts.strftime("%d/%m")
                    heure  = ts.strftime("%H:%M")
                    if e["delta"] is not None:
                        d_lbl = "🟢" if e["delta_hc"] else "🔴"
                        delta = f"{d_lbl}+{e['delta']:.3f}"
                    else:
                        delta = "          —    "
                    lines.append(f"<code>{date} {heure}  {hc_lbl}  {idx}  {delta}  {temp}</code>")

                if totaux:
                    hc_k, hp_k = totaux
                    tot = hc_k + hp_k
                    pct = (hc_k / tot * 100) if tot > 0 else 0
                    lines += [
                        "",
                        f"🟢 HC total : <b>{hc_k:.3f} kWh</b> ({pct:.0f}%)",
                        f"🔴 HP total : <b>{hp_k:.3f} kWh</b> ({100-pct:.0f}%)",
                        f"⚡ Total    : <b>{tot:.3f} kWh</b>",
                    ]
                return "\n".join(lines)

            # ── ABSENCE : 60% tous les jours (sans déclencher le mode absence Atlantic)
            # ── HOME    : 80% semaine (Lun-Ven) + 100% weekend (Sam-Dim)
            # Pas d'appel PUT setup → pas de procédure de réchauffage forcé Atlantic
            jours = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]

            if action == "ABSENCE":
                # 60% sur les 7 jours → T = (60+90)/3 = 50°C
                cibles = {cap_id: 60 for cap_id in CAPS_QTITE}
            elif action == "HOME":
                # Lun-Ven (cap237-241) = 80% → T = (80+90)/3 = 56.7°C
                # Sam-Dim (cap242-243) = 100% → T = (100+90)/3 = 63.3°C
                cibles = {}
                for i, cap_id in enumerate(CAPS_QTITE):
                    cibles[cap_id] = 100 if i >= 5 else 80  # i=5→Sam, i=6→Dim
            else:
                return "❓ Action inconnue"

            # Écriture parallèle des 7 jours → réduit le temps de ~90s à ~20s
            async def write_one(i, cap_id):
                pct = cibles[cap_id]
                T   = pct_to_temp(pct)
                slot_val = json.dumps([[0, T], [0, 0], [0, 0], [0, 0]])
                ok = await write_capability(c, h, dev_id, cap_id, slot_val)
                log(f"BEC write cap{cap_id} ({jours[i]})={pct}% ({T}°C) → {'OK' if ok else 'ERR'}")
                return ok

            ok_list = await asyncio.gather(
                *[write_one(i, cap_id) for i, cap_id in enumerate(CAPS_QTITE)]
            )
            results = ["✓" if ok else "✗" for ok in ok_list]

            # Validation : relecture des caps après écriture
            await asyncio.sleep(3)
            r_check = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}",
                                  headers=h)
            caps_check = {x["capabilityId"]: x["value"] for x in r_check.json()}
            qtite_lines = decode_quantite_semaine(caps_check)
            label = "✈️ <b>BALLON ABSENCE</b>" if action == "ABSENCE" else "🏡 <b>BALLON MAISON</b>"
            # Enregistrer le changement de mode pour la surveillance
            save_mode_change("ABSENCE" if action == "ABSENCE" else "HOME")
            return "\n".join([
                f"{label} — validation",
                "", "💧 <b>QUANTITÉ PAR JOUR (valeurs lues)</b>",
            ] + qtite_lines)

        except Exception as e:
            log(f"BEC ERR: {e}"); return f"⚠️ {e}"
