import httpx
import logging

# Configuration du logging pour voir ce qui se passe dans les logs Koyeb
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # Serveurs Overkiz pour Atlantic
        self.clusters = [
            "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi",
            "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi"
        ]
        self.base_url = self.clusters[0]
        self.cookies = None

    async def login(self):
        """
        Authentification calquée sur pyoverkiz (Home Assistant).
        Simule un appareil iOS pour éviter le blocage 'Cloud'.
        """
        headers = {
            "User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9"
        }
        
        payload = {
            "userId": self.user,
            "userPassword": self.passwd
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            for url in self.clusters:
                try:
                    logger.info(f"Tentative de connexion sur {url}...")
                    
                    # 1. On initialise la session (Handshake)
                    await cli.get(f"{url}/login", headers=headers)
                    
                    # 2. On envoie les identifiants
                    res = await cli.post(f"{url}/login", data=payload, headers=headers)
                    
                    if res.status_code == 200:
                        self.cookies = res.cookies
                        self.base_url = url
                        logger.info(f"Connexion réussie sur {url}")
                        return True, "Success"
                    else:
                        logger.warning(f"Echec sur {url}: Code {res.status_code}")
                        
                except Exception as e:
                    logger.error(f"Erreur réseau sur {url}: {str(e)}")
                    continue
            
            return False, "Tous les serveurs Overkiz ont refusé la connexion"

    async def get_setup(self):
        """Récupère la liste des équipements (Setup)"""
        if not self.cookies:
            success, msg = await self.login()
            if not success:
                return {"error": "Authentication Failed", "details": msg}
        
        headers = {"User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)"}
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            try:
                res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies, headers=headers)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code in [401, 403]:
                    # Session expirée ou rejetée, on tente de se reconnecter une fois
                    self.cookies = None
                    return await self.get_setup()
                return {"error": f"Erreur serveur {res.status_code}", "body": res.text}
            except Exception as e:
                return {"error": "Requête Setup échouée", "details": str(e)}

    async def send_command(self, device_url, commands):
        """Exécute une commande sur un appareil"""
        if not self.cookies:
            await self.login()
            
        headers = {"User-Agent": "Cozytouch/4.3.0 (iPhone; iOS 16.0; Scale/3.00)"}
        url = f"{self.base_url}/exec/apply"
        
        payload = {
            "label": "Action_Koyeb",
            "actions": [
                {
                    "deviceURL": device_url,
                    "commands": commands
                }
            ]
        }
        
        async with httpx.AsyncClient(timeout=30.0) as cli:
            try:
                res = await cli.post(url, json=payload, cookies=self.cookies, headers=headers)
                return res.status_code
            except Exception as e:
                logger.error(f"Erreur lors de l'envoi de la commande: {e}")
                return 500
