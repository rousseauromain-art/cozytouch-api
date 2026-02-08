import httpx
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.cookies = None
        self.base_url = None
        
        # Les 4 "portes" possibles pour Atlantic/Overkiz
        self.endpoints = [
            "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi",
            "https://ha101-1.overkiz.com/enduserapi",
            "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi",
            "https://ha102-1.overkiz.com/enduserapi"
        ]

    async def login(self):
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (Android; 13)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }
        payload = {"userId": self.user, "userPassword": self.passwd}

        results = []
        for url in self.endpoints:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as cli:
                    # Tentative de handshake
                    await cli.get(f"{url}/login", headers=headers)
                    res = await cli.post(f"{url}/login", data=payload, headers=headers)
                    
                    if res.status_code == 200:
                        self.base_url = url
                        self.cookies = res.cookies
                        logger.info(f"Porte trouvée : {url}")
                        return True, f"Success on {url}"
                    
                    results.append(f"{url} -> {res.status_code}")
            except Exception as e:
                results.append(f"{url} -> Erreur: {str(e)}")

        return False, " | ".join(results)

    async def get_setup(self):
        """Cette fonction va maintenant nous servir de rapport de scan"""
        success, report = await self.login()
        if not success:
            return {
                "error": "Toutes les portes ont échoué",
                "details": report,
                "note": "Si tout est en 404/401, Atlantic bloque peut-être l'IP de Koyeb"
            }
        
        # Si on a trouvé une porte, on récupère le setup
        async with httpx.AsyncClient(timeout=20.0) as cli:
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            return {
                "message": "Porte trouvée !",
                "url_valide": self.base_url,
                "data": res.json()
            }

    async def send_command(self, device_url, commands):
        if not self.cookies: await self.login()
        url = f"{self.base_url}/exec/apply"
        async with httpx.AsyncClient(timeout=20.0) as cli:
            res = await cli.post(url, json={"actions": [{"deviceURL": device_url, "commands": commands}]}, cookies=self.cookies)
            return res.status_code
