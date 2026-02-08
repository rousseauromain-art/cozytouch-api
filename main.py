import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Mon API Chauffage Atlantic")

# Initialisation du client avec tes variables Koyeb
client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug():
    return {"user_configuré": os.getenv("CT_USER") is not None}

@app.get("/radiators/discover")
async def discover():
    """Affiche tous les radiateurs détectés pour vérifier que le pont fonctionne"""
    setup = await client.get_setup()
    if "error" in setup:
        return setup
    
    devices = []
    for d in setup.get("devices", []):
        # On filtre pour n'afficher que les radiateurs et sèche-serviettes
        ui_class = d.get("uiClass", "")
        if "Heating" in ui_class or "Heater" in ui_class:
            devices.append({
                "nom": d.get("label"),
                "type": ui_class,
                "url": d.get("deviceURL")
            })
    return devices

@app.post("/away-all")
async def set_away_all(temp: float = 16.0):
    """L'ACTION FINALE : Met tous les radiateurs à 16°C en mode Absence"""
    setup = await client.get_setup()
    if "error" in setup: return setup
    
    results = []
    for d in setup.get("devices", []):
        ui_class = d.get("uiClass", "")
        if "Heating" in ui_class or "Heater" in ui_class:
            device_url = d.get("deviceURL")
            
            # Les commandes identifiées via ton mode Debug Overkiz
            commands = [
                {"name": "setDerogatedTargetTemperature", "parameters": [temp]},
                {"name": "setOperatingMode", "parameters": ["away"]}
            ]
            
            status = await client.send_command(device_url, commands)
            results.append({"nom": d.get("label"), "code_retour": status})
            
    return {"message": "Ordre envoyé à la maison", "details": results}

@app.post("/back-to-auto")
async def back_to_auto():
    """Optionnel : Repasse tout en mode auto via l'API (si tu ne veux pas utiliser l'app)"""
    setup = await client.get_setup()
    results = []
    for d in setup.get("devices", []):
        if "Heating" in d.get("uiClass", ""):
            commands = [{"name": "setOperatingMode", "parameters": ["auto"]}]
            status = await client.send_command(d.get("deviceURL"), commands)
            results.append({"nom": d.get("label"), "status": status})
    return results
