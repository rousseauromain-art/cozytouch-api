import os, time, httpx

GA_TOKEN_URL = "https://apis.groupe-atlantic.com/token"
GA_JWT_URL   = "https://apis.groupe-atlantic.com/magellan/accounts/jwt"
GA_BASIC_AUTH = "Basic Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
UA_COZYTOUCH = "Cozytouch/2.10.0 (iPhone; iOS 15.0; Scale/3.00)"

class CozytouchClient:
    def __init__(self, user, passwd, timeout=20.0):
        self.user = user
        self.passwd = passwd
        self.timeout = timeout
        # On stocke les tokens en mémoire vive (RAM) au lieu de Redis
        self._oauth, self._jwt, self._jwt_exp = None, None, 0

    async def _oauth_token(self):
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            data = {
                "grant_type": "password",
                "username": f"GA-PRIVATEPERSON/{self.user}",
                "password": self.passwd
            }
            headers = {
                "Authorization": GA_BASIC_AUTH,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": UA_COZYTOUCH
            }
            try:
                r = await cli.post(GA_TOKEN_URL, data=data, headers=headers)
                if r.status_code != 200:
                    return {"error": "Auth Failed", "code": r.status_code, "body": r.text}
                return r.json()
            except Exception as e:
                return {"error": "Connection Error", "detail": str(e)}

    async def _jwt_token(self, access_token: str):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": UA_COZYTOUCH
        }
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.get(GA_JWT_URL, headers=headers)
            r.raise_for_status()
            
            # Sécurité : on vérifie si c'est du JSON avant de faire .get()
            try:
                data = r.json()
                if isinstance(data, dict):
                    return data.get("token", r.text)
                return r.text
            except Exception:
                # Si ce n'est pas du JSON (simple texte), on renvoie le texte brut
                return r.text

    async def token(self):
        now = time.time()
        # Si pas de token ou expire dans moins de 60s, on renouvelle
        if (not self._oauth) or now >= self._jwt_exp - 60:
            res = await self._oauth_token()
            if isinstance(res, dict) and "error" in res:
                return res
            self._oauth = res
            self._jwt = await self._jwt_token(self._oauth["access_token"])
            self._jwt_exp = now + int(self._oauth.get("expires_in", 3600))
        return self._jwt

    async def _ga(self, method, url, **kw):
        jwt = await self.token()
        if isinstance(jwt, dict) and "error" in jwt:
            return jwt
        
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {jwt}"
        headers["User-Agent"] = UA_COZYTOUCH
        
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.request(method, url, headers=headers, **kw)
            if r.status_code >= 400:
                return {"error": f"API Error {r.status_code}", "url": url, "body": r.text}
            return r.json() if "application/json" in r.headers.get("content-type", "") else r.text

    async def get_setup(self):
        url = "https://apis.groupe-atlantic.com/magellan/v1/setup"
        
        # On utilise un client qui gère les cookies automatiquement
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as cli:
            # 1. On récupère le token
            token = await self.token()
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": "Cozytouch/4.3.0 (com.groupe-atlantic.cozytouch; build:1; iOS 16.0.0) Alamofire/5.4.3"
            }
            
            # 2. On tente l'appel
            r = await cli.get(url, headers=headers)
            
            # Si le corps est vide ou [], on tente une version alternative
            if r.status_code == 200 and (not r.text or r.text == "[]"):
                # Plan B : On tente sans le 'v1' si la réponse était vide
                alt_url = "https://apis.groupe-atlantic.com/magellan/setup"
                r = await cli.get(alt_url, headers=headers)

            try:
                return r.json()
            except:
                return {"error": "Réponse non JSON", "body": r.text, "code": r.status_code}
    
    async def send_commands(self, device_url: str, commands: list[dict]):
        payload = {"label":"API-Control","actions":[{"deviceURL":device_url,"commands":commands}]}
        return await self._ga("POST", "https://apis.groupe-atlantic.com/magellan/exec/apply", json=payload)

    @staticmethod
    def iter_devices(setup: dict):
        if not isinstance(setup, dict): return
        if "devices" in setup: yield from setup["devices"]
        else:
            for p in setup.get("places", []):
                for d in p.get("devices", []): yield d

    @staticmethod
    def is_radiator(dev: dict) -> bool:
        text = (dev.get("uiClass","") + dev.get("widget","") + dev.get("controllableName",""))
        return any(x in text for x in ["Heater", "Radiator", "Heating"])

    @staticmethod
    def states_map(dev: dict) -> dict:
        out = {}
        for s in (dev.get("states") or []):
            out[s.get("name")] = s.get("value")
        return out




