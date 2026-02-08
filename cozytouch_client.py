import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # On teste les deux clusters principaux d'Atlantic
        self.clusters = [
            "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi",
            "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi"
        ]
        self.base_url = self.clusters[0]
        self.cookies = None

    async def login(self):
        """Authentification Overkiz avec gestion de session"""
        headers = {"User-Agent": "Cozytouch/4.3.0", "Content-Type": "application/x-www-form-urlencoded"}
        payload = {"userId": self.user, "userPassword": self.passwd}

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cli:
            for url in self.clusters:
                try:
                    # Initialisation session
                    await cli.get(f"{url}/login", headers=headers)
                    # Tentative de login
                    res = await cli.post(f"{url}/login", data=payload, headers=headers)
                    if res.status_code == 200:
                        self.base_url = url
                        self.cookies = res.cookies
                        return True
                except Exception:
                    continue
            return False

    async def get_setup(self):
        if not self.cookies:
            if not await self.login(): return {"error": "Auth failed"}
        
        async with httpx.AsyncClient(timeout=15.0) as cli:
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            return res.json()

    async def send_command(self, device_url, commands):
        if not self.cookies:
            await self.login()
        url = f"{self.base_url}/exec/apply"
        payload = {
            "label": "Action via API",
            "actions": [{"deviceURL": device_url, "commands": commands}]
        }
        async with httpx.AsyncClient(timeout=15.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code
