import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # Nouvelle structure d'URL pour éviter la 404
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """Authentification avec gestion de session persistante"""
        # Utilisation d'un User-Agent générique mais propre
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Cozytouch/4.3.0 (Android; 13)"
        }
        
        payload = {
            "userId": self.user,
            "userPassword": self.passwd
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # Tentative de login directe
                login_url = f"{self.base_url}/login"
                res = await cli.post(login_url, data=payload, headers=headers)
                
                if res.status_code == 200:
                    self.cookies = res.cookies
                    logger.info("Connexion Overkiz réussie")
                    return True, "Success"
                
                # Si 404 ou 401, on tente le cluster ha102
                if res.status_code in [404, 401]:
                    alt_url = "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi"
                    res_alt = await cli.post(f"{alt_url}/login", data=payload, headers=headers)
                    if res_alt.status_code == 200:
                        self.base_url = alt_url
                        self.cookies = res_alt.cookies
                        return True, "Success (Cluster 2)"
                
                return False, f"Refusé (Code {res.status_code})"

            except Exception as e:
                return False, f"Erreur connexion : {str(e)}"

    async def get_setup(self):
        """Récupération de tes Oniris"""
        if not self.cookies:
            success, msg = await self.login()
            if not success:
                return {"error": "Auth failed", "details": msg}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            # L'appel au setup ne doit PAS avoir /login dans l'URL
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": f"Erreur {res.status_code}", "body": res.text}

    async def send_command(self, device_url, commands):
        """Envoi d'ordre (16°C, etc.)"""
        if not self.cookies: await self.login()
        url = f"{self.base_url}/exec/apply"
        payload = {"label": "Action", "actions": [{"deviceURL": device_url, "commands": commands}]}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code
