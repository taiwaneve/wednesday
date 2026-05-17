from playwright.sync_api import sync_playwright

p = sync_playwright().start()
try:
    b = p.chromium.launch(headless=False)
    page = b.new_page()
    page.goto('https://barricade.gg/local', timeout=30000)
    page.wait_for_selector('div[data-tutorial="board"]', timeout=20000)
    print('EVAL_RESULT:', page.evaluate('() => 1 + 1'))
    b.close()
finally:
    p.stop()
