import httpx
import asyncio

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # On commence par le cluster 1, le plus fréquent pour Atlantic
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """
        Authentification calquée sur l'application mobile Cozytouch.
        Utilise des cookies de session au lieu de tokens JWT.
        """
        # User-Agent spécifique indispensable pour que le serveur accepte la connexion
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (com.groupe-atlantic.cozytouch; build:1; iOS 16.0.0)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        
        payload = {
            "userId": self.user,
            "userPassword": self.passwd
        }

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cli:
            # On tente d'abord le cluster ha101-1
            try:
                # Étape 1 : On s'assure qu'aucune vieille session ne bloque
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # Étape 2 : Tentative de connexion réelle
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    self.cookies = res.cookies
                    return True
                
                # Étape 3 : Si échec, on tente le cluster ha102-1 (Cluster de secours Atlantic)
                alt_url = "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi"
                res_alt = await cli.post(f"{alt_url}/login", data=payload, headers=headers)
                
                if res_alt.status_code == 200:
                    self.base_url = alt_url
                    self.cookies = res_alt.cookies
                    return True
                    
            except Exception as e:
                print(f"Erreur lors de la tentative de login : {e}")
            
            return False

    async def get_setup(self):
        """Récupère l'état complet du bridge et des radiateurs"""
        if not self.cookies:
            success = await self.login()
            if not success:
                return {"error": "Authentication failed", "details": "Vérifiez vos identifiants CT_USER et CT_PASS sur Koyeb"}
        
        url = f"{self.base_url}/setup"
        async with httpx.AsyncClient(timeout=20.0) as cli:
            try:
                res = await cli.get(url, cookies=self.cookies)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 401: # Session expirée
                    self.cookies = None
                    return await self.get_setup()
                return {"error": f"Serveur Error {res.status_code}", "body": res.text}
            except Exception as e:
                return {"error": "Request failed", "details": str(e)}

    async def send_command(self, device_url, commands):
        """
        Envoie une liste de commandes à un appareil spécifique (deviceURL).
        Format attendu par Overkiz : {"label": "...", "actions": [...]}
        """
        if not self.cookies:
            await self.login()
            
        url = f"{self.base_url}/exec/apply"
        payload = {
            "label": "API_Koyeb_Action",
            "actions": [
                {
                    "deviceURL": device_url,
                    "commands": commands
                }
            ]
        }
        
        async with httpx.AsyncClient(timeout=20.0) as cli:
            try:
                res = await cli.post(url, json=payload, cookies=self.cookies)
                # 200 = OK, 204 = No Content (souvent utilisé pour les succès d'exécution)
                return res.status_code
            except Exception as e:
                print(f"Erreur envoi commande : {e}")
                return 500
