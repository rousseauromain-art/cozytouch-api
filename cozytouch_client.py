import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """Reproduit le 'Flow' d'authentification vu dans Chrome"""
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (Android; 13)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*"
        }
        
        # On utilise un client qui garde les cookies automatiquement pendant la session de login
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # ÉTAPE 1 : Le GET initial pour ouvrir le 'Flow' (récupère le cookie JSESSIONID)
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # ÉTAPE 2 : Le POST avec les identifiants
                payload = {
                    "userId": self.user,
                    "userPassword": self.passwd
                }
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    # On stocke les cookies de la réponse finale (ceux qui contiennent la session valide)
                    self.cookies = res.cookies
                    return True, "Authentification réussie"
                
                return False, f"Échec Login: {res.status_code}"
            except Exception as e:
                return False, f"Erreur réseau: {str(e)}"

    async def get_setup(self):
        if not self.cookies:
            success, msg = await self.login()
            if not success: return {"error": msg}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            # On passe les cookies récupérés à l'étape 2
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": f"Setup Error {res.status_code}"}

    async def send_command(self, device_url, commands):
        if not self.cookies: await self.login()
        payload = {"label": "Koyeb_Action", "actions": [{"deviceURL": device_url, "commands": commands}]}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.post(f"{self.base_url}/exec/apply", json=payload, cookies=self.cookies)
            return res.status_code
