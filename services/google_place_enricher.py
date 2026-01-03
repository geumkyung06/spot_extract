import os
import time
import uuid
import requests
from typing import List, Dict, Optional

# 구글 API 엔드포인트
GOOGLE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"

# 이미지를 저장할 로컬 경로 (Flask static 폴더 등)
SAVE_DIR = "static/uploads"
os.makedirs(SAVE_DIR, exist_ok=True)

def _text_search(
    query: str,
    api_key: str,
    *,
    language: str = "ko",
    region: str = "KR",
    location: Optional[str] = None,
    radius: Optional[int] = None
) -> Optional[dict]:
    """Google Places Text Search로 장소 검색"""
    params = {"query": query, "key": api_key, "language": language, "region": region}
    if location:
        params["location"] = location
    if radius:
        params["radius"] = radius

    try:
        r = requests.get(GOOGLE_TEXTSEARCH_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        return results[0] if results else None
    except Exception as e:
        print(f"[GOOGLE SEARCH FAIL] {e}")
        return None

def _download_google_photo(photo_reference: str, api_key: str) -> Optional[str]:
    """
    [추가됨] 구글 포토 Reference를 이용해 실제 이미지를 다운로드하고 저장된 경로를 반환
    """
    try:
        # maxwidth는 400~800 정도가 적당 (비용/용량 고려)
        params = {
            "maxwidth": 800,
            "photo_reference": photo_reference,
            "key": api_key
        }
        r = requests.get(GOOGLE_PHOTO_URL, params=params, timeout=10)
        r.raise_for_status()

        # 파일명 생성 (UUID)
        filename = f"google_{uuid.uuid4()}.jpg"
        filepath = os.path.join(SAVE_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(r.content)
            
        # DB에 저장할 웹 경로 반환 (예: /static/uploads/...)
        return f"/{SAVE_DIR}/{filename}"

    except Exception as e:
        print(f"[GOOGLE PHOTO FAIL] {e}")
        return None

def enrich_with_google(places: List[Dict], api_key: str) -> List[Dict]:
    """
    입력:  [{name, address, lat, lng, ...}]
    출력:  평점, 리뷰수, 그리고 '저장된 이미지 경로 리스트'가 추가된 딕셔너리 리스트
    """
    out: List[Dict] = []
    
    for p in places:
        name = (p.get("name") or "").strip()
        addr = (p.get("address") or "").strip()
        lat  = p.get("lat") or p.get("latitude")
        lng  = p.get("lng") or p.get("longitude")

        if not name:
            out.append(p)
            continue

        # 1. 장소 검색
        query = f"{name} {addr}".strip() if addr else name
        location_str = f"{lat},{lng}" if (lat and lng) else None

        best = _text_search(query, api_key, location=location_str)

        if best:
            merged = dict(p)
            
            # 2. 평점 & 리뷰 수 저장
            if best.get("rating"):
                merged["rating_avg"] = float(best["rating"])
            if best.get("user_ratings_total"):
                merged["rating_count"] = int(best["user_ratings_total"])

            # 3. [수정됨] 이미지 3장 다운로드 및 저장
            photos = best.get("photos") or []
            saved_paths = []
            
            # 최대 3장까지만 반복
            for photo_data in photos[:3]:
                ref = photo_data.get("photo_reference")
                if ref:
                    path = _download_google_photo(ref, api_key)
                    if path:
                        saved_paths.append(path)
            
            # 결과에 저장 (리스트 형태)
            merged["saved_image_paths"] = saved_paths
            
            # DB의 place.photo 컬럼에는 '첫 번째 사진'을 대표로 넣기 위해 별도 키 제공
            if saved_paths:
                merged["main_photo"] = saved_paths[0]

            out.append(merged)
        else:
            out.append(p)

        time.sleep(0.1)  # API 레이트 리밋 조절

    return out