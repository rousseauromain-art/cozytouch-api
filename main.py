import os
from fastapi import FastAPI
from cozytouch_client import CozytouchClient

app = FastAPI()

client = CozytouchClient(
    user=os.getenv("CT_USER"),
    passwd=os.getenv("CT_PASS")
)

@app.get("/debug-vars")
async def debug_vars():
    user = os.getenv("CT_USER")
    password = os.getenv("CT_PASS")
    
    return {
        "CT_USER_detecte": user is not None,
        "CT_USER_valeur": user if user else "NON TROUVE",
        "CT_PASS_detecte": password is not None,
        "CT_PASS_longueur": len(password) if password else 0,
        "python_version": os.sys.version
    }

@app.get("/test-auth")
async def test_auth():
    try:
        return await client.token()
    except Exception as e:
        return {"error": "Le script a crashé", "details": str(e)}
