import os
import time
import uuid
import base64
import requests
import shutil
from typing import List, Dict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError

# ───────────────────────────────────────────────────────────────
# 설정
# ───────────────────────────────────────────────────────────────
# 현재 파일 위치 기준 'temp_images' 폴더 생성
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp_images")
os.makedirs(TEMP_DIR, exist_ok=True)

# ───────────────────────────────────────────────────────────────
# 1. Instagram 데이터 추출 (Playwright + BS4)
# ───────────────────────────────────────────────────────────────
def extract_post_data(post_url: str) -> Dict:
    """
    Playwright로 URL에 접속하여 이미지 URL 리스트와
    BeautifulSoup으로 캡션(글)을 추출하여 반환합니다.
    """
    with sync_playwright() as p:
        # AWS 서버(Linux) 호환을 위해 헤드리스 모드 필수
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
        page = context.new_page()
        
        result = {
            "caption": "",
            "images": []
        }

        try:
            # 60초 타임아웃 설정
            page.goto(post_url, timeout=60000, wait_until="networkidle")
            time.sleep(2) # 로딩 안정화 대기

            # 팝업(로그인 유도 등) 제거 시도
            try:
                page.evaluate("document.querySelectorAll('div[role=\"dialog\"]').forEach(e => e.remove());")
            except: pass

            # --- 이미지 URL 수집 ---
            image_urls = []
            visited = set()
            
            try:
                # 메인 이미지 컨테이너 대기
                root_div = page.wait_for_selector("div.x6s0dn4.x78zum5.xdt5ytf.xdj266r", timeout=10000)
                
                while True:
                    # 현재 슬라이드에 보이는 이미지 찾기
                    img = root_div.query_selector("div._aagv img[src*='scontent']")
                    if img:
                        src = img.get_attribute("src")
                        if src and src not in visited:
                            visited.add(src)
                            image_urls.append(src)
                    
                    # 다음 버튼 클릭
                    next_btn = root_div.query_selector('button[aria-label="다음"], button[aria-label="Next"]')
                    if not next_btn:
                        break
                    try:
                        next_btn.click(force=True)
                        time.sleep(1) 
                    except:
                        break
            except TimeoutError:
                print("이미지 컨테이너를 찾을 수 없거나 단일 이미지입니다.")

            result["images"] = image_urls

            # --- 캡션 추출 (BeautifulSoup) ---
            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            
            caption = ""
            # og:description 태그가 가장 깔끔함
            meta_desc = soup.find("meta", property="og:description")
            if meta_desc:
                caption = meta_desc["content"]
            else:
                title_tag = soup.find("title")
                if title_tag:
                    caption = title_tag.get_text()
            
            result["caption"] = caption

        except Exception as e:
            print(f"Playwright 에러: {e}")
        finally:
            browser.close()

        return result


# ───────────────────────────────────────────────────────────────
# 2. 이미지 임시 폴더 다운로드 (UUID 파일명)
# ───────────────────────────────────────────────────────────────
def download_images_to_temp(urls: List[str]) -> List[str]:
    saved_paths = []
    
    for url in urls:
        try:
            # 동시성 문제 해결을 위해 UUID 사용
            filename = f"{uuid.uuid4()}.jpg"
            filepath = os.path.join(TEMP_DIR, filename)
            
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(r.content)
                saved_paths.append(filepath)
            else:
                print(f"이미지 다운로드 실패 Code: {r.status_code}")
        except Exception as e:
            print(f"다운로드 중 에러: {e}")
            continue

    return saved_paths


# ───────────────────────────────────────────────────────────────
# 3. GPT Vision 전송용 Base64 인코딩
# ───────────────────────────────────────────────────────────────
def encode_image_to_base64(image_path: str) -> str:
    """이미지 파일을 읽어서 Base64 문자열로 반환"""
    if not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"이미지 인코딩 실패 ({image_path}): {e}")
        return None


# ───────────────────────────────────────────────────────────────
# 4. 파일 청소 (라우트에서 사용)
# ───────────────────────────────────────────────────────────────
def delete_temp_files(paths: List[str]):
    """리스트에 있는 파일들을 디스크에서 삭제합니다."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
                print(f"🗑️ 임시 파일 삭제: {os.path.basename(p)}")
        except Exception as e:
            print(f"파일 삭제 에러: {e}")