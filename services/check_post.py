
import asyncio
import time
import re
import json
from playwright.async_api import Page, async_playwright

from services.instagram_image_extracter import extract_insta_images
from services.browser_manager import global_browser_manager


def get_shortcode(post_url: str) -> str | None:
    match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", post_url)
    return match.group(1) if match else None


def find_media_node(obj, shortcode: str):
    if isinstance(obj, dict):
        code = obj.get("code")
        if code == shortcode and (
            "carousel_media_count" in obj or "carousel_media" in obj
        ):
            return obj
        for v in obj.values():
            found = find_media_node(v, shortcode)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_media_node(item, shortcode)
            if found is not None:
                return found
    return None


async def get_carousel_image_count(page: Page, post_url: str) -> int:
    shortcode = get_shortcode(post_url)
    if not shortcode:
        return 1

    scripts = await page.query_selector_all('script[type="application/json"]')
    for script in scripts:
        text = await script.inner_text()
        try:
            blob = json.loads(text)
        except json.JSONDecodeError:
            continue

        node = find_media_node(blob, shortcode)
        if node is not None:
            if "carousel_media_count" in node:
                return node["carousel_media_count"]
            if isinstance(node.get("carousel_media"), list):
                return len(node["carousel_media"])

    next_btn = await page.query_selector('button[aria-label="다음"], button[aria-label="Next"]')
    if next_btn:
        return -1 
    return 1 

async def extract_post_data(page: Page, post_url: str) -> dict:
    await page.goto(post_url, wait_until="networkidle")
    img_count = await get_carousel_image_count(page, post_url)

    return img_count
