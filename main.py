import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI(title="Cozytouch API - Full House")

client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/radiators/discover")
async def discover():
    """Utile pour vérifier ce que le script voit"""
    setup = await client.get_setup()
    return [d.get("label") for d in client.iter_devices(setup) if client.is_radiator(d)]

@app.post("/away-all")
async def set_away_all(temperature: float = 16.0):
    """
    Scénario : 16°C pour TOUS les radiateurs détectés.
    Active le mode 'Away' qui sera visible sur l'application Cozytouch.
    """
    setup = await client.get_setup()
    results = []
    
    # On boucle sur tous les appareils pour trouver les radiateurs
    for d in client.iter_devices(setup):
        if client.is_radiator(d):
            device_url = d.get("deviceURL")
            name = d.get("label", "Inconnu")
            
            # Envoi de la commande de 16°C en mode Away
            commands = [
                {"name": "setDerogatedTargetTemperature", "parameters": [temperature]},
                {"name": "setOperatingMode", "parameters": ["away"]}
            ]
            res = await client.send_commands(device_url, commands)
            results.append({"radiateur": name, "status": "OK" if "error" not in str(res) else "Erreur"})
            
    return {"message": "Commande envoyée à toute la maison", "details": results}
