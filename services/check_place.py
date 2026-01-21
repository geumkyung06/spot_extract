import os
import re
import requests
import time
import uuid
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

SEARCH_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
SEARCH_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

SAVE_DIR = "static/uploads"
os.makedirs(SAVE_DIR, exist_ok=True)

# 구글 API 엔드포인트
GOOGLE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"


def _map_google_category(google_types: list) -> str:
    """
    구글 Places API Types -> 내 앱 9개 카테고리로 매핑
    """
    types_set = set(google_types)

    # 1. 전시회
    if any(t in types_set for t in ["art_gallery", "museum", "arts_organization"]):
        return "exhibition"
    # 2. 체험 
    if any(t in types_set for t in [
        "amusement_park", "aquarium", "bowling_alley", "campground",
        "movie_theater", "zoo", "park", "tourist_attraction", "stadium"
    ]):
        return "experience"
    # 3. 소품샵 
    if any(t in types_set for t in ["home_goods_store", "book_store", "florist", "furniture_store"]):
        return "accessory"
    # 4. 옷가게 
    if any(t in types_set for t in ["clothing_store", "shoe_store", "jewelry_store", "shopping_mall", "department_store"]):
        return "cloth"
    # 5. 디저트 
    if "bakery" in types_set:
        return "dessert"
    # 6. 카페 
    if "cafe" in types_set:
        return "cafe"
    # 7. 술집
    if any(t in types_set for t in ["bar", "night_club", "casino", "liquor_store"]):
        return "bar"
    # 8. 음식점
    if any(t in types_set for t in ["restaurant", "food", "meal_takeaway", "meal_delivery"]):
        return "restaurant"

    # 9. 기타
    return "etc"

def _download_google_photo(photo_reference: str) -> Optional[str]:
    """구글 포토 Reference로 이미지 다운로드 및 저장"""
    if not GOOGLE_API_KEY: return None

    try:
        params = {
            "maxwidth": 800,
            "photo_reference": photo_reference,
            "key": GOOGLE_API_KEY
        }
        r = requests.get(GOOGLE_PHOTO_URL, params=params, timeout=10)
        r.raise_for_status()

        filename = f"place_{uuid.uuid4()}.jpg"
        filepath = os.path.join(SAVE_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(r.content)
            
        return f"/{SAVE_DIR}/{filename}"

    except Exception as e:
        print(f"[Photo Download Error] {e}")
        return None

def _search_naver_local(query: str) -> dict: # 쿼리 ex) 서울(성수) 진사천훠궈 
    """네이버 지역 검색 (한국어 상호명/주소 검증용)"""
    if not SEARCH_CLIENT_ID or not SEARCH_CLIENT_SECRET: return {}
    
    url = f"https://openapi.naver.com/v1/search/local.json?query={quote_plus(query)}&display=1&sort=random"
    headers = {
        "X-Naver-Client-Id": SEARCH_CLIENT_ID,
        "X-Naver-Client-Secret": SEARCH_CLIENT_SECRET
    }
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("items"):
                return data["items"][0]
    except Exception:
        pass
    return {}

def _fetch_google_details(name: str, address: str) -> dict:
    """
    구글 검색으로 모든 정보(좌표, 카테고리, 평점, 리뷰수, 사진) 가져오기
    """
    if not GOOGLE_API_KEY:
        return {}

    query = f"{name} {address}"
    params = {
        "query": query,
        "key": GOOGLE_API_KEY,
        "language": "ko",
        "region": "KR"
    }

    result_data = {
        "latitude": 0.0,
        "longitude": 0.0,
        "category": "etc",
        "rating_avg": 0.0,
        "rating_count": 0,
        "photos": []      # 썸네일만
    }

    try:
        r = requests.get(GOOGLE_TEXTSEARCH_URL, params=params, timeout=5)
        data = r.json()
        
        if data.get("status") == "OK" and data.get("results"):
            best = data["results"][0]
            
            # 좌표
            loc = best["geometry"]["location"]
            result_data["latitude"] = loc["lat"]
            result_data["longitude"] = loc["lng"]
            
            # 카테고리 매핑
            google_types = best.get("types", [])
            result_data["category"] = _map_google_category(google_types)
            
            # 평점 및 리뷰 수
            result_data["rating_avg"] = float(best.get("rating", 0.0))
            result_data["rating_count"] = int(best.get("user_ratings_total", 0))

            # 사진 다운로드 (썸네일만)
            photo_list = best.get("photos", [])
            saved_paths = []
            
            for photo in photo_list[:1]:
                ref = photo.get("photo_reference")
                if ref:
                    path = _download_google_photo(ref)
                    if path:
                        saved_paths.append(path)
            
            result_data["photos"] = saved_paths
            
    except Exception as e:
        print(f"[Google Details Error] {e}")

    return result_data

def process_places(place_queries: list[str]) -> list[dict]:
    """
    입력된 장소명 리스트를 받아 네이버 검증 -> 구글 상세정보 병합 후 최종 데이터 반환
    """
    final_results = []

    for query in place_queries:
        print(f"\n[Processing] {query}...")
        
        # 네이버 검색
        naver_item = _search_naver_local(query)
        
        if not naver_item:
            print(f"네이버 검색 실패. 해당 장소 건너뜀")
            continue

        # 네이버 데이터 정제
        road_name = re.sub(r'<[^>]+>', '', naver_item['title'])
        road_addr = naver_item.get('roadAddress') or naver_item.get('address')
        
        print(f"  -> 네이버 확인: {road_name} ({road_addr})")

        # 2. 구글 통합 검색 (좌표, 카테고리, 평점, 리뷰, 사진)
        google_data = _fetch_google_details(road_name, road_addr)
        
        # 3. 데이터 병합
        place_obj = {
            "name": road_name,
            "address": road_addr,
            "list": google_data.get("category", "etc"),  
            "latitude": google_data.get("latitude", 0.0),
            "longitude": google_data.get("longitude", 0.0),
            "rating_avg": google_data.get("rating_avg", 0.0),
            "rating_count": google_data.get("rating_count", 0),
            "photo": google_data.get("photos", [])     # 썸네일 하나 저장
        }

        # 대표 사진 설정 (다운로드된 사진이 있다면 첫 번째 것을 사용)
        if place_obj["photo_urls"]:
            place_obj["photo"] = place_obj["photo_urls"][0]

        final_results.append(place_obj)
        
        time.sleep(0.1)

    return final_results