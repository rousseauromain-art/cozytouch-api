import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # CHANGEMENT : On passe sur l'API EndUser directe (sans le tag mobile)
        self.base_url = "https://ha101-1.overkiz.com/enduserapi"
        self.cookies = None

    async def login(self):
        # On simule un navigateur Chrome classique, pas une App
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # ÉTAPE 1 : On force la création d'une session
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # ÉTAPE 2 : On poste le login
                payload = {"userId": self.user, "userPassword": self.passwd}
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                # Si 404 ici, c'est que le cluster ha101 est saturé ou bloqué pour Koyeb
                if res.status_code == 200:
                    self.cookies = res.cookies
                    return True, "Connecté"
                
                return False, f"Erreur {res.status_code}"
            except Exception as e:
                return False, str(e)

    async def get_setup(self):
        if not self.cookies: await self.login()
        async with httpx.AsyncClient(timeout=30.0) as cli:
            # On demande le setup sur l'API standard
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": f"Status {res.status_code}"}
