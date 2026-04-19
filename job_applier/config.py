import os
import json
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES = ["healthcare", "finance", "general"]


def load_profile(profile_name: str) -> dict:
    path = os.path.join(BASE_DIR, "profiles", f"{profile_name.lower()}.json")
    with open(path) as f:
        return json.load(f)
