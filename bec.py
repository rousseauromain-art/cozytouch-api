"""Module BEC — Chauffe-eau Atlantic/Sauter via API Magellan."""
import json, httpx, psycopg2, time
from datetime import datetime

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
HC_TRANSITIONS = [(1*60+56,True),(7*60+56,False),(14*60+26,True),(16*60+26,False)]

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --- Heures creuses ---
def is_heure_creuse(dt=None):
    m = (dt or datetime.now())
    m = m.hour*60 + m.minute
    return (1*60+56 <= m < 7*60+56) or (14*60+26 <= m < 16*60+26)

def get_hc_label():
    now = datetime.now()
    m = now.hour*60 + now.minute
    if is_heure_creuse(now):
        fin = min((e for e in [7*60+56, 16*60+26] if e > m), default=7*60+56)
        return f"🟢 HC jusqu'à {fin//60:02d}h{fin%60:02d}"
    nxt = min((s for s in [1*60+56, 14*60+26] if s > m), default=1*60+56)
    return f"🔴 HP — prochain HC à {nxt//60:02d}h{nxt%60:02d}"

def minutes_until_next_transition():
    now = datetime.now()
    m = now.hour*60 + now.minute
    all_t = sorted(t for t,_ in HC_TRANSITIONS)
    futures = [t for t in all_t if t > m]
    nxt = min(futures) if futures else (min(all_t) + 24*60)
    return (nxt - m)*60 - now.second

# --- DB ---
def init_bec_table(db_url):
    if not db_url: return
    try:
        conn = psycopg2.connect(db_url); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS bec_transitions (
            id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            index_kwh FLOAT NOT NULL, heure_creuse BOOLEAN NOT NULL);""")
        conn.commit(); cur.close(); conn.close()
    except Exception as e: log(f"BEC DB ERR: {e}")

def save_transition(db_url, index_kwh, heure_creuse):
    if not db_url: return
    try:
        conn = psycopg2.connect(db_url); cur = conn.cursor()
        cur.execute("INSERT INTO bec_transitions (index_kwh,heure_creuse) VALUES (%s,%s)",
                    (index_kwh, heure_creuse))
        conn.commit(); cur.close(); conn.close()
        log(f"BEC transition: {index_kwh:.3f} kWh HC={heure_creuse}")
    except Exception as e: log(f"BEC save ERR: {e}")

def get_conso_stats(db_url, jours=7):
    if not db_url: return None
    try:
        conn = psycopg2.connect(db_url); cur = conn.cursor()
        cur.execute("""SELECT timestamp,index_kwh,heure_creuse FROM bec_transitions
            WHERE timestamp > NOW()-INTERVAL '%s days' ORDER BY timestamp""", (jours,))
        rows = cur.fetchall(); cur.close(); conn.close()
        if len(rows) < 2: return None
        hc = hp = 0.0; nb = 0
        for i in range(len(rows)-1):
            diff = rows[i+1][1] - rows[i][1]
            if diff < 0: continue
            if rows[i][2]: hc += diff
            else: hp += diff
            nb += 1
        return hc, hp, nb
    except Exception as e: log(f"BEC stats ERR: {e}"); return None

# --- Helpers ---
def find_water_heater(devices):
    keywords = ["chauffe","aqueo","water","ballon","phazy","sauter","calypso","aeromax","explorer"]
    for d in devices:
        if any(k in str(d.get("name","")).lower() for k in keywords):
            return d
    return devices[0] if len(devices) == 1 else None

def decode_planning(raw):
    jours = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim","J8","J9","J10"]
    modes = {0:"Manuel", 3:"Eco+", 4:"Prog/HC"}
    lines = []
    try:
        if isinstance(raw, str): raw = json.loads(raw)
    except: return [f"  Erreur: {raw}"]
    for i, slot in enumerate(raw):
        try:
            d,f,_,mode = int(slot[0]),int(slot[1]),slot[2],int(slot[3])
            if d==0 and f==255: lines.append(f"  {jours[i]}: toute la journée [{modes.get(mode,mode)}]")
            elif d==255:         lines.append(f"  {jours[i]}: inactif")
            else:                lines.append(f"  {jours[i]}: {d//60:02d}h{d%60:02d}→{f//60:02d}h{f%60:02d} [{modes.get(mode,mode)}]")
        except: lines.append(f"  Slot{i}: {slot}")
    return lines or ["  (vide)"]

# --- Auth & index ---
async def bec_authenticate(bec_user, bec_pass):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{ATLANTIC_API}/users/token",
            headers={"Authorization":f"Basic {CLIENT_BASIC}","Content-Type":"application/x-www-form-urlencoded"},
            data={"grant_type":"password","scope":"openid",
                  "username":f"GA-PRIVATEPERSON/{bec_user}","password":bec_pass}, timeout=12)
        if r.status_code == 200: return r.json().get("access_token")
        log(f"BEC Auth {r.status_code}: {r.text[:100]}"); return None

async def bec_get_index(bec_user, bec_pass):
    token = await bec_authenticate(bec_user, bec_pass)
    if not token: return None
    h = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
        if r.status_code != 200: return None
        aqueo = find_water_heater(r.json()[0].get("devices",[]))
        if not aqueo: return None
        r2 = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={aqueo['deviceId']}", headers=h)
        if r2.status_code != 200: return None
        return float({cap["capabilityId"]:cap["value"] for cap in r2.json()}.get(59,0)) / 1000

# --- Action principale ---
async def manage_bec(action, bec_user, bec_pass, db_url):
    if not bec_user or not bec_pass: return "❌ BEC_EMAIL/BEC_PASSWORD manquants"
    token = await bec_authenticate(bec_user, bec_pass)
    if not token: return "❌ Auth Magellan échouée"
    h = {"Authorization":f"Bearer {token}","Content-Type":"application/json"}

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(f"{ATLANTIC_API}/magellan/cozytouch/setupviewv2", headers=h)
            if r.status_code != 200: return f"❌ Setup {r.status_code}"
            setup = r.json()[0]; setup_id = setup.get("id")
            aqueo = find_water_heater(setup.get("devices",[]))
            if not aqueo:
                return f"❓ Introuvable. Devices: {[d.get('name') for d in setup.get('devices',[])]}"
            dev_id = aqueo.get("deviceId")

            if action == "GET":
                r2 = await c.get(f"{ATLANTIC_API}/magellan/capabilities/?deviceId={dev_id}", headers=h)
                caps = {x["capabilityId"]:x["value"] for x in r2.json()}
                log(f"BEC caps: {caps}")
                idx    = float(caps.get(59,0))/1000
                nom_w  = int(float(caps.get(164,0)))  # puissance nominale de la résistance (fixe)
                res99  = str(caps.get(99,"0"))         # 0=OFF, 1=ON → VRAIE chauffe active
                temp   = float(caps.get(22,0))
                hc     = is_heure_creuse()
                mmap   = {0:"Manuel",3:"Eco+",4:"Prog HC/HP"}
                mode   = mmap.get(int(float(caps.get(87,0))),"?")
                boost  = "🟢 ON" if str(caps.get(165,"0")) not in ("0","false") else "OFF"
                amap   = {0:"🏡 Normal",1:"✈️ Activé",2:"⏳ En attente"}
                absent = amap.get(int(float(caps.get(227,0))),"?")
                try:
                    ts = json.loads(str(caps.get(222,"[0,0]"))) if isinstance(caps.get(222),str) else caps.get(222,[0,0])
                    dates = f"{datetime.fromtimestamp(int(ts[0])).strftime('%d/%m %Hh%M')}→{datetime.fromtimestamp(int(ts[1])).strftime('%d/%m %Hh%M')}" if ts and int(ts[0])>0 else "aucune"
                except: dates="?"
                # cap99=résistance ON/OFF = indicateur fiable de chauffe active
                if res99 != "0":
                    chauffe = f"🔥 CHAUFFE ({nom_w}W) — {'✅ HC' if hc else '⚠️ HP'}"
                    resist  = "🟢 ON — chauffe active"
                else:
                    chauffe = f"💤 En veille"
                    resist  = f"🔴 OFF  (puissance nominale : {nom_w}W)"
                return "\n".join([
                    f"💧 <b>{aqueo.get('name','Chauffe-eau')}</b> (id={dev_id})",
                    "","⚡ <b>ÉTAT</b>",
                    f"  {chauffe}",f"  Consigne:{temp:.0f}°C  Mode:{mode}",
                    f"  Résistance:{resist}  Boost:{boost}",f"  {get_hc_label()}",
                    "","📊 <b>CONSO</b>",
                    f"  Index total (cap59): <b>{idx:.3f} kWh</b>",
                    f"  Index partiel (cap168): {float(caps.get(168,0))/1000:.3f} kWh",
                    "","✈️ <b>ABSENCE</b>",f"  {absent}  Période:{dates}",
                    "","📅 <b>PLANNING cap150</b>",
                ] + decode_planning(caps.get(150,[])) + [
                    "","🔧 <b>CAPS NON DOCUMENTÉS</b>",
                    f"  cap188={caps.get(188,'?')}  cap218={caps.get(218,'?')}",
                    f"  cap223={caps.get(223,'?')}  cap224={caps.get(224,'?')}",
                    f"  cap225={caps.get(225,'?')}  cap228={caps.get(228,'?')}  cap230={caps.get(230,'?')}",
                ])

            if action == "STATS":
                stats = get_conso_stats(db_url)
                if not stats: return "⚠️ Pas encore assez de données.\nRevenez après quelques transitions HC/HP."
                hc_k, hp_k, nb = stats; total = hc_k+hp_k
                pct = (hc_k/total*100) if total>0 else 0
                return "\n".join(["📊 <b>CONSO BALLON — 7J</b>",
                    f"🟢 HC : <b>{hc_k:.2f} kWh</b> ({pct:.0f}%)",
                    f"🔴 HP : <b>{hp_k:.2f} kWh</b> ({100-pct:.0f}%)",
                    f"⚡ Total : <b>{total:.2f} kWh</b>  <i>({nb} périodes)</i>"])

            payload = {k:setup[k] for k in ("address","area","currency","mainHeatingEnergy",
                "mainDHWEnergy","name","numberOfPersons","numberOfRooms","setupBuildingDate","type") if k in setup}
            if action == "ABSENCE":
                now_ts = int(datetime.now().timestamp())
                payload["absence"] = {"startDate":now_ts,"endDate":now_ts+30*24*3600}
            elif action == "HOME":
                payload["absence"] = {}
            else:
                return "❓ Action inconnue"
            r = await c.put(f"{ATLANTIC_API}/magellan/v2/setups/{setup_id}", json=payload, headers=h)
            log(f"BEC {action}: {r.status_code}")
            return ("✅ Absence activée (30j)" if action=="ABSENCE" else "✅ Mode normal restauré") if r.status_code in (200,204) else f"❌ {r.status_code}"

        except Exception as e:
            log(f"BEC exception: {e}"); return f"⚠️ {str(e)}"
