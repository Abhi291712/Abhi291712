import asyncio
import logging
import os
import sys
from colorama import Fore, Style, init
from config import load_profile, PROFILES, EMAIL, PASSWORD
from sites import SITES

init(autoreset=True)

os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "apply.log")),
    ],
)


def pick_one(title: str, options: list[str]) -> str:
    print(f"\n{Fore.CYAN}{title}{Style.RESET_ALL}")
    for i, opt in enumerate(options, 1):
        print(f"  {Fore.YELLOW}{i}.{Style.RESET_ALL} {opt}")
    while True:
        choice = input("Enter number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("Invalid choice.")


def pick_many(title: str, options: list[str]) -> list[str]:
    print(f"\n{Fore.CYAN}{title}{Style.RESET_ALL}")
    for i, opt in enumerate(options, 1):
        print(f"  {Fore.YELLOW}{i}.{Style.RESET_ALL} {opt}")
    print(f"  {Fore.YELLOW}A.{Style.RESET_ALL} ALL sites")
    while True:
        raw = input("Enter numbers separated by commas (or 'A' for all): ").strip().upper()
        if raw == "A":
            return options[:]
        try:
            picks = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if picks and all(1 <= p <= len(options) for p in picks):
                return [options[p - 1] for p in picks]
        except ValueError:
            pass
        print("Invalid input.")


async def main():
    print(f"\n{Fore.GREEN}=== Job Application Bot (Multi-Site) ==={Style.RESET_ALL}")
    print(f"User: {EMAIL}\n")

    profile_name = pick_one("Select profile:", PROFILES)
    profile = load_profile(profile_name)
    print(f"{Fore.GREEN}Profile: {profile['profile_name']}{Style.RESET_ALL}")

    resume_path = profile["resume_file"]
    if not os.path.isabs(resume_path):
        resume_path = os.path.join(os.path.dirname(__file__), resume_path)
    if not os.path.exists(resume_path):
        print(f"{Fore.RED}Resume not found: {resume_path}{Style.RESET_ALL}")
        sys.exit(1)
    print(f"{Fore.GREEN}Resume: {resume_path}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Target roles: {', '.join(profile['job_titles'][:4])}...{Style.RESET_ALL}")

    site_names = list(SITES.keys())
    chosen = pick_many("Select job sites:", site_names)

    max_jobs_input = input("\nMax jobs per search (default 5): ").strip()
    max_jobs = int(max_jobs_input) if max_jobs_input.isdigit() else 5

    headless_input = input("Run in background (headless)? [y/N]: ").strip().lower()
    headless = headless_input == "y"

    print(f"\n{Fore.GREEN}Starting on {len(chosen)} sites...{Style.RESET_ALL}\n")

    total = 0
    for site_key in chosen:
        SiteClass = SITES[site_key]
        site = SiteClass(profile=profile, email=EMAIL, password=PASSWORD)
        print(f"\n{Fore.CYAN}--- {site.name.upper()} ---{Style.RESET_ALL}")
        try:
            await site.run(max_jobs=max_jobs, headless=headless)
            total += site.applied_count
        except Exception as e:
            print(f"{Fore.RED}[{site.name}] failed: {e}{Style.RESET_ALL}")

    print(f"\n{Fore.GREEN}Done. Total applications submitted: {total}{Style.RESET_ALL}")
    print(f"Logs: logs/apply.log")


if __name__ == "__main__":
    asyncio.run(main())
