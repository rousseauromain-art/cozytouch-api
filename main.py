import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API Lite")

# On récupère les variables CT_USER et CT_PASS configurées sur Koyeb
client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/test-auth")
async def test_auth():
    return await client.token()

@app.get("/radiators/discover")
async def discover():
    setup = await client.get_setup()
    if isinstance(setup, dict) and "error" in setup:
        return setup
    
    devices = []
    for d in client.iter_devices(setup):
        if client.is_radiator(d):
            states = client.states_map(d)
            devices.append({
                "label": d.get("label"),
                "url": d.get("deviceURL"),
                "current_temp": states.get("core:TargetTemperatureState"),
                "mode": states.get("core:OperatingModeState")
            })
    return devices

@app.post("/radiators/away")
async def set_away(device_url: str, temperature: float = 16.0):
    """Active le mode absence (hors-gel dérogé) à la température choisie"""
    commands = [
        {"name": "setDerogatedTargetTemperature", "parameters": [temperature]},
        {"name": "setOperatingMode", "parameters": ["away"]}
    ]
    return await client.send_commands(device_url, commands)

@app.post("/radiators/basic")
async def set_basic(device_url: str):
    """Repasse le radiateur en mode normal (Basic)"""
    return await client.send_commands(device_url, [{"name": "setOperatingMode", "parameters": ["basic"]}])
