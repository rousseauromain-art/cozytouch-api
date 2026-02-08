import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API - Pilotage Oniris")

# Initialisation du client avec tes variables d'environnement Koyeb
client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug_vars():
    """Vérifie si les variables sont bien chargées sur Koyeb sans les exposer entièrement"""
    user = os.getenv("CT_USER") or ""
    passwd = os.getenv("CT_PASS") or ""
    
    # On affiche juste le début et la fin pour vérifier les erreurs (guillemets, espaces)
    user_display = f"{user[:3]}...{user[-3:]}" if len(user) > 6 else "Format invalide"
    pass_display = f"{passwd[:2]}...{passwd[-2:]}" if len(passwd) > 4 else "Format invalide"
    
    return {
        "user_detecte": user != "",
        "user_apercu": user_display,
        "pass_apercu": pass_display,
        "pass_longueur": len(passwd),
        "serveur_actuel": client.base_url
    }

@app.get("/radiators/discover")
async def discover():
    """Affiche la liste de tes radiateurs et leurs URLs uniques"""
    setup = await client.get_setup()
    
    if isinstance(setup, dict) and "error" in setup:
        return setup
    
    devices = []
    # Overkiz range les appareils dans la liste 'devices'
    for d in setup.get("devices", []):
        ui_class = d.get("uiClass", "")
        # On filtre pour ne garder que les chauffages (Oniris, Adelis)
        if ui_class and ("Heating" in ui_class or "Heater" in ui_class):
            devices.append({
                "nom": d.get("label", "Sans nom"),
                "type": ui_class,
                "url": d.get("deviceURL")
            })
    
    return devices if devices else {"message": "Aucun radiateur trouvé", "raw": setup}

@app.post("/away-all")
async def away_all(temperature: float = 16.0):
    """Bascule tous les radiateurs en mode Manuel à la température choisie (ex: 16°C)"""
    setup = await client.get_setup()
    if isinstance(setup, dict) and "error" in setup:
        return setup
        
    results = []
    for d in setup.get("devices", []):
        ui_class = d.get("uiClass", "")
        if ui_class and ("Heating" in ui_class or "Heater" in ui_class):
            url = d.get("deviceURL")
            
            # Commandes validées par ton mode Debug : Température fixe + mode Manuel
            cmds = [
                {"name": "setTargetTemperature", "parameters": [temperature]},
                {"name": "setOperatingMode", "parameters": ["manual"]}
            ]
            
            status = await client.send_command(url, cmds)
            results.append({"nom": d.get("label"), "statut_http": status})
            
    return {"action": f"Passage à {temperature}°C", "resultats": results}

@app.post("/back-home")
async def back_home():
    """Remet tous les radiateurs en mode Programmation (Planning interne)"""
    setup = await client.get_setup()
    if isinstance(setup, dict) and "error" in setup:
        return setup
        
    results = []
    for d in setup.get("devices", []):
        ui_class = d.get("uiClass", "")
        if ui_class and ("Heating" in ui_class or "Heater" in ui_class):
            url = d.get("deviceURL")
            
            # Paramètre 'internalScheduling' identifié dans tes captures de debug
            cmds = [{"name": "setOperatingMode", "parameters": ["internalScheduling"]}]
            
            status = await client.send_command(url, cmds)
            results.append({"nom": d.get("label"), "statut_http": status})
            
    return {"action": "Reprise de la programmation", "resultats": results}
