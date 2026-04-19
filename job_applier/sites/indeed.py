import logging
from playwright.async_api import Page
from sites.base import JobSite

logger = logging.getLogger("sites.indeed")


class IndeedSite(JobSite):
    name = "indeed"
    login_url = "https://secure.indeed.com/auth"

    async def login(self, page: Page):
        await page.goto(self.login_url, wait_until="networkidle")
        await self._fill(page, "input[type='email'], input[name='__email']", self.email)
        await self._click_if_present(page, "button[type='submit']", timeout=3000)
        await self._fill(page, "input[type='password'], input[name='__password']", self.password)
        await self._click_if_present(page, "button[type='submit']", timeout=4000)
        logger.info(f"[{self.name}] Logged in.")

    async def search_and_apply(self, page: Page, job_title: str, max_jobs: int):
        url = f"https://www.indeed.com/jobs?q={job_title.replace(' ', '+')}&sc=0kf%3Aattr%28DSQF7%29%3B"
        logger.info(f"[{self.name}] Searching: {job_title}")
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)

        cards = page.locator("a.tapItem, a[data-jk], li.css-5lfssm")
        count = min(await cards.count(), max_jobs)

        for i in range(count):
            try:
                await cards.nth(i).click()
                await page.wait_for_timeout(1500)

                apply_btn = page.locator("button:has-text('Apply now'), a:has-text('Apply now'), button[id*='indeedApplyButton']")
                if await apply_btn.count() == 0:
                    continue
                await apply_btn.first.click()
                await page.wait_for_timeout(2000)

                for _ in range(6):
                    await self.fill_common_fields(page)
                    await self.upload_resume(page)
                    if await self._click_if_present(page, "button:has-text('Submit')", 2000):
                        self.applied_count += 1
                        logger.info(f"[{self.name}] Applied to job {i + 1}.")
                        break
                    if await self._click_if_present(page, "button:has-text('Continue')", 1500):
                        continue
                    break

                await page.go_back()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"[{self.name}] Skipped job {i + 1}: {e}")
