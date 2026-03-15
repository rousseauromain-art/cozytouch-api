"""Module BEC — Ballon eau chaude Atlantic/Sauter via API Magellan."""
import asyncio, json, httpx, psycopg2
from datetime import datetime
from config import ATLANTIC_API, CLIENT_BASIC, BEC_USER, BEC_PASS, DB_URL, HC_TRANSITIONS, log

# cap237-243 = consigne quantité par jour (Lun→Dim)
# Formule confirmée : % affiché app = 3×T − 90  ↔  T = (%+90)/3
# Exemple : 60% → 50°C, 80% → 56.7°C, 100% → 63.3°C
CAPS_QTITE = [237, 238, 239, 240, 241, 242, 243]

def pct_to_temp(pct: int) -> float:
    return round((pct + 90) / 3, 1)


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
# DB — transitions HC/HP
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

def get_conso_stats(jours: int = 7):
    """Retourne (conso_hc, conso_hp, nb_periodes, chute_temp_hp_moy)."""
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""SELECT timestamp, index_kwh, heure_creuse, temp_eau
            FROM bec_transitions WHERE timestamp > NOW() - INTERVAL '%s days'
            ORDER BY timestamp ASC""", (jours,))
        rows = cur.fetchall(); cur.close(); conn.close()
        if len(rows) < 2:
            return None
        hc_k = hp_k = 0.0; nb = 0; chutes = []
        for i in range(len(rows) - 1):
            _, is1, hc1, t1 = rows[i]
            _, ie2, _,  t2 = rows[i + 1]
            diff = ie2 - is1
            if diff < 0: continue
            if hc1: hc_k += diff
            else:   hp_k += diff
            if not hc1 and t1 is not None and t2 is not None:
                chutes.append(t1 - t2)
            nb += 1
        return hc_k, hp_k, nb, (sum(chutes)/len(chutes) if chutes else None)
    except Exception as e:
        log(f"Conso stats ERR: {e}"); return None


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
    try:
        slots = json.loads(str(raw)) if isinstance(raw, str) else raw
        plages = [f"{s[0]//60:02d}h{s[0]%60:02d}→{s[1]//60:02d}h{s[1]%60:02d}"
                  for s in slots if s and s != [0,0] and s[0] != s[1]]
        return "  ".join(plages) if plages else "—"
    except:
        return str(raw)

def decode_quantite_semaine(caps: dict) -> list[str]:
    jours = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]
    lines = []
    for i, cap_id in enumerate(CAPS_QTITE):
        val = caps.get(cap_id)
        if val is None:
            continue
        try:
            slots = json.loads(str(val)) if isinstance(val, str) else val
            t = float(slots[0][1]) if isinstance(slots, list) else float(val)
            pct = round(3 * t - 90)
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
        t_raw = caps.get(266, caps.get(265))
        temp  = float(t_raw) if t_raw is not None else None
        return idx, temp


# ---------------------------------------------------------------------------
# WRITE CAPABILITY
# ---------------------------------------------------------------------------
async def write_capability(c: httpx.AsyncClient, h: dict, dev_id: int,
                           cap_id: int, value) -> bool:
    r = await c.post(f"{ATLANTIC_API}/magellan/executions/writecapability",
                     json={"capabilityId": cap_id, "deviceId": dev_id, "value": str(value)},
                     headers=h, timeout=15)
    if r.status_code != 201:
        log(f"writecap {cap_id}={value} → HTTP {r.status_code}")
        return False
    exec_id = r.json()
    for _ in range(8):
        await asyncio.sleep(1)
        r2 = await c.get(f"{ATLANTIC_API}/magellan/executions/{exec_id}", headers=h, timeout=10)
        state = r2.json().get("state", 0) if r2.status_code == 200 else 0
        if state == 3: return True
        if state not in (1, 2): break
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

            # ── GET ─────────────────────────────────────────────────────────
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

            # ── STATS ────────────────────────────────────────────────────────
            if action == "STATS":
                s = get_conso_stats(7)
                if not s:
                    return "⚠️ Pas encore assez de données (4 relevés/jour aux transitions)."
                hc_k, hp_k, nb, chute = s
                tot = hc_k + hp_k
                pct = (hc_k / tot * 100) if tot > 0 else 0
                lines = [
                    "📊 <b>CONSO BALLON — 7 JOURS</b>",
                    f"🟢 Heures Creuses : <b>{hc_k:.2f} kWh</b> ({pct:.0f}%)",
                    f"🔴 Heures Pleines : <b>{hp_k:.2f} kWh</b> ({100-pct:.0f}%)",
                    f"⚡ Total : <b>{tot:.2f} kWh</b>  |  <i>{nb} périodes</i>",
                ]
                if chute is not None:
                    lines.append(f"🌡️ Chute temp. HP : <b>−{chute:.1f}°C</b> en moyenne")
                return "\n".join(lines)

            # ── ABSENCE / HOME ───────────────────────────────────────────────
            jours = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]

            if action == "ABSENCE":
                cibles = {cap_id: 60 for cap_id in CAPS_QTITE}
            elif action == "HOME":
                cibles = {}
                for i, cap_id in enumerate(CAPS_QTITE):
                    cibles[cap_id] = 100 if i >= 5 else 80
            else:
                return "❓ Action inconnue"

            async def write_one(i, cap_id):
                pct_val = cibles[cap_id]
                T   = pct_to_temp(pct_val)
                slot_val = json.dumps([[0, T], [0, 0], [0, 0], [0, 0]])
                ok = await write_capability(c, h, dev_id, cap_id, slot_val)
                log(f"BEC write cap{cap_id} ({jours[i]})={pct_val}% ({T}°C) → {'OK' if ok else 'ERR'}")
                return ok

            ok_list = await asyncio.gather(
                *[write_one(i, cap_id) for i, cap_id in enumerate(CAPS_QTITE)]
            )

            # Validation : relecture après écriture
            await asyncio.sleep(3)
            r_check = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}",
                                  headers=h)
            caps_check = {x["capabilityId"]: x["value"] for x in r_check.json()}
            qtite_lines = decode_quantite_semaine(caps_check)
            label = "✈️ <b>BALLON ABSENCE</b>" if action == "ABSENCE" else "🏡 <b>BALLON MAISON</b>"
            return "\n".join([
                f"{label} — validation",
                "", "💧 <b>QUANTITÉ PAR JOUR (valeurs lues)</b>",
            ] + qtite_lines)

        except Exception as e:
            log(f"BEC ERR: {e}"); return f"⚠️ {e}"
