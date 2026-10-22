import asyncio, pathlib
from playwright.async_api import async_playwright

OUT = pathlib.Path(r"D:\work\GPT\expense-agent\docs\screens")
OUT.mkdir(parents=True, exist_ok=True)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:8013"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.screenshot(path=str(OUT / "01-login.png"))

        await page.click("#submit")
        await page.wait_for_url(f"{BASE}/")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(OUT / "02-workbench.png"))

        await page.goto(f"{BASE}/#claims")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(OUT / "03-claims.png"))

        await page.locator("#claimList .claim-row").first.click()
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(OUT / "04-detail.png"))
        await page.locator("#dChecks .check").first.click()
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / "05-policy.png"))
        await page.locator("#dClose").click()

        await page.goto(f"{BASE}/#submit")
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "06-submit.png"))

        await page.goto(f"{BASE}/#policies")
        await page.wait_for_timeout(800)
        await page.locator(".policy").first.locator(".toggle").click()
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / "07-policies.png"))

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.goto(f"{BASE}/#workbench")
        await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "08-mobile.png"))

        await browser.close()
        print("screens:", sorted(x.name for x in OUT.iterdir()))

asyncio.run(main())
