import os, json, redis
from fastapi import FastAPI, Header, HTTPException, Query, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cozytouch_client import CozytouchClient

# --- CONFIGURATION DES SECRETS ---
API_KEY = os.getenv("API_KEY")
CT_USER = os.getenv("CT_USER")
CT_PASS = os.getenv("CT_PASS")
REDIS_URL = os.getenv("REDIS_URL")

if not (API_KEY and CT_USER and CT_PASS):
    raise SystemExit("Erreur : Les variables API_KEY, CT_USER ou CT_PASS sont manquantes.")

# --- INITIALISATION ---
app = FastAPI(
    title="Cozytouch Micro-API Redis",
    description="Pilotez vos radiateurs Cozytouch depuis votre mobile"
)

# Sécurité pour Swagger (le cadenas)
security = HTTPBearer()

# Connexion à Redis
db = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

# --- FONCTIONS UTILITAIRES ---
def _verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Vérifie le token Bearer saisi dans le cadenas ou envoyé par mobile."""
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide")
    return credentials.credentials

def _load():
    if not db: return {}
    data = db.get("radiator_snapshot_storage")
    return json.loads(data) if data else {}

def _save(d: dict):
    if db:
        db.set("radiator_snapshot_storage", json.dumps(d))

# --- ROUTES API ---

@app.get("/", tags=["Système"])
async def root():
    return {
        "status": "online", 
        "database": "connected" if db else "offline (REDIS_URL manquante)"
    }

@app.get("/radiators/discover", tags=["Radiateurs"])
async def discover(token: str = Depends(_verify_token)):
    """1 - Liste tous les radiateurs et leur état actuel"""
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    out = []
    for d in cli.iter_devices(setup):
        if not CozytouchClient.is_radiator(d): continue
        st = CozytouchClient.states_map(d)
        out.append({
            "label": d.get("label"),
            "deviceURL": d.get("deviceURL"),
            "mode": st.get("core:OperatingModeState"),
            "temp_actuelle": st.get("core:TargetTemperatureState")
        })
    return {"radiators": out}

@app.post("/radiators/set_16_degrees", tags=["Actions"])
async def set_16_all(token: str = Depends(_verify_token)):
    """2 - Force tous les radiateurs à 16°C (Mode Manuel)"""
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    results = []
    for d in cli.iter_devices(setup):
        if not CozytouchClient.is_radiator(d): continue
        url = d.get("deviceURL")
        # Passage en mode 'basic' pour imposer strictement 16.0
        cmds = [
            {"name": "setOperatingMode", "parameters": ["basic"]},
            {"name": "setTargetTemperature", "parameters": [16.0]}
        ]
        try:
            await cli.send_commands(url, cmds)
            results.append({"label": d.get("label"), "status": "16°C OK"})
        except Exception as e:
            results.append({"label": d.get("label"), "status": "Erreur", "msg": str(e)})
    return {"ok": True, "results": results}

@app.post("/radiators/save_current_state", tags=["Sauvegarde"])
async def save_all(token: str = Depends(_verify_token)):
    """Sauvegarde le programme actuel de tous les radiateurs dans Redis"""
    cli = CozytouchClient(CT_USER, CT_PASS)
    setup = await cli.get_setup()
    store = {}
    for d in cli.iter_devices(setup):
        if not CozytouchClient.is_radiator(d): continue
        url = d.get("deviceURL")
        st = CozytouchClient.states_map(d)
        store[url] = {
            "label": d.get("label"),
            "states": {k: st.get(k) for k in [
                "core:OperatingModeState","io:TargetHeatingLevelState","core:TargetTemperatureState"
            ] if k in st}
        }
    _save(store)
    return {"ok": True, "message": "État sauvegardé pour tous les radiateurs"}

@app.post("/radiators/restore_program", tags=["Sauvegarde"])
async def restore_all(token: str = Depends(_verify_token)):
    """3 - Remet tous les radiateurs sur le programme sauvegardé"""
    store = _load()
    if not store: 
        raise HTTPException(404, "Aucune sauvegarde trouvée dans Redis")
    
    cli = CozytouchClient(CT_USER, CT_PASS)
    results = []
    for url, data in store.items():
        cmds, st = [], data["states"]
        if st.get("core:OperatingModeState"):
            cmds.append({"name":"setOperatingMode","parameters":[st["core:OperatingModeState"]]})
        if st.get("core:OperatingModeState")=="basic" and st.get("core:TargetTemperatureState") is not None:
            cmds.append({"name":"setTargetTemperature","parameters":[float(st["core:TargetTemperatureState"])]})
        if st.get("io:TargetHeatingLevelState"):
            cmds.append({"name":"setTargetHeatingLevel","parameters":[st["io:TargetHeatingLevelState"]]})
        
        try:
            await cli.send_commands(url, cmds)
            results.append({"label": data["label"], "status": "Restauré"})
        except:
            results.append({"label": data["label"], "status": "Échec"})
    return {"ok": True, "results": results}

@app.get("/test-auth", tags=["Debug"])
async def test_auth(token: str = Depends(_verify_token)):
    cli = CozytouchClient(CT_USER, CT_PASS)
    try:
        jwt = await cli.token()
        return {"status": "Authentification réussie", "token_prefix": jwt[:15]}
    except Exception as e:
        return {"status": "Erreur d'identifiants", "detail": str(e)}

@app.post("/radiators/away-mode")
async def set_away_16(device_url: str):
    # On définit d'abord la température de dérogation (16°C)
    # Puis on bascule en mode absence (away)
    commands = [
        {
            "name": "setDerogatedTargetTemperature", 
            "parameters": [16.0]
        },
        {
            "name": "setOperatingMode", 
            "parameters": ["away"] 
        }
    ]
    return await client.send_commands(device_url, commands)
