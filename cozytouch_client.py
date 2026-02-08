import os, time
import httpx
import urllib.parse

GA_TOKEN_URL = "https://apis.groupe-atlantic.com/token"
GA_JWT_URL   = "https://apis.groupe-atlantic.com/magellan/accounts/jwt"
GA_BASIC_AUTH = "Basic Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

UA_COZYTOUCH = "Cozytouch/2.10.0 (iPhone; iOS 15.0; Scale/3.00)"

class CozytouchClient:
    def __init__(self, user, passwd, timeout=20.0):
        self.user = user
        self.passwd = passwd
        self.timeout = timeout
        self._oauth, self._jwt, self._jwt_exp = None, None, 0

    async def _oauth_token(self):
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            data = {
                "grant_type": "password",
                "username": "GA-PRIVATEPERSON/rousseau.romain@gmail.com",
                "password": "Cozyius8nei9235!"
            }
            headers = {
                "Authorization": GA_BASIC_AUTH,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": UA_COZYTOUCH
            }
            
            # Appel API
            r = await cli.post(GA_TOKEN_URL, data=data, headers=headers)
            
            if r.status_code == 403:
                r = await cli.post(GA_TOKEN_URL, json=data, headers=headers)

            if r.status_code != 200:
                return {"status": "Echec Atlantic", "code": r.status_code, "reponse": r.text}
                
            return r.json()

    async def _jwt_token(self, access_token: str):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": UA_COZYTOUCH
        }
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.get(GA_JWT_URL, headers=headers)
            r.raise_for_status()
            if r.headers.get("content-type","").startswith("application/json"):
                return r.json().get("token")
            return r.text

    async def token(self):
        now = time.time()
        if (not self._oauth) or now >= self._jwt_exp - 60:
            res = await self._oauth_token()
            # Si on a reçu le dictionnaire d'erreur au lieu du token
            if isinstance(res, dict) and "status" in res:
                return res
            self._oauth = res
            self._jwt = await self._jwt_token(self._oauth["access_token"])
            self._jwt_exp = now + int(self._oauth.get("expires_in", 3600))
        return self._jwt

    async def _ga(self, method, url, **kw):
        jwt = await self.token()
        if isinstance(jwt, dict): return jwt # Propager l'erreur
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {jwt}"
        headers["User-Agent"] = UA_COZYTOUCH
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            r = await cli.request(method, url, headers=headers, **kw)
            r.raise_for_status()
            if r.headers.get("content-type","").startswith("application/json"):
                return r.json()
            return r.text

    async def get_setup(self):
        for path in [
            "https://apis.groupe-atlantic.com/magellan/setup",
            "https://apis.groupe-atlantic.com/magellan/v4/setup",
            "https://apis.groupe-atlantic.com/magellan/registered/setup",
        ]:
            try: return await self._ga("GET", path)
            except Exception: continue
        raise RuntimeError("Setup Cozytouch introuvable (API)")

    async def send_commands(self, device_url: str, commands: list[dict]):
        payload = {"label":"Cozytouch API","actions":[{"deviceURL":device_url,"commands":commands}]}
        for path in [
            "https://apis.groupe-atlantic.com/magellan/exec/apply",
            "https://apis.groupe-atlantic.com/magellan/v4/exec/apply",
        ]:
            try: return await self._ga("POST", path, json=payload)
            except Exception: continue
        raise RuntimeError("Impossible d'envoyer les commandes (exec/apply)")

    @staticmethod
    def iter_devices(setup: dict):
        if "devices" in setup: yield from setup["devices"]
        else:
            for p in setup.get("places", []):
                for d in p.get("devices", []): yield d

    @staticmethod
    def is_radiator(dev: dict) -> bool:
        text = (dev.get("uiClass","") + dev.get("widget","") + dev.get("controllableName",""))
        return ("ElectricalHeater" in text) or ("Heater" in text)

    @staticmethod
    def states_map(dev: dict) -> dict:
        arr = dev.get("states") or dev.get("attributes") or []
        out = {}
        for s in arr:
            n = s.get("name") or s.get("key")
            out[n] = s.get("value")
        return out
