"""Module BEC — Ballon eau chaude Atlantic/Sauter via API Magellan."""
import json, httpx, psycopg2
from datetime import datetime
from config import ATLANTIC_API, CLIENT_BASIC, BEC_USER, BEC_PASS, DB_URL, HC_TRANSITIONS, log


# ---------------------------------------------------------------------------
# HEURES CREUSES
# ---------------------------------------------------------------------------
def is_heure_creuse(dt: datetime = None) -> bool:
    if dt is None:
        dt = datetime.now()
    m = dt.hour * 60 + dt.minute
    return (1*60+56 <= m < 7*60+56) or (14*60+26 <= m < 16*60+26)

def get_hc_label() -> str:
    now = datetime.now()
    m   = now.hour * 60 + now.minute
    if is_heure_creuse(now):
        ends = [7*60+56, 16*60+26]
        fin  = min((e for e in ends if e > m), default=7*60+56)
        return f"🟢 HC jusqu'à {fin//60:02d}h{fin%60:02d}"
    starts    = [1*60+56, 14*60+26]
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
def save_transition(index_kwh: float, heure_creuse: bool):
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("INSERT INTO bec_transitions (index_kwh, heure_creuse) VALUES (%s,%s)",
                    (index_kwh, heure_creuse))
        conn.commit(); cur.close(); conn.close()
        log(f"Transition BEC : {index_kwh:.3f} kWh HC={heure_creuse}")
    except Exception as e:
        log(f"Transition save ERR: {e}")

def get_conso_stats(jours: int = 7):
    if not DB_URL:
        return None
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""SELECT timestamp, index_kwh, heure_creuse
            FROM bec_transitions WHERE timestamp > NOW() - INTERVAL '%s days'
            ORDER BY timestamp ASC""", (jours,))
        rows = cur.fetchall(); cur.close(); conn.close()
        if len(rows) < 2:
            return None
        hc_k = hp_k = 0.0; nb = 0
        for i in range(len(rows) - 1):
            _, is1, hc1 = rows[i]
            _, ie2, _   = rows[i + 1]
            diff = ie2 - is1
            if diff < 0: continue
            if hc1: hc_k += diff
            else:   hp_k += diff
            nb += 1
        return hc_k, hp_k, nb
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

def decode_planning(raw) -> list[str]:
    jours    = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim","J8","J9","J10"]
    mode_map = {0:"Manuel", 3:"Eco+", 4:"Prog HC/HP"}
    lines    = []
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
    except:
        return [f"  Erreur: {raw}"]
    for i, s in enumerate(raw):
        try:
            dm, fm, _, mode = int(s[0]), int(s[1]), s[2], int(s[3])
            m = mode_map.get(mode, str(mode))
            if dm == 0 and fm == 255:
                lines.append(f"  {jours[i]}: toute la journée [{m}]")
            elif dm == 255:
                lines.append(f"  {jours[i]}: inactif")
            else:
                lines.append(f"  {jours[i]}: {dm//60:02d}h{dm%60:02d}→{fm//60:02d}h{fm%60:02d} [{m}]")
        except Exception as e:
            lines.append(f"  Slot{i}: {s} ({e})")
    return lines or ["  (vide)"]


# ---------------------------------------------------------------------------
# AUTH — utilise les globals de config.py, pas de paramètres
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

async def bec_get_index() -> float | None:
    """Lit uniquement l'index kWh (pour les transitions HC/HP auto)."""
    token = await bec_authenticate()
    if not token:
        return None
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
        if r.status_code != 200:
            return None
        dev = find_water_heater(r.json()[0].get("devices", []))
        if not dev:
            return None
        r2 = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev['deviceId']}", headers=h)
        if r2.status_code != 200:
            return None
        caps = {x["capabilityId"]: x["value"] for x in r2.json()}
        return float(caps.get(59, 0)) / 1000


# ---------------------------------------------------------------------------
# ACTION PRINCIPALE — pas de paramètres, utilise config.py
# ---------------------------------------------------------------------------
async def manage_bec(action="GET"):
    if not BEC_USER or not BEC_PASS:
        return "❌ BEC_EMAIL ou BEC_PASSWORD manquants dans les variables d'env"
    token = await bec_authenticate()
    if not token:
        return "❌ Auth Magellan échouée"
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
            if r.status_code != 200:
                return f"❌ Setup {r.status_code}"
            setup    = r.json()[0]
            setup_id = setup.get("id")
            dev      = find_water_heater(setup.get("devices", []))
            if not dev:
                return f"❓ Chauffe-eau non trouvé. Devices: {[d.get('name') for d in setup.get('devices',[])]}"
            dev_id = dev.get("deviceId")

            if action == "GET":
                r2   = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=h)
                caps = {x["capabilityId"]: x["value"] for x in r2.json()}
                # Aussi logger les caps statiques du device dans setupviewv2
                dev_caps_static = {x["capabilityId"]: x["value"] for x in dev.get("capabilities", [])}
                dev_tags        = dev.get("tags", [])
                log(f"BEC caps dynamiques: {caps}")
                log(f"BEC caps statiques (setupviewv2): {dev_caps_static}")
                log(f"BEC tags: {dev_tags}")
                log(f"BEC device keys: {list(dev.keys())}")

                nom_w  = int(float(caps.get(164, 0)))  # puissance nominale (fixe)
                res99  = str(caps.get(99, "0"))         # 0=OFF, 1=ON = vraie chauffe
                temp   = float(caps.get(22, 0))
                idx    = float(caps.get(59, 0)) / 1000
                hc     = is_heure_creuse()
                mode   = {0:"Manuel",3:"Eco+",4:"Prog HC/HP"}.get(int(float(caps.get(87,0))), "?")
                boost  = "🟢 ON" if str(caps.get(165,"0")) not in ("0","false") else "OFF"
                absent = {0:"🏡 Normal",1:"✈️ Activé",2:"⏳ En attente"}.get(int(float(caps.get(227,0))), "?")
                try:
                    ts    = caps.get(222, "[0,0]")
                    tsl   = json.loads(str(ts)) if isinstance(ts, str) else ts
                    dates = (f"{datetime.fromtimestamp(int(tsl[0])).strftime('%d/%m %Hh%M')}"
                             f"→{datetime.fromtimestamp(int(tsl[1])).strftime('%d/%m %Hh%M')}"
                             ) if tsl and int(tsl[0]) > 0 else "aucune"
                except:
                    dates = "?"

                if res99 != "0":
                    chauffe = f"🔥 CHAUFFE ({nom_w}W) — {'✅ HC' if hc else '⚠️ HP'}"
                    resist  = "🟢 ON — chauffe active"
                else:
                    chauffe = "💤 En veille"
                    resist  = f"🔴 OFF  (nominale : {nom_w}W)"

                return "\n".join([
                    f"💧 <b>{dev.get('name','Chauffe-eau')}</b> (id={dev_id})",
                    "", "⚡ <b>ÉTAT</b>",
                    f"  {chauffe}",
                    f"  Consigne : <b>{temp:.0f}°C</b>  Mode : <b>{mode}</b>",
                    f"  Résistance : {resist}  |  Boost : {boost}",
                    f"  {get_hc_label()}",
                    "", "📊 <b>CONSO</b>",
                    f"  Index total  (cap59)  : <b>{idx:.3f} kWh</b>",
                    f"  Index partiel(cap168) : {float(caps.get(168,0))/1000:.3f} kWh",
                    "", "✈️ <b>ABSENCE</b>",
                    f"  {absent}  |  Période : {dates}",
                    "", "📅 <b>PLANNING cap150</b>",
                ] + decode_planning(caps.get(150, [])) + [
                    "", "🔧 <b>CAPS NON DOCUMENTÉS (dynamiques)</b>",
                    f"  cap188={caps.get(188,'?')}  cap218={caps.get(218,'?')}",
                    f"  cap223={caps.get(223,'?')}  cap224={caps.get(224,'?')}",
                    f"  cap225={caps.get(225,'?')}  cap228={caps.get(228,'?')}  cap230={caps.get(230,'?')}",
                    "", "🗂 <b>CAPS STATIQUES (setupviewv2)</b>",
                    f"  IDs: {sorted(dev_caps_static.keys())}",
                    f"  Tags: {dev_tags[:3] if dev_tags else 'aucun'}",
                ])

            if action == "STATS":
                s = get_conso_stats(7)
                if not s:
                    return "⚠️ Pas encore assez de données (4 relevés/jour aux transitions HC/HP)."
                hc_k, hp_k, nb = s
                tot = hc_k + hp_k
                pct = (hc_k / tot * 100) if tot > 0 else 0
                return "\n".join([
                    "📊 <b>CONSO BALLON — 7 JOURS</b>",
                    f"🟢 Heures Creuses : <b>{hc_k:.2f} kWh</b> ({pct:.0f}%)",
                    f"🔴 Heures Pleines : <b>{hp_k:.2f} kWh</b> ({100-pct:.0f}%)",
                    f"⚡ Total : <b>{tot:.2f} kWh</b>  |  <i>{nb} périodes</i>",
                ])

            # Payload commun ABSENCE / HOME
            payload = {k: setup[k] for k in
                       ("address","area","currency","mainHeatingEnergy","mainDHWEnergy",
                        "name","numberOfPersons","numberOfRooms","setupBuildingDate","type")
                       if k in setup}
            if action == "ABSENCE":
                now = int(datetime.now().timestamp())
                payload["absence"] = {"startDate": now, "endDate": now + 30*24*3600}
            elif action == "HOME":
                payload["absence"] = {}
            else:
                return "❓ Action inconnue"

            r = await c.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}", json=payload, headers=h)
            log(f"BEC {action}: {r.status_code}")
            labels = {"ABSENCE": "✅ Mode absence activé (30j)", "HOME": "✅ Ballon mode normal"}
            return labels[action] if r.status_code in (200, 204) else f"❌ {r.status_code}"

        except Exception as e:
            log(f"BEC ERR: {e}"); return f"⚠️ {e}"
