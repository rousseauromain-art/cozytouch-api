import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # URL confirmée par tes captures (ha101-1 est le bon cluster)
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """
        Simule le comportement exact d'un navigateur ou de l'app officielle.
        L'utilisation d'un seul 'cli' avec gestion de cookies est cruciale.
        """
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*"
        }
        
        payload = {
            "userId": self.user,
            "userPassword": self.passwd
        }

        # On utilise un client unique pour TOUT le processus de login
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # Étape 1: On 'réveille' la session (GET)
                # C'est ici que le serveur nous donne un ticket d'entrée (JSESSIONID)
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # Étape 2: On envoie les identifiants (POST)
                # Le client 'cli' renvoie automatiquement le ticket reçu à l'étape 1
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    self.cookies = res.cookies
                    logger.info("Connexion réussie : Session établie.")
                    return True, "Success"
                
                logger.error(f"Echec Login: Code {res.status_code}")
                return False, f"Refusé (Code {res.status_code})"

            except Exception as e:
                logger.error(f"Erreur réseau login: {e}")
                return False, str(e)

    async def get_setup(self):
        """Récupère tes équipements (Oniris, Adelis)"""
        if not self.cookies:
            success, msg = await self.login()
            if not success:
                return {"error": "Auth failed", "details": msg}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            try:
                # On utilise les cookies de la session validée
                res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
                if res.status_code == 200:
                    return res.json()
                return {"error": f"Setup error {res.status_code}", "body": res.text}
            except Exception as e:
                return {"error": "Request failed", "details": str(e)}

    async def send_command(self, device_url, commands):
        """Envoie une commande (ex: passer à 19°C)"""
        if not self.cookies:
            await self.login()
            
        url = f"{self.base_url}/exec/apply"
        payload = {
            "label": "Koyeb_Action",
            "actions": [{"deviceURL": device_url, "commands": commands}]
        }
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.post(url, json=payload, cookies=self.cookies)
            return res.status_code
