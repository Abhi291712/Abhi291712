import logging
from playwright.async_api import Page
from sites.base import JobSite

logger = logging.getLogger("sites.linkedin")


class LinkedInSite(JobSite):
    name = "linkedin"
    login_url = "https://www.linkedin.com/login"

    async def login(self, page: Page):
        await page.goto(self.login_url, wait_until="networkidle")
        await self._fill(page, "input#username", self.email)
        await self._fill(page, "input#password", self.password)
        await self._click_if_present(page, "button[type='submit']", timeout=4000)
        logger.info(f"[{self.name}] Logged in.")

    async def search_and_apply(self, page: Page, job_title: str, max_jobs: int):
        url = f"https://www.linkedin.com/jobs/search/?keywords={job_title.replace(' ', '%20')}&f_AL=true"
        logger.info(f"[{self.name}] Searching: {job_title}")
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)

        cards = page.locator("div.job-card-container, li.jobs-search-results__list-item")
        count = min(await cards.count(), max_jobs)

        for i in range(count):
            try:
                await cards.nth(i).click()
                await page.wait_for_timeout(1500)

                easy_apply = page.locator("button:has-text('Easy Apply')")
                if await easy_apply.count() == 0:
                    continue
                await easy_apply.first.click()
                await page.wait_for_timeout(1500)

                # LinkedIn Easy Apply has multiple steps — walk through them
                for _ in range(6):
                    await self.fill_common_fields(page)
                    await self.upload_resume(page)
                    if await self._click_if_present(page, "button:has-text('Submit application')", 2000):
                        self.applied_count += 1
                        logger.info(f"[{self.name}] Applied to job {i + 1}.")
                        break
                    if await self._click_if_present(page, "button:has-text('Review')", 1500):
                        continue
                    if await self._click_if_present(page, "button:has-text('Next')", 1500):
                        continue
                    break

                # Close modal if still open
                await self._click_if_present(page, "button[aria-label='Dismiss']", 500)
                await self._click_if_present(page, "button:has-text('Discard')", 500)
            except Exception as e:
                logger.warning(f"[{self.name}] Skipped job {i + 1}: {e}")
