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
            if isinstance(res, dict) and "error" in res:
                return res
            self._oauth = res
            self._jwt = await self._jwt_token(self._oauth["access_token"])
            # Correction de la ligne coupée :
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
        # On teste les 3 URLs connues pour parer aux erreurs 500
        paths = [
            "https://apis.groupe-atlantic.com/magellan/setup",
            "https://apis.groupe-atlantic.com/magellan/v4/setup",
            "https://apis.groupe-atlantic.com/magellan/registered/setup",
        ]
        last_err = None
        for path in paths:
            res = await self._ga("GET", path)
            if isinstance(res, dict) and "error" in res:
                last_err = res
                continue
            return res
        return {"error": "All setup URLs failed", "last_detail": last_err}

    async def send_commands(self, device_url: str, commands: list[dict]):
        payload = {"label":"Cozytouch API","actions":[{"deviceURL":device_url,"commands":commands}]}
        # Test des endpoints d'exécution
        for path in ["https://apis.groupe-atlantic.com/magellan/exec/apply", "https://apis.groupe-atlantic.com/magellan/v4/exec/apply"]:
            res = await self._ga("POST", path, json=payload)
            if isinstance(res, dict) and "error" in res: continue
            return res
        return {"error": "Failed to send commands"}
