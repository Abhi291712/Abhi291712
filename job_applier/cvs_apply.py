import asyncio
import os
import logging
from playwright.async_api import async_playwright, Page
from config import EMAIL, PASSWORD, CVS_LOGIN_URL

logger = logging.getLogger("cvs_apply")


async def login(page: Page):
    logger.info("Navigating to CVS careers...")
    await page.goto(CVS_LOGIN_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    sign_in = page.locator("a:has-text('Sign In'), button:has-text('Sign In')")
    if await sign_in.count() > 0:
        await sign_in.first.click()
        await page.wait_for_timeout(2000)

    email_field = page.locator("input[type='email'], input[name*='email'], input[id*='email']")
    if await email_field.count() > 0:
        await email_field.first.fill(EMAIL)

    pass_field = page.locator("input[type='password']")
    if await pass_field.count() > 0:
        await pass_field.first.fill(PASSWORD)

    submit = page.locator("button[type='submit'], input[type='submit']")
    if await submit.count() > 0:
        await submit.first.click()
        await page.wait_for_timeout(3000)

    logger.info("Login attempted.")


async def search_jobs(page: Page, job_title: str):
    logger.info(f"Searching CVS for: {job_title}")
    search_box = page.locator("input[placeholder*='Search'], input[aria-label*='Search'], input[id*='search']")
    if await search_box.count() > 0:
        await search_box.first.fill(job_title)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)


async def apply_to_jobs(page: Page, profile: dict, max_jobs: int = 5):
    resume_path = profile["resume_file"]
    job_cards = page.locator("a[class*='job'], div[class*='job-card'], li[class*='job']")
    count = min(await job_cards.count(), max_jobs)
    logger.info(f"Found {count} jobs to apply to on CVS.")

    applied = 0
    for i in range(count):
        try:
            card = job_cards.nth(i)
            await card.click()
            await page.wait_for_timeout(2000)

            apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply Now')")
            if await apply_btn.count() == 0:
                await page.go_back()
                continue

            await apply_btn.first.click()
            await page.wait_for_timeout(2000)

            await _fill_application(page, profile, resume_path)
            applied += 1
            logger.info(f"Applied to job {i + 1} on CVS.")
            await page.go_back()
            await page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"Skipped job {i + 1}: {e}")

    return applied


async def _fill_application(page: Page, profile: dict, resume_path: str):
    first_name, _, last_name = profile["full_name"].partition(" ")

    fn_field = page.locator("input[name*='first' i], input[id*='first' i], input[placeholder*='First' i]")
    if await fn_field.count() > 0:
        await fn_field.first.fill(first_name)

    ln_field = page.locator("input[name*='last' i], input[id*='last' i], input[placeholder*='Last' i]")
    if await ln_field.count() > 0:
        await ln_field.first.fill(last_name)

    name_field = page.locator("input[name*='name' i]:not([name*='first' i]):not([name*='last' i])")
    if await name_field.count() > 0:
        await name_field.first.fill(profile["full_name"])

    email_field = page.locator("input[type='email'], input[name*='email' i]")
    if await email_field.count() > 0:
        await email_field.first.fill(profile["email"])

    phone_field = page.locator("input[type='tel'], input[name*='phone' i]")
    if await phone_field.count() > 0:
        await phone_field.first.fill(profile["phone"])

    loc_field = page.locator("input[name*='location' i], input[name*='city' i]")
    if await loc_field.count() > 0:
        await loc_field.first.fill(profile["location"])

    cover_field = page.locator("textarea[name*='cover' i], textarea[id*='cover' i], textarea[placeholder*='cover' i]")
    if await cover_field.count() > 0:
        await cover_field.first.fill(profile["cover_letter"])

    if os.path.exists(resume_path):
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            await upload.first.set_input_files(resume_path)
            await page.wait_for_timeout(1500)
    else:
        logger.warning(f"Resume not found: {resume_path}")

    submit = page.locator("button[type='submit']:has-text('Submit'), button:has-text('Submit Application')")
    if await submit.count() > 0:
        await submit.first.click()
        await page.wait_for_timeout(2000)


async def run_cvs(profile: dict, max_jobs: int = 5, headless: bool = False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await login(page)
            for title in profile["job_titles"][:2]:
                await search_jobs(page, title)
                await apply_to_jobs(page, profile, max_jobs)
        finally:
            await browser.close()
