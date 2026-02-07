import os, json, redis
from fastapi import FastAPI, Header, HTTPException, Query
from cozytouch_client import CozytouchClient

# Configuration des Secrets
API_KEY = os.getenv("API_KEY")
CT_USER = os.getenv("CT_USER")
CT_PASS = os.getenv("CT_PASS")
REDIS_URL = os.getenv("REDIS_URL")

if not (API_KEY and CT_USER and CT_PASS):
    raise SystemExit("Erreur : Variables d'environnement manquantes sur Render")

app = FastAPI(title="Cozytouch Micro-API Redis")

# Connexion à la base de données Render Redis
db = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

def _auth(auth: str | None):
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization Bearer")
    if auth.split(" ",1)[1] != API_KEY:
        raise HTTPException(401, "Invalid API key")

def _load():
    if not db: return {}
    data = db.get("radiator_storage")
    return json.loads(data) if data else {}

def _save(d: dict):
    if db:
        db.set("radiator_storage", json.dumps(d))

@app.get("/radiators/discover")
async def discover(authorization: str | None = Header(default=None)):
    _auth(authorization)
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    out = []
    for d in cli.iter_devices(setup):
        if not CozytouchClient.is_radiator(d): continue
        st = CozytouchClient.states_map(d)
        out.append({
            "label": d.get("label"),
            "deviceURL": d.get("deviceURL"),
            "operating_mode": st.get("core:OperatingModeState"),
            "heating_level": st.get("io:TargetHeatingLevelState"),
            "target_temp": st.get("core:TargetTemperatureState"),
            "comfort_room_temp": st.get("io:ComfortRoomSetpoint", st.get("core:ComfortRoomTemperature")),
            "eco_room_temp": st.get("io:EcoRoomSetpoint", st.get("core:EcoRoomTemperature")),
        })
    return {"ok": True, "radiators": out}

@app.post("/radiator/snapshot")
async def snapshot(authorization: str | None = Header(default=None),
                   device_url: str = Query(..., description="deviceURL du radiateur à sauvegarder")):
    _auth(authorization)
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    dev = next((d for d in cli.iter_devices(setup) if d.get("deviceURL")==device_url), None)
    if not dev: raise HTTPException(404, "Radiateur introuvable")
    st = CozytouchClient.states_map(dev)
    store = _load()
    store[device_url] = {
        "label": dev.get("label"),
        "states": {k: st.get(k) for k in [
            "core:OperatingModeState","io:TargetHeatingLevelState","core:TargetTemperatureState",
            "core:DerogationActiveState","core:DerogationEndDateTimeState",
        ] if k in st}
    }
    _save(store)
    return {"ok": True, "saved_for": device_url, "snapshot": store[device_url]}

@app.post("/radiator/restore")
async def restore(authorization: str | None = Header(default=None),
                  device_url: str = Query(..., description="deviceURL du radiateur à restaurer")):
    _auth(authorization)
    store = _load()
    snap = store.get(device_url)
    if not snap: raise HTTPException(404, "Aucun snapshot pour ce device_url")

    cmds, st = [], snap["states"]
    if st.get("core:OperatingModeState"):
        cmds.append({"name":"setOperatingMode","parameters":[st["core:OperatingModeState"]]})
    if st.get("core:OperatingModeState")=="basic" and st.get("core:TargetTemperatureState") is not None:
        cmds.append({"name":"setTargetTemperature","parameters":[float(st["core:TargetTemperatureState"])]})
    if st.get("io:TargetHeatingLevelState"):
        cmds.append({"name":"setTargetHeatingLevel","parameters":[st["io:TargetHeatingLevelState"]]})
    if not cmds: raise HTTPException(400,"Snapshot incomplet : aucune commande applicable")

    cli = CozytouchClient(CT_USER, CT_PASS)
    res = await cli.send_commands(device_url, cmds)
    return {"ok": True, "applied": cmds, "resp": res}

@app.post("/radiators/program_eco")
async def program_eco(authorization: str | None = Header(default=None)):
    _auth(authorization)
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    results = []
    for d in cli.iter_devices(setup):
        if not CozytouchClient.is_radiator(d): continue
        url = d.get("deviceURL")
        cmds = [
            {"name":"setOperatingMode","parameters":["internal"]},
            {"name":"setTargetHeatingLevel","parameters":["eco"]},
        ]
        try:
            r = await cli.send_commands(url, cmds)
            results.append({"deviceURL": url, "ok": True})
        except Exception as e:
            results.append({"deviceURL": url, "ok": False, "error": str(e)})

    return {"ok": True, "count": len(results), "results": results}

