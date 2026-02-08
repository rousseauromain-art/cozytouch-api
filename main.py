import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API - Production")

# Initialisation du client
client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug_vars():
    """Vérifie que Koyeb transmet bien les accès"""
    user = os.getenv("CT_USER")
    return {
        "user_detecte": user is not None,
        "python_version": os.sys.version
    }

@app.get("/test-auth")
async def test_auth():
    """Affiche le token JWT (ton test réussi !)"""
    return await client.token()

@app.get("/radiators/discover")
async def discover():
    """Liste tous tes radiateurs et leurs URLs"""
    setup = await client.get_setup()
    if isinstance(setup, dict) and "error" in setup:
        return setup
    
    devices = []
    for d in client.iter_devices(setup):
        if client.is_radiator(d):
            states = client.states_map(d)
            devices.append({
                "nom": d.get("label"),
                "url": d.get("deviceURL"),
                "temperature_actuelle": states.get("core:TargetTemperatureState"),
                "mode_actuel": states.get("core:OperatingModeState")
            })
    return devices

@app.post("/radiators/away")
async def set_away(device_url: str, temperature: float = 16.0):
    """Active le mode absence à 16°C sans toucher au mode Confort"""
    commands = [
        {"name": "setDerogatedTargetTemperature", "parameters": [temperature]},
        {"name": "setOperatingMode", "parameters": ["away"]}
    ]
    return await client.send_commands(device_url, commands)

@app.post("/radiators/basic")
async def set_basic(device_url: str):
    """Repasse le radiateur en mode programmation/normal"""
    return await client.send_commands(device_url, [{"name": "setOperatingMode", "parameters": ["basic"]}])
