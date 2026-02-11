from flask import request
import json, os, re, io
import asyncio, aiohttp
import uuid
from datetime import datetime
from playwright.async_api import async_playwright
from google import genai
from google.genai import types
from PIL import Image

from .browser import BrowserManager

SAVE_FOLDER = "downloaded_images"
os.makedirs(SAVE_FOLDER, exist_ok=True)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 동시 작업 제한(3)
sem = asyncio.Semaphore(3)

# 이미지 처리
def crop_and_save_image(image_data, filepath, cut_height=250):
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            w, h = img.size
        
            # 윗부분 크롭
            if h > cut_height:
                crop_box = (0, cut_height, w, h)
                img = img.crop(crop_box)
            
            # 리사이징 (LANCZOS -> BILINEAR로 변경하여 속도 향상)
            max_size = 800
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.BILINEAR)
            
            # 흑백 변환
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img = img.convert("L")

            # 저장 (optimize=True 제거하여 저장 속도 향상, quality=50 유지)
            img.save(filepath, format='JPEG', quality=50)
            
            return filepath

    except Exception as e:
        print(f"이미지 처리 에러: {e}")
        return None

# OCR 함수
def gemini_flash_ocr(image_path):
    if not os.path.exists(image_path):
        return {"error": "이미지 파일이 없습니다."}

    try:
        with Image.open(image_path) as img:
            response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[
                "이 이미지에서 가게의 '상호명(name)'과 '주소(address)'를 식별해서 추출해줘",
                "만약 이미지에서 텍스트를 찾을 수 없거나, 해당 항목이 명확하지 않다면 억지로 만들지 말고 빈 문자열(\"\")로 채워",
                img
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", 
                response_schema={
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {
                                "type": "STRING", 
                                "description": "가게 이름. 간판이나 로고에 있는 텍스트. 없으면 빈 문자열"
                            },
                            "address": {
                                "type": "STRING", 
                                "description": "도로명 주소 또는 지번 주소. 없으면 빈 문자열"
                            }
                        },
                        "required": ["name"] 
                    }
                }
            )
        )
        data = json.loads(response.text)

        # 가게명 없는 건 제외
        valid_data = [item for item in data if item.get('name') and item['name'].strip() != ""]

        for item in valid_data:
            raw_address = item.get('address', '')
            raw_name = item.get('name', '')

            clean_addr = re.sub(r'#\S+', '', raw_address)
            clean_addr = re.sub(r'[^\w\s\(\)\-,.]', '', clean_addr) 

            clean_name = raw_name.replace('#', '')
            clean_name = re.sub(r'[^\w\s\(\)\-,.&\'\+]', '', clean_name)
            
            item['address'] = clean_addr.strip()
            item['name'] = clean_name.strip()
            
        return valid_data

    except Exception as e:
        return {"error": f"에러 발생: {str(e)}"}

# 비동기 처리
async def safe_ocr(image_path):
    async with sem:
        result = await asyncio.to_thread(gemini_flash_ocr, image_path)
        
        return result

# 이미지 다운로드
async def extract_images(browser_manager: BrowserManager, post_url: str):
    ordered_images = [] 
    seen_urls = set()

    # 기존 context에서 페이지만 새로 엶
    page = await browser_manager.context.new_page()

    await page.route("**/*", lambda route: 
        route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
        else route.continue_()
    )

    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=5000)
        content = await page.content()

        pattern = r'(https:\\/\\/scontent[^\s"]+)'
        matches = re.findall(pattern, content)
        dimension_pattern = re.compile(r'[ps]\d{2,4}x\d{2,4}')

        for raw_url in matches:
            url = raw_url.encode('utf-8').decode('unicode_escape').replace(r'\/', '/')
            if "/t51.2885-19/" in url: continue
            if any(x in url for x in ["vp/", "profile", "null", "sha256"]): continue
            if dimension_pattern.search(url): continue
            if "c0." in url: continue

            if url not in seen_urls:
                seen_urls.add(url)
                ordered_images.append(url)

    except Exception as e:
        print(f"추출 중 에러: {e}")
    finally:
        await page.close() # 페이지만 닫음

    return ordered_images

async def process_download(session, url, index):
    pattern = r'/(?:p|reel)/([^/?]+)'
    match = re.search(pattern, url)

    # url별 파일 이름 안 겹치게
    if match:
        shortcut = match.group(1)
    else:
        # 실패했다면 랜덤 문자열로 대체 (에러 방지용)
        shortcut = f"unknown_{uuid.uuid4().hex[:8]}"

    filename = f"image_{shortcut}_{index+1}.jpg"
    filepath = os.path.join(SAVE_FOLDER, filename)
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                # 이미지 처리는 비동기로 3개씩
                path = await asyncio.to_thread(crop_and_save_image, data, filepath, cut_height=150)
                return path
    except Exception as e:
        print(f"다운로드 에러: {e}")
    return None

async def extract_insta_images(url=""):
    # 브라우저 초기화 (서버 시작할 때만 한 번 실행)
    manager = BrowserManager()
    await manager.start()

    data = request.get_json()
    post_url = data.get('url')
    
    try:        
        print("이미지 추출 중...")
        image_urls = await extract_images(manager, post_url)
        print(f"{len(image_urls)}장 추출 완료")

        if not os.path.exists(SAVE_FOLDER):
            os.makedirs(SAVE_FOLDER)

        saved_files = []
        if image_urls:
            # 다운로드 및 전처리 (동시 실행)
            print("이미지 다운로드 및 변환 중...")
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [process_download(session, url, i) for i, url in enumerate(image_urls)]
                results = await asyncio.gather(*tasks)
                
                saved_files = [r for r in results if r is not None]

            # OCR 비동기 수행
            print(f"OCR 분석 시작 ({len(saved_files)}장)...")
            
            ocr_tasks = [safe_ocr(filepath) for filepath in saved_files]
            ocr_results = await asyncio.gather(*ocr_tasks)

    finally:
        await manager.stop()
        # ocr 끝난 후 이미지 삭제
        return ocr_results # JSON 형태 {'place': '아우스페이스', 'address': '경기도 파주시 탄현면 새오리로 145-21'}