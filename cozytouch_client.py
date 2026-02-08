import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # URL spécifique identifiée dans tes tests Overkiz
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """Authentification Overkiz avec gestion de session vide préalable"""
        headers = {
            "User-Agent": "Cozytouch/4.3.0",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cli:
            # Étape 1 : On va sur le login juste pour récupérer un cookie de session vide
            await cli.get(f"{self.base_url}/login", headers=headers)
            
            # Étape 2 : On envoie les vrais identifiants
            payload = {"userId": self.user, "userPassword": self.passwd}
            res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
            
            if res.status_code == 200:
                self.cookies = res.cookies
                return True
            else:
                # Debug : On veut savoir ce que dit le serveur
                print(f"Erreur login : {res.status_code} - {res.text}")
                return False

        async with httpx.AsyncClient(timeout=15.0) as cli:
            res = await cli.post(url, data=payload, headers=headers)
            if res.status_code == 200:
                self.cookies = res.cookies
                return True
            return False

    async def get_setup(self):
        """Récupère la liste complète des appareils (Oniris, Adelis, etc.)"""
        if not self.cookies:
            await self.login()
        
        url = f"{self.base_url}/setup"
        async with httpx.AsyncClient(timeout=15.0) as cli:
            res = await cli.get(url, cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": "Impossible de récupérer le setup", "code": res.status_code}

    async def send_command(self, device_url, commands):
        """Envoie une commande spécifique à un appareil"""
        if not self.cookies:
            await self.login()
            
        url = f"{self.base_url}/exec/apply"
        # Structure de données spécifique à l'API Overkiz
        payload = {
            "label": "Action via API Koyeb",
            "actions": [
                {
                    "deviceURL": device_url,
                    "commands": commands
                }
            ]
        }
        
        async with httpx.AsyncClient(timeout=15.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code

