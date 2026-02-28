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
# DB — transitions HC/HP (avec température eau)
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
        hc_k = hp_k = 0.0; nb = 0
        chutes = []
        for i in range(len(rows) - 1):
            _, is1, hc1, t1 = rows[i]
            _, ie2, hc2, t2 = rows[i + 1]
            diff = ie2 - is1
            if diff < 0: continue
            if hc1: hc_k += diff
            else:   hp_k += diff
            # Chute de température pendant HP (t1=début HP, t2=fin HP)
            if not hc1 and t1 is not None and t2 is not None:
                chutes.append(t1 - t2)  # positif = refroidissement
            nb += 1
        chute_moy = sum(chutes) / len(chutes) if chutes else None
        return hc_k, hp_k, nb, chute_moy
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
    """Retourne (index_kwh, temp_eau_haut) pour les transitions."""
    token = await bec_authenticate()
    if not token:
        return None, None
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
        if r.status_code != 200:
            return None, None
        dev = find_water_heater(r.json()[0].get("devices", []))
        if not dev:
            return None, None
        r2 = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev['deviceId']}", headers=h)
        if r2.status_code != 200:
            return None, None
        caps = {x["capabilityId"]: x["value"] for x in r2.json()}
        idx  = float(caps.get(59, 0)) / 1000
        # Température représentative : haut du ballon (cap266), sinon milieu (cap265)
        t_raw = caps.get(266, caps.get(265, None))
        temp  = float(t_raw) if t_raw is not None else None
        return idx, temp


# ---------------------------------------------------------------------------
# ACTION PRINCIPALE
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
                return f"❓ Non trouvé. Devices: {[d.get('name') for d in setup.get('devices',[])]}"
            dev_id = dev.get("deviceId")

            if action == "GET":
                r2   = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=h)
                caps = {x["capabilityId"]: x["value"] for x in r2.json()}
                log(f"BEC caps: {caps}")

                # État chauffe
                nom_w = int(float(caps.get(164, 0)))
                res99 = str(caps.get(99, "0"))
                temp_c = float(caps.get(22, 0))
                idx   = float(caps.get(59, 0)) / 1000
                hc    = is_heure_creuse()
                mode  = {0:"Manuel",3:"Eco+",4:"Prog HC/HP"}.get(int(float(caps.get(87,0))), "?")
                boost = "🟢 ON" if str(caps.get(165,"0")) not in ("0","false") else "OFF"

                if res99 != "0":
                    chauffe = f"🔥 CHAUFFE ({nom_w}W) — {'✅ HC' if hc else '⚠️ HP'}"
                    resist  = "🟢 ON — chauffe active"
                else:
                    chauffe = "💤 En veille"
                    resist  = f"🔴 OFF  (nominale : {nom_w}W)"

                # Températures eau (cap265=milieu, cap266=haut, cap267=bas)
                t_haut   = caps.get(266)
                t_milieu = caps.get(265)
                t_bas    = caps.get(267)
                t_cond   = caps.get(264)  # condenseur si PAC

                def fmt_t(v): return f"{float(v):.1f}°C" if v is not None else "—"

                # Disponibilité eau chaude
                v40_dispo  = caps.get(268)   # litres à 40°C disponibles
                v40_total  = caps.get(270)   # capacité totale à 40°C
                pct_dispo  = caps.get(271)   # % eau chaude dispo
                cap_tank   = caps.get(258)   # volume cuve en litres

                def fmt_v(v): return f"{float(v):.0f}L" if v is not None else "—"
                def fmt_p(v): return f"{float(v):.0f}%" if v is not None else "—"

                # Absence
                absent = {0:"🏡 Normal",1:"✈️ Activé",2:"⏳ En attente"}.get(int(float(caps.get(227,0))), "?")
                try:
                    ts    = caps.get(222, "[0,0]")
                    tsl   = json.loads(str(ts)) if isinstance(ts, str) else ts
                    dates = (f"{datetime.fromtimestamp(int(tsl[0])).strftime('%d/%m %Hh%M')}"
                             f"→{datetime.fromtimestamp(int(tsl[1])).strftime('%d/%m %Hh%M')}"
                             ) if tsl and int(tsl[0]) > 0 else "aucune"
                except:
                    dates = "?"

                return "\n".join([
                    f"💧 <b>{dev.get('name','Chauffe-eau')}</b> (id={dev_id})",
                    "", "⚡ <b>ÉTAT</b>",
                    f"  {chauffe}",
                    f"  Consigne : <b>{temp_c:.0f}°C</b>  Mode : <b>{mode}</b>",
                    f"  Résistance : {resist}  |  Boost : {boost}",
                    f"  {get_hc_label()}",
                    "", "🌡️ <b>TEMPÉRATURES EAU</b>",
                    f"  Haut   (cap266) : <b>{fmt_t(t_haut)}</b>",
                    f"  Milieu (cap265) : <b>{fmt_t(t_milieu)}</b>",
                    f"  Bas    (cap267) : <b>{fmt_t(t_bas)}</b>",
                    "", "💦 <b>DISPONIBILITÉ</b>",
                    f"  Eau dispo à 40°C : <b>{fmt_v(v40_dispo)}</b> / {fmt_v(v40_total)}",
                    f"  Taux dispo       : <b>{fmt_p(pct_dispo)}</b>",
                    f"  Capacité cuve    : {fmt_v(cap_tank)}",
                    "", "📊 <b>CONSO</b>",
                    f"  Index total  (cap59)  : <b>{idx:.3f} kWh</b>",
                    f"  Index partiel(cap168) : {float(caps.get(168,0))/1000:.3f} kWh",
                    "", "✈️ <b>ABSENCE</b>",
                    f"  {absent}  |  Période : {dates}",
                ] + (["", "📅 <b>PLANNING cap150</b>"] + decode_planning(caps.get(150, []))))

            if action == "STATS":
                s = get_conso_stats(7)
                if not s:
                    return "⚠️ Pas encore assez de données (4 relevés/jour aux transitions HC/HP)."
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
                    lines.append(f"🌡️ Chute temp. en HP : <b>−{chute:.1f}°C</b> en moyenne")
                return "\n".join(lines)

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
