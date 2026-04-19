import os
import logging
from abc import ABC, abstractmethod
from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger("sites.base")


class JobSite(ABC):
    name: str = "base"
    login_url: str = ""

    def __init__(self, profile: dict, email: str, password: str):
        self.profile = profile
        self.email = email
        self.password = password
        self.resume_path = profile["resume_file"]
        self.applied_count = 0

    @abstractmethod
    async def login(self, page: Page):
        ...

    @abstractmethod
    async def search_and_apply(self, page: Page, job_title: str, max_jobs: int):
        ...

    async def run(self, max_jobs: int = 5, headless: bool = False):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
            page = await context.new_page()
            try:
                logger.info(f"[{self.name}] Starting run for profile: {self.profile['profile_name']}")
                await self.login(page)
                for title in self.profile["job_titles"][:3]:
                    try:
                        await self.search_and_apply(page, title, max_jobs)
                    except Exception as e:
                        logger.warning(f"[{self.name}] Search error for '{title}': {e}")
                logger.info(f"[{self.name}] Applied to {self.applied_count} jobs total.")
            except Exception as e:
                logger.error(f"[{self.name}] Fatal error: {e}")
            finally:
                await browser.close()

    async def fill_common_fields(self, page: Page):
        """Fill name/email/phone/location/cover letter when present."""
        p = self.profile
        first, _, last = p["full_name"].partition(" ")

        await self._fill(page, "input[name*='first' i], input[id*='first' i], input[placeholder*='First' i]", first)
        await self._fill(page, "input[name*='last' i], input[id*='last' i], input[placeholder*='Last' i]", last)
        await self._fill(page, "input[type='email'], input[name*='email' i]", p["email"])
        await self._fill(page, "input[type='tel'], input[name*='phone' i]", p["phone"])
        await self._fill(page, "input[name*='city' i], input[name*='location' i]", p["location"])
        await self._fill(page, "textarea[name*='cover' i], textarea[id*='cover' i]", p["cover_letter"])

    async def upload_resume(self, page: Page):
        if not os.path.exists(self.resume_path):
            logger.warning(f"[{self.name}] Resume missing: {self.resume_path}")
            return
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            try:
                await upload.first.set_input_files(self.resume_path)
                await page.wait_for_timeout(1500)
            except Exception as e:
                logger.warning(f"[{self.name}] Resume upload failed: {e}")

    async def _fill(self, page: Page, selector: str, value: str):
        if not value:
            return
        loc = page.locator(selector)
        if await loc.count() > 0:
            try:
                await loc.first.fill(value)
            except Exception:
                pass

    async def _click_if_present(self, page: Page, selector: str, timeout: int = 1500) -> bool:
        loc = page.locator(selector)
        if await loc.count() > 0:
            try:
                await loc.first.click()
                await page.wait_for_timeout(timeout)
                return True
            except Exception:
                return False
        return False
