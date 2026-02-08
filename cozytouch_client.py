import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        
        # L'ASTUCE : Utiliser un seul client pour TOUTE la phase de login
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # 1. On ouvre le "Flow" (le serveur nous donne un cookie JSESSIONID)
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # 2. On envoie les identifiants (le client cli renvoie automatiquement le cookie reçu à l'étape 1)
                payload = {
                    "userId": self.user,
                    "userPassword": self.passwd
                }
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    # On sauvegarde les cookies pour les futurs appels (setup, etc.)
                    self.cookies = res.cookies
                    return True, "Success"
                
                return False, f"Erreur {res.status_code}"
            except Exception as e:
                return False, str(e)

    async def get_setup(self):
        if not self.cookies:
            success, msg = await self.login()
            if not success: return {"error": msg}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": f"Setup code {res.status_code}"}

    async def send_command(self, device_url, commands):
        if not self.cookies: await self.login()
        url = f"{self.base_url}/exec/apply"
        payload = {"label": "Action", "actions": [{"deviceURL": device_url, "commands": commands}]}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code
