import os
import json
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
CVS_LOGIN_URL = os.getenv("CVS_LOGIN_URL", "https://jobs.cvs.com/")
UHC_LOGIN_URL = os.getenv("UHC_LOGIN_URL", "https://careers.unitedhealthgroup.com/")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_profile(profile_name: str) -> dict:
    path = os.path.join(BASE_DIR, "profiles", f"{profile_name.lower()}.json")
    with open(path) as f:
        return json.load(f)


PROFILES = ["healthcare", "finance", "general"]
