import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        # Serveur principal pour les comptes Atlantic Cozytouch
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        """
        Tentative de connexion Overkiz (Bridge V2).
        Cette méthode est la seule capable de voir tes radiateurs io-homecontrol.
        """
        # User-Agent officiel de l'application Cozytouch Android (très robuste)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Cozytouch/4.3.0 (Android; 13)",
            "Accept": "application/json, text/plain, */*"
        }
        
        payload = {
            "userId": self.user,
            "userPassword": self.passwd
        }

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cli:
            try:
                # 1. On initialise la session avec un GET pour obtenir un cookie de base
                await cli.get(f"{self.base_url}/login", headers=headers)
                
                # 2. On poste les identifiants
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    self.cookies = res.cookies
                    return True, "Success"
                
                # 3. Si échec sur le serveur 1, tentative sur le serveur 2 (Cluster alternatif)
                alt_url = "https://ha102-1.overkiz.com/enduser-mobile-web/enduserapi"
                res_alt = await cli.post(f"{alt_url}/login", data=payload, headers=headers)
                
                if res_alt.status_code == 200:
                    self.base_url = alt_url
                    self.cookies = res_alt.cookies
                    return True, "Success (Cluster 2)"
                
                return False, f"Refusé (Code {res.status_code})"

            except Exception as e:
                return False, f"Erreur réseau : {str(e)}"

    async def get_setup(self):
        """Récupère la configuration complète (Oniris, Adelis, etc.)"""
        if not self.cookies:
            success, message = await self.login()
            if not success:
                return {"error": "Authentication Overkiz Failed", "details": message}
        
        async with httpx.AsyncClient(timeout=25.0) as cli:
            try:
                res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 401:
                    self.cookies = None # Reset session si expiré
                    return await self.get_setup()
                return {"error": f"Erreur setup {res.status_code}", "body": res.text}
            except Exception as e:
                return {"error": "Exception setup", "details": str(e)}

    async def send_command(self, device_url, commands):
        """Envoie l'ordre (ex: 16°C Manuel)"""
        if not self.cookies:
            await self.login()
            
        url = f"{self.base_url}/exec/apply"
        payload = {
            "label": "Action via API",
            "actions": [
                {
                    "deviceURL": device_url,
                    "commands": commands
                }
            ]
        }
        
        async with httpx.AsyncClient(timeout=20.0) as cli:
            try:
                res = await cli.post(url, json=payload, cookies=self.cookies)
                return res.status_code
            except Exception as e:
                print(f"Erreur envoi : {e}")
                return 500
