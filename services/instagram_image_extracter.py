from flask import request
import json, os, re, io
import asyncio, aiohttp
import uuid
from playwright.async_api import async_playwright
from google import genai
from google.genai import types
from PIL import Image

# 설정
SAVE_FOLDER = "downloaded_images"
os.makedirs(SAVE_FOLDER, exist_ok=True)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
sem = asyncio.Semaphore(3) # OCR 동시 요청 제한

# --- [1. 브라우저 매니저 클래스 (최적화 버전)] ---
class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        """서버 시작 시 또는 최초 요청 시 실행"""
        if self.browser is not None:
            return # 이미 켜져 있으면 패스

        self.playwright = await async_playwright().start()
        # 리눅스 호환성 및 속도 최적화 옵션 추가
        self.browser = await self.playwright.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        # 컨텍스트(세션) 미리 생성
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36"
        )
        print("✅ 브라우저(Warm) 준비 완료")

    async def stop(self):
        """서버 완전 종료 시에만 호출"""
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
        self.browser = None
        print("🛑 브라우저 종료")

# 전역 브라우저 인스턴스 (재사용을 위해 함수 밖으로 뺌)
global_browser_manager = BrowserManager()


# --- [2. 이미지 처리 함수 (속도 최적화 적용)] ---
def crop_and_save_image(image_data, filepath, cut_height=250):
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            w, h = img.size
        
            # 1. 윗부분 크롭 (상단 불필요 정보 제거)
            if h > cut_height:
                crop_box = (0, cut_height, w, h)
                img = img.crop(crop_box)
            
            # 2. 리사이징 (BILINEAR: 속도 최우선)
            max_size = 800
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.BILINEAR)
            
            # 3. 흑백 변환 (OCR 인식률 유지하면서 용량 감소)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img = img.convert("L")

            # 4. 저장 (optimize=False로 저장 속도 확보)
            img.save(filepath, format='JPEG', quality=50)
            
            return filepath

    except Exception as e:
        print(f"이미지 처리 에러: {e}")
        return None


# --- [3. 크롤링 로직 (리소스 차단 및 타임아웃 단축)] ---
async def extract_images(browser_manager: BrowserManager, post_url: str):
    ordered_images = [] 
    seen_urls = set()

    # 이미 켜져 있는 context에서 '탭'만 새로 엶 (매우 빠름)
    page = await browser_manager.context.new_page()

    # 이미지, 폰트, 미디어 차단 (속도 향상)
    await page.route("**/*", lambda route: 
        route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
        else route.continue_()
    )

    try:
        # 타임아웃 5초 (메인 콘텐츠만 빠르게 로드)
        await page.goto(post_url, wait_until="domcontentloaded", timeout=5000)
        content = await page.content()

        pattern = r'(https:\\/\\/scontent[^\s"]+)'
        matches = re.findall(pattern, content)
        dimension_pattern = re.compile(r'[ps]\d{2,4}x\d{2,4}')

        for raw_url in matches:
            url = raw_url.encode('utf-8').decode('unicode_escape').replace(r'\/', '/')
            
            # 필터링 로직
            if "/t51.2885-19/" in url: continue
            if any(x in url for x in ["vp/", "profile", "null", "sha256"]): continue
            if dimension_pattern.search(url): continue
            if "c0." in url: continue
            
            # 추가 필터 (아이콘, 작은 썸네일 제거)
            if "/e35/" in url or "/e15/" in url: continue 
            if re.search(r'\/s\d{3}x\d{3}\/', url): continue

            if url not in seen_urls:
                seen_urls.add(url)
                ordered_images.append(url)
        
        # 안전장치: 너무 많이 잡히면 앞부분(메인사진)만 자름
        if len(ordered_images) > 10:
            ordered_images = ordered_images[:10]

    except Exception as e:
        print(f"추출 중 에러 (Timeout 등): {e}")
    finally:
        await page.close() # 브라우저는 끄지 않고 탭만 닫음

    return ordered_images


# --- [4. 다운로드 및 OCR (기존 로직 유지)] ---
async def process_download(session, url, index):
    # 파일명 생성 로직 (기존 유지)
    pattern = r'/(?:p|reel)/([^/?]+)'
    match = re.search(pattern, url)
    shortcut = match.group(1) if match else f"unknown_{uuid.uuid4().hex[:8]}"

    filename = f"image_{shortcut}_{index+1}.jpg"
    filepath = os.path.join(SAVE_FOLDER, filename)
    
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
                # 비동기 스레드로 이미지 변환
                path = await asyncio.to_thread(crop_and_save_image, data, filepath, cut_height=150)
                return path
    except Exception as e:
        print(f"다운로드 에러: {e}")
    return None

def gemini_flash_ocr(image_path):
    # ... (기존 Gemini OCR 코드와 100% 동일) ...
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
                            "name": {"type": "STRING", "description": "가게 이름. 없으면 빈 문자열"},
                            "address": {"type": "STRING", "description": "도로명/지번 주소. 없으면 빈 문자열"}
                        },
                        "required": ["name"] 
                    }
                }
            )
        )
        data = json.loads(response.text)
        
        valid_data = [item for item in data if item.get('name') and item['name'].strip() != ""]
        
        # 정제 로직
        for item in valid_data:
            clean_addr = re.sub(r'#\S+', '', item.get('address', ''))
            clean_addr = re.sub(r'[^\w\s\(\)\-,.]', '', clean_addr) 
            clean_name = item.get('name', '').replace('#', '')
            clean_name = re.sub(r'[^\w\s\(\)\-,.&\'\+]', '', clean_name)
            
            item['address'] = clean_addr.strip()
            item['name'] = clean_name.strip()
            
        return valid_data
    except Exception as e:
        return {"error": f"에러 발생: {str(e)}"}

async def safe_ocr(image_path):
    async with sem:
        result = await asyncio.to_thread(gemini_flash_ocr, image_path)
        return result


# --- [5. 메인 핸들러 (통합)] ---
async def extract_insta_images(url=""):
    # [중요] 전역 브라우저 매니저 사용
    # 처음 실행될 때만 start()가 작동하고, 이후에는 무시됨 (Warm Start 효과)
    await global_browser_manager.start()

    # Flask request 객체 처리 (JSON 바디가 없으면 인자 url 사용)
    target_url = url
    try:
        if request:
            data = request.get_json(silent=True)
            if data and 'url' in data:
                target_url = data.get('url')
    except RuntimeError:
        pass # Flask context 밖에서 실행될 경우 대비

    ocr_results = []
    
    try:        
        print(f"이미지 추출 시작: {target_url}")
        # 전역 매니저를 넘겨줌
        image_urls = await extract_images(global_browser_manager, target_url)
        print(f"{len(image_urls)}장 URL 확보 완료")

        saved_files = []
        if image_urls:
            print("이미지 다운로드 및 변환 중...")
            connector = aiohttp.TCPConnector(limit=10)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [process_download(session, url, i) for i, url in enumerate(image_urls)]
                results = await asyncio.gather(*tasks)
                saved_files = [r for r in results if r is not None]

            print(f"OCR 분석 시작 ({len(saved_files)}장)...")
            ocr_tasks = [safe_ocr(filepath) for filepath in saved_files]
            
            # 2차원 리스트([[{},{}], [{},{}]])를 1차원으로 평탄화
            nested_results = await asyncio.gather(*ocr_tasks)
            for res in nested_results:
                if isinstance(res, list):
                    ocr_results.extend(res)
                elif isinstance(res, dict) and "error" not in res:
                    ocr_results.append(res)

    except Exception as e:
        print(f"전체 프로세스 에러: {e}")
        return {"error": str(e)}
    
    # finally 블록에서 browser.stop()을 제거했습니다!
    # 그래야 브라우저가 계속 살아있어서 다음 요청 때 빠릅니다.
    
    return ocr_results