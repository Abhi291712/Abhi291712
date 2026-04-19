import logging
from playwright.async_api import Page
from sites.base import JobSite

logger = logging.getLogger("sites.ziprecruiter")


class ZipRecruiterSite(JobSite):
    name = "ziprecruiter"
    login_url = "https://www.ziprecruiter.com/login"

    async def login(self, page: Page):
        await page.goto(self.login_url, wait_until="networkidle")
        await self._fill(page, "input[type='email'], input[name='email']", self.email)
        await self._fill(page, "input[type='password']", self.password)
        await self._click_if_present(page, "button[type='submit']", timeout=4000)
        logger.info(f"[{self.name}] Logged in.")

    async def search_and_apply(self, page: Page, job_title: str, max_jobs: int):
        url = f"https://www.ziprecruiter.com/jobs-search?search={job_title.replace(' ', '+')}&refine_by_org_oneclick=1"
        logger.info(f"[{self.name}] Searching: {job_title}")
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)

        cards = page.locator("article.job_item, div[class*='JobCard']")
        count = min(await cards.count(), max_jobs)

        for i in range(count):
            try:
                await cards.nth(i).click()
                await page.wait_for_timeout(1500)

                apply_btn = page.locator("button:has-text('1-Click Apply'), button:has-text('Quick Apply'), a:has-text('Apply')")
                if await apply_btn.count() == 0:
                    continue
                await apply_btn.first.click()
                await page.wait_for_timeout(2000)

                await self.fill_common_fields(page)
                await self.upload_resume(page)

                if await self._click_if_present(page, "button:has-text('Submit')", 2000):
                    self.applied_count += 1
                    logger.info(f"[{self.name}] Applied to job {i + 1}.")
            except Exception as e:
                logger.warning(f"[{self.name}] Skipped job {i + 1}: {e}")
