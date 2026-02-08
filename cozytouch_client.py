import httpx

class CozytouchClient:
    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.base_url = "https://ha101-1.overkiz.com/enduser-mobile-web/enduserapi"
        self.cookies = None

    async def login(self):
        # On imite EXACTEMENT un iPhone pour passer sous les radars
        headers = {
            "Host": "ha101-1.overkiz.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "User-Agent": "Cozytouch/4.3.0 (com.groupe-atlantic.cozytouch; build:1; iOS 16.5.0) Alamofire/5.4.3",
            "Accept-Language": "fr-FR;q=1.0",
            "Connection": "keep-alive"
        }
        
        payload = {"userId": self.user, "userPassword": self.passwd}

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            try:
                # Le secret : on ne fait PAS de GET avant, on fonce direct sur le POST
                res = await cli.post(f"{self.base_url}/login", data=payload, headers=headers)
                
                if res.status_code == 200:
                    self.cookies = res.cookies
                    return True, "Success"
                return False, f"Status {res.status_code}"
            except Exception as e:
                return False, str(e)

    async def get_setup(self):
        if not self.cookies: await self.login()
        headers = {"User-Agent": "Cozytouch/4.3.0 (com.groupe-atlantic.cozytouch; build:1; iOS 16.5.0)"}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            res = await cli.get(f"{self.base_url}/setup", cookies=self.cookies, headers=headers)
            return res.json() if res.status_code == 200 else {"error": res.status_code}
