import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API - Debug Mode")

client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug_vars():
    user = os.getenv("CT_USER")
    return {"user_detecte": user is not None, "python": os.sys.version}

@app.get("/test-auth")
async def test_auth():
    return await client.token()

@app.get("/radiators/discover")
async def discover():
    setup = await client.get_setup()
    devices = []
    
    # On itère sur tous les appareils sans aucun filtre au début
    for d in client.iter_devices(setup):
        ui_class = d.get("uiClass")
        widget = d.get("widget")
        label = d.get("label", "Sans nom")
        
        # On enregistre tout pour le debug
        devices.append({
            "nom": label,
            "type": ui_class,
            "widget": widget,
            "url": d.get("deviceURL")
        })
    
    return devices if devices else {"message": "Aucun appareil trouvé dans le setup", "raw": setup}

@app.post("/away-all")
async def away_all(temperature: float = 16.0):
    setup = await client.get_setup()
    results = []
    for d in client.iter_devices(setup):
        # On force l'envoi à tous les appareils de type 'Heater' ou 'Radiator'
        if any(x in str(d.get("uiClass")) for x in ["Heating", "Heater"]):
            url = d.get("deviceURL")
            cmd = [
                {"name": "setDerogatedTargetTemperature", "parameters": [temperature]},
                {"name": "setOperatingMode", "parameters": ["away"]}
            ]
            res = await client.send_commands(url, cmd)
            results.append({"nom": d.get("label"), "res": res})
    return results

