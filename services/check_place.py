import os
import re
import requests
import time
import uuid
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus
import geopandas as gpd
import boto3
import logging
from models import db, Place, InstaUrl, UrlPlace

s3 = boto3.client('s3')

SEARCH_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
SEARCH_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
PLACE_API_KEY = os.getenv("PLACE_API_KEY")

# 구글 API 엔드포인트
GOOGLE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"

session = requests.Session()

# enum('restaurant','bar','cafe','dessert','exhibition','prop_shop','experience','clothing','etc') 
def _map_google_category(google_types: list) -> str:
    """
    구글 Places API Types -> 내 앱 9개 카테고리로 매핑
    """
    types_set = set(google_types)

    # 1. 전시회
    if any(t in types_set for t in ["art_gallery", "museum", "arts_organization"]):
        result_category = "exhibition"
    # 2. 체험 
    elif any(t in types_set for t in [
        "amusement_park", "aquarium", "bowling_alley", "campground",
        "movie_theater", "zoo", "park", "tourist_attraction", "stadium"
    ]):
        result_category = "experience"
    # 3. 소품샵 
    elif any(t in types_set for t in ["home_goods_store", "book_store", "florist", "furniture_store"]):
        result_category = "prop_shop"
    # 4. 옷가게 
    elif any(t in types_set for t in ["clothing_store", "shoe_store", "jewelry_store", "shopping_mall", "department_store"]):
        result_category = "clothing"
    # 5. 디저트 
    elif "bakery" in types_set:
        result_category = "dessert"
    # 6. 카페 
    elif "cafe" in types_set:
        result_category = "cafe"
    # 7. 술집
    elif any(t in types_set for t in ["bar", "night_club", "casino", "liquor_store"]):
        result_category = "bar"
    # 8. 음식점
    elif any(t in types_set for t in ["restaurant", "food", "meal_takeaway", "meal_delivery"]):
        result_category = "restaurant"
    else:
    # 9. 기타
        result_category = "etc"

    logging.debug(f"분류: {result_category}, 구글 카테고리: {types_set}")

    return result_category
def _download_google_photo(shortcut, photo_reference: str) -> Optional[str]:
    """구글 포토 Reference로 이미지 다운로드 및 저장"""
    if not PLACE_API_KEY: return None

    try:
        params = {
            "maxwidth": 400,
            "photo_reference": photo_reference,
            "key": PLACE_API_KEY
        }
        r = requests.get(GOOGLE_PHOTO_URL, params=params, timeout=10)
        r.raise_for_status()

        filename = f"{shortcut}_{uuid.uuid4()}.jpg"
        bucket_name = "spottests"
        s3_key = f"places/{filename}" 

        s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=r.content,
        ContentType='image/jpeg' # 브라우저에서 바로 보이도록 설정
    )
        return f"https://{bucket_name}.s3.ap-northeast-2.amazonaws.com/{s3_key}"
    
    except Exception as e:
        print(f"[Photo Download Error] {e}")
        return None


def _search_naver_local(query: str) -> dict: # 쿼리 ex) 서울(성수) 진사천훠궈 query[0]으로 [위치+가게명, 주소] 로 변경 필요
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

def _fetch_google_details(name: str, address: str, shortcut) -> dict:
    """
    구글 검색으로 모든 정보(좌표, 카테고리, 평점, 리뷰수, 사진) 가져오기
    """
    if not PLACE_API_KEY:
        print("not google api key")
        return {}
    
    
    query = f"{name} {address}"
    params = {
        "query": query,
        "key": PLACE_API_KEY,
        "language": "ko",
        "region": "KR"
    }

    # name, address 결정 여부 정리
    result_data = {
            "gid": " ", # gid
            "name": " ",
            "address" : " ", # 네이버 검색 시도 시 없을 때
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

        status = data.get("status")
        if status != "OK":
            print(f"구글 검색 결과 없음/에러 ({name}): {status}")
            return {}
            
        if data.get("status") == "OK" and data.get("results"):
            best = data["results"][0]
            
            # 애초에 검색할 때 한번에 
            # 주소
            result_data["gid"] = best.get("place_id", "")
            result_data["name"] = best.get("name", name)
            result_data["address"] = best["formatted_address", address]

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
            
            for photo in photo_list:
                ref = photo.get("photo_reference")
                if ref:
                    path = _download_google_photo(shortcut, ref)
                    if path:
                        saved_paths.append(path)
            
            result_data["photos"] = saved_paths
            
    except Exception as e:
        print(f"[Google Details Error] {e}")

    return result_data

def trans_geo(road_mapx, road_mapy) -> Tuple[float, float]:
    gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy([int(road_mapx)], [int(road_mapy)]))
    gdf.crs = 'epsg:5179' 
    gdf = gdf.to_crs('epsg:4326')

    lng = float(gdf['geometry'].x.iloc[0])
    lat = float(gdf['geometry'].y.iloc[0])

    return lng, lat 

def process_places(place_queries: list[str], shortcut) -> list[dict]: # [[name, address], [name, address]...]
    """
    입력된 장소명 리스트를 받아 네이버 검증 -> 구글 상세정보 병합 후 최종 데이터 반환
    """
    final_results = []
    
    for idx, query in enumerate(place_queries):
        logging.debug(f"\n[Processing] {query}...")
        new_places=[]
        orig_name = query[0]
        orig_addr = query[1] if len(query) > 1 else ""
        
        # 네이버 검색해서 없으면 그냥 버리기
        # 네이버 검색
        if idx%5 == 0: time.sleep(0.3)

        naver_success = False
        road_name, road_addr = orig_name, orig_addr
        lat, lng = 0.0, 0.0
        naver_item = _search_naver_local(orig_name)
        if naver_item :
            # 네이버 데이터 정제
            road_name = re.sub(r'<[^>]+>', '', naver_item['title'])
            road_addr = naver_item.get('roadAddress') or naver_item.get('address')
            road_mapx = naver_item.get('mapx') 
            road_mapy = naver_item.get('mapy') 
        
            # DB 확인(위,경도값으로 place 내부 돌기)
            # DB 있으니 다른 장소로 넘어가기
            if road_mapx and road_mapy:
                naver_success = True
                # 위경도 변환 (경도, 위도 순서로 받음)
                lng, lat = trans_geo(road_mapx, road_mapy)
            
                place = db.session.query(Place).filter(
                    Place.latitude == lat, Place.longitude == lng
                ).first()

                if place:
                    logging.debug(f"[DB Hit] 기존 장소 발견 (Naver 위경도): {place.name}")
                    place_data = {       
                    "name": place.name,
                    "address": place.address,
                    "category": place.category, 
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                    "rating_avg": place.rating_avg,
                    "rating_count": place.rating_count,
                    "gid": place.gid,
                    "photo": place.photo    
                    }   
                    final_results.append(place_data)
                    continue
        else:
            logging.debug(f"[네이버 검색 실패] 가게명: {road_name}, 주소: {road_addr}")
            # 검색 결과 없으면 이름 바로 구글 검색
    
        # 2. 구글 통합 검색 (좌표, 카테고리, 평점, 리뷰, 사진) 
        google_data = _fetch_google_details(road_name, road_addr, shortcut) # 주소 없어도 되나?
        
        gid =  google_data.get("place_id")
        if gid: 
            place = db.session.query(Place).filter(Place.gid == gid).first()
            if place:
                logging.debug(f"[DB Hit] 기존 장소 발견 (Google gid): {place.name}")
                place_data = {       
                "name": place.name,
                "address": place.address,
                "category": place.category, 
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating_avg": place.rating_avg,
                "rating_count": place.rating_count,
                "gid": place.gid,
                "photo": place.photo    
                }   
                final_results.append(place_data)
                continue            

        raw_photos = google_data.get("photos", [])
        # 3. 데이터 병합
        # 주소만 있는 경우도 설명하는가? >> 봐야함. 근데 아마 주소만 있으면 안되게 할 듯
        if naver_success:
            final_name = road_name
            final_address = road_addr
            final_lat = lat
            final_lng = lng
        else:
            final_name = google_data.get("name", road_name)
            final_address = google_data.get("address", road_addr)
            final_lat = google_data.get("latitude", 0.0)
            final_lng = google_data.get("longitude", 0.0)
        
        if final_name == "" and final_address == "":
            logging.debug("[Google] 장소 추출 실패")
            continue
        else:
            place_obj = {
                "name": final_name,
                "address": final_address,
                "category": google_data.get("category", "etc"),  
                "latitude": final_lat,
                "longitude": final_lng,
                "rating_avg": google_data.get("rating_avg", 0.0),
                "rating_count": google_data.get("rating_count", 0),
                "gid": gid if gid else "",
                "photo": raw_photos[0] if raw_photos else ""    # 썸네일 하나 저장
            }
            final_results.append(place_obj)
            new_places.append(place_obj)
        time.sleep(0.1)
    return final_results, new_places


