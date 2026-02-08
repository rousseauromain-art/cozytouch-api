import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """
        Reproduit le 'handshake' Home Assistant : 
        1. GET pour ouvrir la session
        2. POST pour s'authentifier
        """
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*"
        }

        # L'utilisation d'un SEUL client pour les deux étapes est obligatoire
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # ÉTAPE 1 : On demande l'accès (crée le JSESSIONID sur le serveur)
                # Sans cela, le POST suivant renverra TOUJOURS une 404
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # ÉTAPE 2 : On envoie les identifiants
                payload = {
                    "userId": self.user,
                    "userPassword": self.passwd
                }
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    # On extrait et sauvegarde les cookies de session validée
                    self.cookies = res.cookies
                    logger.info("Session Cozytouch validée avec succès")
                    return True, "Success"
                
                logger.error(f"Échec authentification : {res.status_code}")
                return False, f"Refusé (Code {res.status_code})"
                
            except Exception as e:
                logger.error(f"Erreur de connexion : {e}")
                return False, str(e)

    async def get_setup(self):
        """Récupère tes Oniris et Adelis"""
        if not self.cookies:
            success, msg = await self.login()
            if not success: return {"error": "Auth failed", "details": msg}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
            if res.status_code == 200:
                return res.json()
            return {"error": f"Erreur Setup {res.status_code}", "body": res.text}

    async def send_command(self, device_url, commands):
        if not self.cookies: await self.login()
        url = f"{self.base_url}/exec/apply"
        payload = {"label": "Koyeb_Action", "actions": [{"deviceURL": device_url, "commands": commands}]}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code
