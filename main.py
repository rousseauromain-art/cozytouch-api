import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API - Overkiz Bridge V2")

# Initialisation du client
client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug_vars():
    user = os.getenv("CT_USER")
    return {
        "user_configure": user is not None, 
        "python": os.sys.version,
        "serveur_cible": client.base_url
    }

@app.get("/radiators/discover")
async def discover():
    """Détecte tes Oniris et ton Adelis"""
    setup = await client.get_setup()
    
    # Gestion d'erreur si le login a échoué
    if isinstance(setup, dict) and "error" in setup:
        return setup
    
    devices = []
    # Dans Overkiz, les appareils sont dans la clé 'devices'
    raw_devices = setup.get("devices", [])
    
    for d in raw_devices:
        ui_class = d.get("uiClass", "")
        # On filtre pour ne garder que le chauffage
        if ui_class and ("Heating" in ui_class or "Heater" in ui_class):
            devices.append({
                "nom": d.get("label", "Sans nom"),
                "type": ui_class,
                "widget": d.get("widget"),
                "url": d.get("deviceURL")
            })
    
    return devices if devices else {"message": "Aucun radiateur trouvé", "brut": setup}

@app.post("/away-all")
async def away_all(temperature: float = 16.0):
    """Bascule tous les radiateurs à la température choisie"""
    setup = await client.get_setup()
    if isinstance(setup, dict) and "error" in setup:
        return setup
        
    results = []
    raw_devices = setup.get("devices", [])
    
    for d in raw_devices:
        ui_class = d.get("uiClass", "")
        if ui_class and ("Heating" in ui_class or "Heater" in ui_class):
            url = d.get("deviceURL")
            
            # Les commandes validées par ton mode Debug
            cmds = [
                {"name": "setDerogatedTargetTemperature", "parameters": [temperature]},
                {"name": "setOperatingMode", "parameters": ["away"]}
            ]
            
            # On appelle 'send_command' (sans 's') comme défini dans ton client
            status = await client.send_command(url, cmds)
            results.append({
                "nom": d.get("label"), 
                "statut_http": status
            })
            
    return {"action": "Mise en mode absence", "resultats": results}
