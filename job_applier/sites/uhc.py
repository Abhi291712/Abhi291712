import logging
from playwright.async_api import Page
from sites.base import JobSite

logger = logging.getLogger("sites.uhc")


class UHCSite(JobSite):
    name = "uhc"
    login_url = "https://careers.unitedhealthgroup.com/"

    async def login(self, page: Page):
        await page.goto(self.login_url, wait_until="networkidle")
        await self._click_if_present(page, "a:has-text('Sign In'), a:has-text('Log In')", timeout=2000)
        await self._fill(page, "input[type='email']", self.email)
        await self._fill(page, "input[type='password']", self.password)
        await self._click_if_present(page, "button[type='submit']", timeout=4000)
        logger.info(f"[{self.name}] Logged in.")

    async def search_and_apply(self, page: Page, job_title: str, max_jobs: int):
        url = f"https://careers.unitedhealthgroup.com/job-search-results/?keyword={job_title.replace(' ', '+')}"
        logger.info(f"[{self.name}] Searching: {job_title}")
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)

        cards = page.locator("a[class*='job'], li[class*='job']")
        count = min(await cards.count(), max_jobs)

        for i in range(count):
            try:
                await cards.nth(i).click()
                await page.wait_for_timeout(1500)

                if not await self._click_if_present(page, "button:has-text('Apply'), a:has-text('Apply Now')", 2000):
                    continue

                await self.fill_common_fields(page)
                await self.upload_resume(page)

                if await self._click_if_present(page, "button:has-text('Submit')", 2000):
                    self.applied_count += 1
                    logger.info(f"[{self.name}] Applied to job {i + 1}.")

                await page.go_back()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"[{self.name}] Skipped job {i + 1}: {e}")
