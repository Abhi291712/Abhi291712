import asyncio
import os
import logging
from playwright.async_api import async_playwright, Page
from config import EMAIL, PASSWORD, UHC_LOGIN_URL, BASE_DIR
from resume_parser import extract_text, extract_phone, extract_name

logger = logging.getLogger("uhc_apply")


async def login(page: Page):
    logger.info("Navigating to UHC careers...")
    await page.goto(UHC_LOGIN_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    sign_in = page.locator("a:has-text('Sign In'), button:has-text('Sign In'), a:has-text('Log In')")
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


async def search_jobs(page: Page, keywords: list[str]):
    logger.info(f"Searching UHC for: {keywords[0]}")
    search_box = page.locator("input[placeholder*='Search'], input[aria-label*='keyword'], input[id*='search']")
    if await search_box.count() > 0:
        await search_box.first.fill(keywords[0])
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)


async def apply_to_jobs(page: Page, profile: dict, max_jobs: int = 5):
    resume_path = os.path.join(BASE_DIR, profile["resume_file"])
    resume_text = extract_text(resume_path) if os.path.exists(resume_path) else ""
    name = extract_name(resume_text)
    phone = extract_phone(resume_text)

    job_cards = page.locator("a[class*='job'], li[class*='job'], div[data-automation*='job']")
    count = min(await job_cards.count(), max_jobs)
    logger.info(f"Found {count} jobs to apply to on UHC.")

    applied = 0
    for i in range(count):
        try:
            card = job_cards.nth(i)
            await card.click()
            await page.wait_for_timeout(2000)

            apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply Now'), button:has-text('Apply Now')")
            if await apply_btn.count() == 0:
                await page.go_back()
                continue

            await apply_btn.first.click()
            await page.wait_for_timeout(2000)

            await _fill_application(page, profile, name, phone, resume_path)
            applied += 1
            logger.info(f"Applied to job {i + 1} on UHC.")
            await page.go_back()
            await page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"Skipped job {i + 1}: {e}")

    return applied


async def _fill_application(page: Page, profile: dict, name: str, phone: str, resume_path: str):
    name_field = page.locator("input[name*='name'], input[id*='name'], input[placeholder*='Name']")
    if await name_field.count() > 0 and name:
        await name_field.first.fill(name)

    email_field = page.locator("input[type='email'], input[name*='email']")
    if await email_field.count() > 0:
        await email_field.first.fill(EMAIL)

    phone_field = page.locator("input[type='tel'], input[name*='phone']")
    if await phone_field.count() > 0 and phone:
        await phone_field.first.fill(phone)

    cover_field = page.locator("textarea[name*='cover'], textarea[id*='cover'], textarea[placeholder*='cover']")
    if await cover_field.count() > 0:
        await cover_field.first.fill(profile["cover_letter"])

    if os.path.exists(resume_path):
        upload = page.locator("input[type='file']")
        if await upload.count() > 0:
            await upload.first.set_input_files(resume_path)
            await page.wait_for_timeout(1500)

    submit = page.locator("button[type='submit']:has-text('Submit'), button:has-text('Submit Application')")
    if await submit.count() > 0:
        await submit.first.click()
        await page.wait_for_timeout(2000)


async def run_uhc(profile: dict, max_jobs: int = 5, headless: bool = False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await login(page)
            await search_jobs(page, profile["keywords"])
            applied = await apply_to_jobs(page, profile, max_jobs)
            logger.info(f"UHC: Applied to {applied} jobs.")
        finally:
            await browser.close()
