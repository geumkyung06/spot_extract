import os
import re
import requests
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

# 1. 네이버 검색 API 키 (가게명 검증용)
SEARCH_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
SEARCH_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 2. 구글 맵스 API 키 (좌표 + 카테고리용)
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# 2. 네이버 지도(Geocoding) API 키 (NCP) (왜 안됨?)
#NCP_CLIENT_ID = os.getenv("NCP_MAP_CLIENT_ID") 
#NCP_CLIENT_SECRET = os.getenv("NCP_MAP_CLIENT_SECRET")

# ---------- [핵심] 카테고리 매핑 함수 ----------
def _map_google_category(google_types: list) -> str:
    """
    구글 Places API Types -> 내 앱 9개 카테고리로 매핑
    우선순위: 전시/체험 > 쇼핑(소품/옷) > 디저트/카페 > 술집 > 음식점
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
        return "activity"

    # 3. 소품샵 
    if any(t in types_set for t in ["home_goods_store", "book_store", "florist", "furniture_store"]):
        return "prop_shop"

    # 4. 옷가게 
    if any(t in types_set for t in ["clothing_store", "shoe_store", "jewelry_store", "shopping_mall", "department_store"]):
        return "clothing_store"

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

# ---------- 유틸 함수 ----------
def _norm_text(s: str) -> str:
    if not s: return ""
    s = s.lower().replace("<b>", "").replace("</b>", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _simplify_address(addr: str) -> str:
    if not addr or "no_address" in addr: return ""
    s = re.sub(r"\(.*?\)", "", addr)
    parts = s.split()
    if len(parts) >= 2:
        return " ".join(parts[:2]).strip()
    return s.strip()


# ---------- API 호출 ----------
def _search_naver_local(query: str) -> dict:
    """네이버 지역 검색 (이름/주소 검증)"""
    if not SEARCH_CLIENT_ID or not SEARCH_CLIENT_SECRET:
        return {}
    
    url = f"https://openapi.naver.com/v1/search/local.json?query={quote_plus(query)}&display=5&sort=random"
    headers = {
        "X-Naver-Client-Id": SEARCH_CLIENT_ID,
        "X-Naver-Client-Secret": SEARCH_CLIENT_SECRET
    }
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}

def _get_google_place_info(name: str, address: str):
    """
    [NEW] 구글 Places Text Search
    좌표(lat, lng)와 상세 타입(types)을 한 번에 가져옵니다.
    """
    if not GOOGLE_API_KEY:
        print("⚠️ GOOGLE_MAPS_API_KEY가 없습니다.")
        return None, None, []

    # 네이버에서 찾은 가장 정확한 이름과 주소로 검색
    query = f"{name} {address}"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": GOOGLE_API_KEY,
        "language": "ko",
        "region": "KR"
    }

    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        
        if data.get("status") == "OK" and data.get("results"):
            # 가장 정확한 첫 번째 결과 사용
            best = data["results"][0]
            
            # 1. 좌표 추출
            loc = best["geometry"]["location"]
            lat, lng = loc["lat"], loc["lng"]
            
            # 2. 타입(카테고리) 추출
            types = best.get("types", [])
            
            return lat, lng, types
            
    except Exception as e:
        print(f"[Google Search Error] {e}")

    return None, None, []


# ---------- 메인 로직 ----------
def check_place_on_naver(gpt_results: list[list[str]]) -> list[dict]:
    confirmed = []

    for name, address in gpt_results:
        if not name or "no_name" in name.lower():
            continue

        # 1. [네이버] 장소 실존 여부 및 정확한 한국어 명칭/주소 확보
        simple_addr = _simplify_address(address)
        query = f"{name} {simple_addr}" if simple_addr else name
        
        resp = _search_naver_local(query)
        items = resp.get("items", [])
        
        found_item = None
        
        # 네이버 결과 중 가장 유사한 것 찾기 (기존 로직 유지)
        if items:
            best_candidate = None
            max_score = 0.0
            clean_gpt_name = _norm_text(name)

            for item in items:
                clean_naver_name = _norm_text(item['title'])
                naver_addr = item.get("roadAddress") or item.get("address") or ""
                
                sim = SequenceMatcher(None, clean_gpt_name, clean_naver_name).ratio()
                if simple_addr and simple_addr in naver_addr:
                    sim += 0.2 # 주소 보너스
                
                if sim > max_score:
                    max_score = sim
                    best_candidate = item
            
            # 임계값 (이름+주소 맞으면 보통 0.5 넘음)
            if best_candidate and max_score >= 0.5:
                found_item = best_candidate

        # 2. [구글] 좌표 및 카테고리 확보
        if found_item:
            final_name = found_item["title"].replace("<b>", "").replace("</b>", "")
            final_addr = found_item.get("roadAddress") or found_item.get("address")
            
            # 구글 API 호출 (좌표 + types)
            lat, lng, google_types = _get_google_place_info(final_name, final_addr)
            
            # [핵심] 구글 types -> 내 앱 카테고리(7개)로 매핑
            my_category = _map_google_category(google_types)

            place_obj = {
                "name": final_name,       
                "address": final_addr,    
                "category": my_category,  # 매핑된 카테고리 (ex: "cafe", "restaurant")
                "latitude": lat,          
                "longitude": lng,
                "original_types": google_types # (디버깅용) 구글 원본 타입
            }
            confirmed.append(place_obj)
            
            print(f"✅ {final_name}")
            print(f"   └─ 카테고리: {my_category} (구글: {google_types})")
            print(f"   └─ 좌표: {lat}, {lng}")

        else:
            print(f"⚠️ 검증 실패: {name} (네이버 검색 결과 없음)")

    return confirmed