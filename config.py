"""Configuration partagée entre tous les modules."""
import os, time
from pyoverkiz.const import SUPPORTED_SERVERS

VERSION = "15.5"

TOKEN            = os.getenv("TELEGRAM_TOKEN")
OVERKIZ_EMAIL    = os.getenv("OVERKIZ_EMAIL")
OVERKIZ_PASSWORD = os.getenv("OVERKIZ_PASSWORD")
DB_URL           = os.getenv("DATABASE_URL")
BEC_USER         = os.getenv("BEC_EMAIL")
BEC_PASS         = os.getenv("BEC_PASSWORD")
SHELLY_TOKEN     = os.getenv("SHELLY_TOKEN")
SHELLY_ID        = os.getenv("SHELLY_ID")
SHELLY_SERVER    = os.getenv("SHELLY_SERVER", "shelly-209-eu.shelly.cloud")

ATLANTIC_API = "https://apis.groupe-atlantic.com"
CLIENT_BASIC = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
MY_SERVER    = SUPPORTED_SERVERS["atlantic_cozytouch"]

# Transitions HC/HP (minutes depuis minuit, booléen = HC après transition)
HC_TRANSITIONS = [
    ( 0*60+56, True),   # 00:56 → HC commence
    ( 6*60+26, False),  # 06:26 → HP commence
    (14*60+26, True),   # 14:26 → HC commence
    (16*60+56, False),  # 16:56 → HP commence
]

# Radiateurs
CONFORT_VALS = {
    "14253355#1": {"name": "Salon",          "temp": 19.5, "eco": 16.0},
    "190387#1":   {"name": "Chambre",         "temp": 19.0, "eco": 16.0},
    "1640746#1":  {"name": "Bureau",          "temp": 18.0, "eco": 15.0},
    "4326513#1":  {"name": "Sèche-Serviette", "temp": 19.5, "eco": 16.0},
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
