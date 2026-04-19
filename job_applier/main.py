import asyncio
import logging
import os
import sys
from colorama import Fore, Style, init
from config import load_profile, PROFILES
from cvs_apply import run_cvs
from uhc_apply import run_uhc

init(autoreset=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "apply.log")),
    ],
)


def menu(title: str, options: list[str]) -> str:
    print(f"\n{Fore.CYAN}{title}{Style.RESET_ALL}")
    for i, opt in enumerate(options, 1):
        print(f"  {Fore.YELLOW}{i}.{Style.RESET_ALL} {opt}")
    while True:
        choice = input("Enter number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("Invalid choice, try again.")


async def main():
    print(f"\n{Fore.GREEN}=== Job Application Bot ==={Style.RESET_ALL}")
    print(f"Email: abhisheknkp7292@gmail.com\n")

    # Choose profile
    profile_name = menu("Select application profile:", PROFILES)
    profile = load_profile(profile_name)
    print(f"{Fore.GREEN}Profile loaded: {profile['profile_name']}{Style.RESET_ALL}")

    # Verify resume exists (supports absolute Windows paths or relative)
    resume_path = profile["resume_file"]
    if not os.path.isabs(resume_path):
        resume_path = os.path.join(os.path.dirname(__file__), resume_path)
    if not os.path.exists(resume_path):
        print(f"{Fore.RED}Resume not found: {resume_path}{Style.RESET_ALL}")
        print(f"Please update profiles/{profile_name}.json with the correct path.")
        sys.exit(1)
    print(f"{Fore.GREEN}Resume: {resume_path}{Style.RESET_ALL}")

    # Choose site
    site = menu("Select job site to apply to:", ["CVS Health", "UHC (United Health Group)", "Both"])

    # How many jobs
    max_jobs_input = input("\nHow many jobs to apply to? (default 5): ").strip()
    max_jobs = int(max_jobs_input) if max_jobs_input.isdigit() else 5

    # Headless mode
    headless_input = input("Run in background (headless)? [y/N]: ").strip().lower()
    headless = headless_input == "y"

    print(f"\n{Fore.GREEN}Starting applications...{Style.RESET_ALL}\n")

    if site in ("CVS Health", "Both"):
        print(f"{Fore.CYAN}--- Applying on CVS Health ---{Style.RESET_ALL}")
        await run_cvs(profile, max_jobs=max_jobs, headless=headless)

    if site in ("UHC (United Health Group)", "Both"):
        print(f"{Fore.CYAN}--- Applying on UHC ---{Style.RESET_ALL}")
        await run_uhc(profile, max_jobs=max_jobs, headless=headless)

    print(f"\n{Fore.GREEN}Done! Check logs/apply.log for details.{Style.RESET_ALL}")


if __name__ == "__main__":
    asyncio.run(main())
