import os, time
import httpx
import urllib.parse

GA_TOKEN_URL = "https://apis.groupe-atlantic.com/token"
GA_JWT_URL   = "https://apis.groupe-atlantic.com/magellan/accounts/jwt"
# Clé d'autorisation officielle de l'application mobile
GA_BASIC_AUTH = "Basic Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
UA_COZYTOUCH = "Cozytouch/2.10.0 (iPhone; iOS 15.0; Scale/3.00)"

class CozytouchClient:
    def __init__(self, user, passwd, timeout=20.0):
        # On garde les variables, mais on va utiliser les valeurs en dur dans _oauth_token
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
            
            try:
                r = await cli.post(GA_TOKEN_URL, data=data, headers=headers)
                if r.status_code != 200:
                    return {"error": "Auth Failed", "code": r.status_code, "body": r.text}
                return r.json()
            except Exception as e:
                return {"error": "Request Exception", "detail": str(e)}

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
            # Si res est un dictionnaire d'erreur
            if isinstance(res, dict) and "error" in res:
                return res
            self._oauth = res
            self._jwt = await self._jwt_token(self._oauth["access_token"])
            self._jwt_exp =
