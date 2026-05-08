import os
import pymysql
import random
import math

from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from services.my_logger import get_my_logger
from services.utils import get_full_photo_url

from models import db, PlaceLike, Place, Friend, KakaoMem

bp = Blueprint('main', __name__)
logger = get_my_logger(__name__)

def get_db():
    if 'db' not in g:
        g.db = pymysql.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# HOME: 친구들의 전체 저장 장소 핀
@bp.route('/main/home', methods=['GET'])
@jwt_required()
def get_all_pins():
    """
    친구들이 저장한 장소(핀) 전체 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: lat
        in: query
        type: number
        format: float
        description: 현재 사용자의 위도
      - name: lng
        in: query
        type: number
        format: float
        description: 현재 사용자의 경도
      - name: distance
        in: query
        type: number
        format: float
        description: 장소가 표시될 반경
    responses:
      200:
        description: 친구들의 핀 목록 조회 성공
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
              name:
                type: string
              latitude:
                type: number
              longitude:
                type: number
              list:
                type: string
                description: 카테고리
              distance:
                type: number
                description: 현재 위치로부터 떨어진 거리
      401:
        description: 인증 실패
      500:
        description: 서버 오류
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
        current_distance = request.args.get("distance", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng, current_distance = None, None, None

    friends = Friend.query.filter_by(member_id=user_id, status='friend').all() # friend_id 검색
    friend_ids = [f.friend_id for f in friends]

    # 친구가 한 명도 없는 경우 빈 리스트 반환
    if not friend_ids:
        return jsonify([]), 200
    
    # 정렬 및 필터 파라미터
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # 2. 쿼리 작성
    # - p.*: 장소 기본 정보
    # - sp.rating: 친구가 매긴 별점 (myRating)
    # - k.nickname, k.photo: 친구 정보 (savers용)
    # - my_sp.id: 내가 저장했는지 여부 확인용 (LEFT JOIN)
    select_clause = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.spot_nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        """
    
    # 내 아이디: JOIN용, 친구 아이디 : WHERE용
    params = []

    if current_lat is not None and current_lng is not None and current_distance is not None:
      # 6371: 지구의 반지름 (km)
      select_clause += """,
          (6371 * acos(
              cos(radians(%s)) * cos(radians(p.latitude)) *
              cos(radians(p.longitude) - radians(%s)) +
              sin(radians(%s)) * sin(radians(p.latitude))
          )) AS distance
      """
      # 쿼리에 들어갈 파라미터 (현재 위도, 현재 경도, 현재 위도)
      params.extend([current_lat, current_lng, current_lat])
    else:
        # 위치 정보가 없으면 거리는 0으로 처리
        select_clause += ", 0 AS distance "

    from_where_clause = """
    FROM saved_place sp
    JOIN place p ON sp.place_id = p.id
    JOIN kakao_mem k ON sp.user_id = k.id
    LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
    WHERE sp.user_id IN ({})
    """.format(', '.join(['%s'] * len(friend_ids)))
    
    params.append(user_id)
    params.extend(friend_ids)

    query = select_clause + from_where_clause

    # 카테고리 쿼리 추가
    if category_filter and category_filter in valid_categories:
      query += " AND p.list = %s"
      params.append(category_filter)
    
    # 반경 거리 추가
    if current_lat is not None and current_lng is not None and current_distance is not None:
      query += " HAVING distance <= %s"
      params.append(current_distance)

    logger.debug(f"쿼리 내 %s 개수: {query.count('%s')}")
    logger.debug(f"params 리스트 개수: {len(params)}")
    logger.debug(f"params 구성 데이터: {params}")
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    # 데이터 없으면 바로 리턴
    if not rows:
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            places_dict[pid] = {
                "placeId": pid,
                "name": row['name'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "distance": round(row['distance'], 2) if 'distance' in row else 0.0, # 계산된 거리 포함
                "list": row['category']     
            }
            logger.debug(f"저장 장소 [{pid}]: {places_dict}")   

    result_list = list(places_dict.values())

    return jsonify(result_list), 200

# HOME: 전체 저장 장소 목록 조회
@bp.route('/main/home/places', methods=['GET'])
@jwt_required()
def get_all_places():
    """
    내 친구들이 저장한 장소 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: sort
        in: query
        type: string
        enum: [latest, star]
        default: latest
        description: |
          정렬 기준:
          - latest: 친구가 저장한 최신순
          - star: 친구가 매긴 별점 높은순
      - name: category
        in: query
        type: string
        enum: [accessory, bar, cafe, cloth, etc, restaurant, dessert, exhibition, experience]
        description: 카테고리 필터 (미선택 시 전체 조회)
    responses:
      200:
        description: 장소 목록 반환 성공 (savers는 항상 최신 저장 순으로 정렬됨)
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
                example: 123
              gId:
                type: string
                example: "147258369"
              name:
                type: string
                example: "성수 힙한 카페"
              address:
                type: string
                example: "서울 성동구 성수동..."
              latitude:
                type: number
                example: 37.5412
              longitude:
                type: number
                example: 127.0567
              list:
                type: string
                example: "cafe"
              photo:
                type: string
                example: "https://image_url.com/img.jpg"
              ratingAvg:
                type: number
                example: 4.5
              myRating:
                type: number
                description: "조회된 친구(들) 중 가장 최근에 저장한 친구가 매긴 별점"
                example: 5.0
              isMarked:
                type: boolean
                description: "로그인한 유저(나)가 이 장소를 저장했는지 여부"
                example: true
              saversCount:
                type: integer
                description: "이 장소를 저장한 내 친구들의 총 수"
              savers:
                type: array
                description: "이 장소를 저장한 내 친구들 목록 (최신 저장순 정렬)"
                items:
                  type: object
                  properties:
                    nickname:
                      type: string
                      example: "김철수"
                    profileImageUrl:
                      type: string
                      example: "https://profile_url.com/p.jpg"
              distance:
                type: number
                description: "현재 위치와의 거리 (km)"
                example: 1.25
      401:
        description: 인증 실패 (JWT 토큰 누락 또는 만료)
    """
    user_id = get_jwt_identity()
    logger.debug(f"👉 /main/home/places 호출됨! 요청한 유저 ID: {user_id}")
  # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng = None, None

    friends = Friend.query.filter_by(member_id=user_id, status='friend').all() # friend_id 검색
    friend_ids = [f.friend_id for f in friends]
    logger.debug(f"👉 내 친구 ID 목록: {friend_ids}")

    # 친구가 한 명도 없는 경우 빈 리스트 반환
    if not friend_ids:
        logger.debug("👉 친구가 없어서 조기 종료됨!")
        return jsonify([]), 200
    
    # 정렬 및 필터 파라미터
    '''sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]'''

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    params = []

    # 2. 쿼리 작성
    # - p.*: 장소 기본 정보
    # - sp.rating: 친구가 매긴 별점 (myRating)
    # - k.nickname, k.photo: 친구 정보 (savers용)
    # - my_sp.id: 내가 저장했는지 여부 확인용 (LEFT JOIN)
    select_clause = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.spot_nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
    """

    if current_lat is not None and current_lng is not None:
        # 6371: 지구의 반지름 (km)
        select_clause += """,
            (6371 * acos(
                cos(radians(%s)) * cos(radians(p.latitude)) *
                cos(radians(p.longitude) - radians(%s)) +
                sin(radians(%s)) * sin(radians(p.latitude))
            )) AS distance
        """
        params.extend([current_lat, current_lng, current_lat])
    else:
        select_clause += ", 0 AS distance "

    # 2. FROM, JOIN, WHERE 절 구성
    placeholders = ', '.join(['%s'] * len(friend_ids))
    from_where_clause = f"""
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        JOIN kakao_mem k ON sp.user_id = k.id
        LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
        WHERE sp.user_id IN ({placeholders})
    """
    
    params.append(user_id)
    params.extend(friend_ids)

    '''additional_where = ""
    if category_filter and category_filter in valid_categories:
        additional_where = " AND p.list = %s"
        params.append(category_filter)'''

    '''# 4. 정렬 방식
    if sort_by == "star":
        order_clause = " ORDER BY sp.rating DESC, sp.updated_at DESC"
    else:
        order_clause = " ORDER BY sp.updated_at DESC"'''
    order_clause = " ORDER BY sp.updated_at DESC"

    # 최종 쿼리 병합
    query = select_clause + from_where_clause + order_clause

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    logger.debug(f"👉 쿼리 실행 완료! 조회된 장소 개수(rows): {len(rows)}")
    # 데이터 없으면 바로 리턴
    if not rows:
        logger.debug("👉 친구의 장소가 하나도 없어서 조기 종료됨!")
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            raw_distance = row.get('distance')
            distance = round(float(raw_distance), 1) if raw_distance is not None else 0.0

            places_dict[pid] = {
                "placeId": pid,
                "gId": row['gid'],
                "name": row['name'],
                "address": row['address'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "list": row['category'],
                "photo": get_full_photo_url(row.get('photo')),
                "ratingAvg": float(row['ratingAvg']) if row['ratingAvg'] else 0.0,
                "myRating": row['friendRating'],
                "isMarked": bool(row['isMarked']), # 내가 저장했는지 여부 정상 출력
                "distance": distance * 1000, # km -> m 단위로 변환
                "saversCount": 0,
                "savers": []
            }
        
        places_dict[pid]["savers"].append({
            "nickname": row['friend_nickname'],
            "profileImageUrl": row['friend_photo'] if row['friend_photo'] else "",
            "updated_at": row['updated_at']
        })

    result_list = list(places_dict.values())
    
    for place in result_list:
        place["savers"].sort(key=lambda x: x['updated_at'], reverse=True)
        place["saversCount"] = len(place["savers"])

        for saver in place["savers"]:
            del saver['updated_at']
    
    logger.debug(f"savers 관련 업데이트한 최종 장소 정보: {result_list[0]}")
    return jsonify(result_list), 200


# FREIND: 친구의 전체 저장 장소 핀
@bp.route('/main/home/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_pins(friend_id):
    """
    특정 친구가 저장한 장소(핀) 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 조회할 친구의 ID
      - name: lat
        in: query
        type: number
        format: float
        description: 현재 사용자의 위도
      - name: lng
        in: query
        type: number
        format: float
        description: 현재 사용자의 경도
      - name: distance
        in: query
        type: number
        format: float
        description: 장소가 표시될 반경
    responses:
      200:
        description: 친구의 핀 목록 조회 성공
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
              name:
                type: string
              latitude:
                type: number
              longitude:
                type: number
              list:
                type: string
              distance:
                type: number
                description: 현재 위치로부터 떨어진 거리
      401:
        description: 인증 실패
      404:
        description: 해당 친구를 찾을 수 없음
      500:
        description: 서버 오류
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
        current_distance = request.args.get("distance", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng, current_distance = None, None, None

    # 정렬 및 필터 파라미터
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # 2. 쿼리 작성
    # - p.*: 장소 기본 정보
    # - sp.rating: 친구가 매긴 별점 (myRating)
    # - k.nickname, k.photo: 친구 정보 (savers용)
    # - my_sp.id: 내가 저장했는지 여부 확인용 (LEFT JOIN)
    select_clause = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.spot_nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        """
    
    # 내 아이디: JOIN용, 친구 아이디 : WHERE용
    params = []

    if current_lat is not None and current_lng is not None and current_distance is not None:
      # 6371: 지구의 반지름 (km)
      select_clause += """,
          (6371 * acos(
              cos(radians(%s)) * cos(radians(p.latitude)) *
              cos(radians(p.longitude) - radians(%s)) +
              sin(radians(%s)) * sin(radians(p.latitude))
          )) AS distance
      """
      # 쿼리에 들어갈 파라미터 (현재 위도, 현재 경도, 현재 위도)
      params.extend([current_lat, current_lng, current_lat])
    else:
      # 위치 정보가 없으면 거리는 0으로 처리
      select_clause += ", 0 AS distance "

    # 특정 친구의 장소들만 확인
    # FROM & WHERE 절 구성 (단일 친구용 + 보안 검증 조인)
    from_where_clause = """
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        JOIN friend f ON f.member_id = %s AND f.friend_id = sp.user_id AND f.status = 'friend'
        JOIN kakao_mem k ON sp.user_id = k.id
        LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
        WHERE sp.user_id = %s
    """
    
    params.extend([user_id, user_id, friend_id])

    query = select_clause + from_where_clause

    # 카테고리 쿼리 추가
    if category_filter and category_filter in valid_categories:
      query += " AND p.list = %s"
      params.append(category_filter)

    if current_lat is not None and current_lng is not None and current_distance is not None:
        query += " HAVING distance <= %s"
        params.append(current_distance)

    logger.debug(f"쿼리 내 %s 개수: {query.count('%s')}")
    logger.debug(f"params 리스트 개수: {len(params)}")
    logger.debug(f"params 구성 데이터: {params}")
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    # 데이터 없으면 바로 리턴
    if not rows:
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            places_dict[pid] = {
                "placeId": pid,
                "name": row['name'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "distance": round(row['distance'], 2) if 'distance' in row else 0.0, # 계산된 거리 포함
                "list": row['category']    
            }

    result_list = list(places_dict.values())

    return jsonify(result_list), 200

# FREIND: 친구가 저장한 장소 목록 조회
@bp.route('/main/places/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_places(friend_id):
    """
    친구가 저장한 장소 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 친구 ID
      - name: sort
        in: query
        type: string
        description: 정렬 기준 (latest, star)
        default: latest
      - name: category
        in: query
        type: string
        description: 카테고리 필터
    responses:
      200:
        description: 장소 목록 반환 성공
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
              gId:
                type: string
              name:
                type: string
              address:
                type: string
              latitude:
                type: number
              longitude:
                type: number
              list:
                type: string
              photo:
                type: string
              ratingAvg:
                type: number
              myRating:
                type: number
              isMarked:
                type: boolean
              saversCount:
                type: integer
                description: "이 장소를 저장한 친구들의 총 수 (프로필 주인 포함)"
              savers:
                type: array
                items:
                  type: object
                  properties:
                    nickname:
                      type: string
                    profileImageUrl:
                      type: string
              distance:
                type: number
                description: "현재 위치와의 거리 (km)"
                example: 1.25
    """    
    user_id = get_jwt_identity()

    # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng = None, None

    my_friends = Friend.query.filter_by(member_id=user_id, status='friend').all()
    my_friend_ids = [f.friend_id for f in my_friends]

    other_saver_ids = list(set(my_friend_ids + [user_id]))

    # 정렬 및 필터 파라미터
    sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # 2. 쿼리 작성
    # - p.*: 장소 기본 정보
    # - sp.rating: 친구가 매긴 별점 (myRating)
    # - k.nickname, k.photo: 친구 정보 (savers용)
    # - my_sp.id: 내가 저장했는지 여부 확인용 (LEFT JOIN)
    query = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            target_sp.rating AS targetFriendRating,
            target_sp.updated_at AS target_updated_at,
            f_k.spot_nickname AS friend_nickname,
            f_k.photo AS friend_photo,
            f_sp.updated_at AS friend_updated_at,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        FROM saved_place target_sp
        JOIN place p ON target_sp.place_id = p.id
        JOIN kakao_mem k ON target_sp.user_id = k.id
        LEFT JOIN saved_place f_sp ON p.id = f_sp.place_id AND f_sp.user_id IN ({})
        LEFT JOIN kakao_mem f_k ON f_sp.user_id = f_k.id
        LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
        WHERE target_sp.user_id = %s
    """.format(', '.join(['%s'] * len(other_saver_ids)) if other_saver_ids else "NULL")
    
    # 순서: [IN 절의 친구ID들] + [isMarked용 내ID] + [WHERE 절의 대상친구ID]
    params = other_saver_ids + [user_id, friend_id]

    if category_filter and category_filter in valid_categories:
        query += " AND p.list = %s"
        params.append(category_filter)

    order_clause = "target_sp.rating DESC, target_sp.updated_at DESC" if sort_by == "star" else "target_sp.updated_at DESC"
    query += f" ORDER BY {order_clause}"

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    if not rows:
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            calculate_distance(current_lat, current_lng, row['latitude'], row['longitude'])
            
            places_dict[pid] = {
                "placeId": pid,
                "gId": row['gid'],
                "name": row['name'],
                "address": row['address'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "list": row['category'],
                "photo": row['photo'] if row['photo'] else "",
                "ratingAvg": float(row['ratingAvg']) if row['ratingAvg'] else 0.0,
                "myRating": row['targetFriendRating'], # 친구의 별점
                "isMarked": bool(row['isMarked']),
                "distance": round(row['distance'], 2) if 'distance' in row else 0.0, # 계산된 거리 포함
                "saversCount": 0,
                "savers": []
            }
        
        if row['friend_nickname']:
            if not any(s['nickname'] == row['friend_nickname'] for s in places_dict[pid]["savers"]):
                places_dict[pid]["savers"].append({
                    "nickname": row['friend_nickname'],
                    "profileImageUrl": row['friend_photo'] if row['friend_photo'] else "",
                    "updated_at": row['friend_updated_at']
                })

    result_list = list(places_dict.values())
    for place in result_list:
        place["savers"].sort(key=lambda x: x['updated_at'], reverse=True)
        place["saversCount"] = len(place["savers"])
        for saver in place["savers"]:
            del saver['updated_at']

    return jsonify(result_list), 200

# FREIND:친구가 남긴 코멘트 전체 조회
@bp.route('/main/comment/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_comments(friend_id):
    """
    친구가 남긴 코멘트 전체 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 친구 ID
      - name: sort
        in: query
        type: string
        description: 정렬 기준 (latest)
        default: latest
    responses:
      200:
        description: 코멘트 조회 성공
        schema:
          type: object
          properties:
            friendId:
              type: integer
            count:
              type: integer
            comments:
              type: array
              items:
                type: object
                properties:
                  commentId:
                    type: integer
                  content:
                    type: string
                  createdAt:
                    type: string
                  isLiked:
                    type: boolean
                    description: 내가 이 장소를 좋아요(place_like)했는지 여부
                  place:
                    type: object
                    description: 장소 정보 객체
                  photos:
                    type: array
                    description: 사진 URL 리스트
                    items:
                      type: string
    """
    
    # 내 아이디 (장소 좋아요 여부 체크용)
    user_id = get_jwt_identity()
    
    sort = request.args.get('sort', 'latest') 

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    order_by = "c.id DESC" 

    # user_id 삭제함
    query = f"""
        SELECT 
            c.id AS comment_id,
            c.content,
            c.created_at,
            p.id AS place_id,
            p.name AS place_name,
            p.address AS place_address,
            p.list AS place_category,
            p.photo AS photo,
            CASE WHEN pl.id IS NOT NULL THEN TRUE ELSE FALSE END AS is_liked
        FROM comments c
        JOIN kakao_mem k ON c.user_id = k.id
        LEFT JOIN place p ON pin.place_id = p.id
        LEFT JOIN place_like pl 
              ON p.id = pl.placeid_id AND pl.userid_id = %s
        WHERE c.user_id = %s
        ORDER BY {order_by}
    """
    
    # 나 : 하트 체크, 친구 : 조회
    cursor.execute(query, (user_id, friend_id))
    comments = cursor.fetchall()

    results = []
    
    # 데이터 가공
    for c in comments:
        # 사진 가져오기
        cursor.execute("""
            SELECT url FROM photos
            WHERE comment_id = %s
        """, (c['comment_id'],))
        
        photo_rows = cursor.fetchall()
        photo_urls = [row['url'] for row in photo_rows]

        results.append({
            "commentId": c["comment_id"],
            "content": c["content"], # >> db column = content. (comment)
            "createdAt": c["created_at"],
            "isLiked": bool(c["is_liked"]), 
            "place": {
                "placeId": c.get("place_id"),
                "name": c.get("place_name"),
                "address": c.get("place_address"),
                "category": c.get("place_category"),
            },
            "photos": photo_urls
        })

    return jsonify({
        "friendId": friend_id,
        "count": len(results),
        "comments": results
    }), 200


# ME: 나의 전체 저장 장소 핀
@bp.route('/main/me/home', methods=['GET'])
@jwt_required()
def get_my_pins():
    """
    내가 저장한 장소(핀) 전체 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: lat
        in: query
        type: number
        format: float
        description: 현재 사용자의 위도 (거리 계산용)
      - name: lng
        in: query
        type: number
        format: float
        description: 현재 사용자의 경도 (거리 계산용)
      - name: distance
        in: query
        type: number
        format: float
        description: 장소가 표시될 반경
    responses:
      200:
        description: 내가 저장한 장소 목록 조회 성공
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
              name:
                type: string
              latitude:
                type: number
              longitude:
                type: number
              list:
                type: string
              distance:
                type: number
      401:
        description: 인증 실패
      500:
        description: 서버 오류
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
        current_distance = request.args.get("distance", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng, current_distance = None, None, None

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # 정렬 및 필터 파라미터
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    # 2. 쿼리 작성
    # - p.*: 장소 기본 정보
    # - sp.rating: 친구가 매긴 별점 (myRating)
    # - k.nickname, k.photo: 친구 정보 (savers용)
    # - my_sp.id: 내가 저장했는지 여부 확인용 (LEFT JOIN)
    select_clause = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.spot_nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        """
  
    params = []

    if current_lat is not None and current_lng is not None and current_distance is not None:
      # 6371: 지구의 반지름 (km)
      select_clause += """,
          (6371 * acos(
              cos(radians(%s)) * cos(radians(p.latitude)) *
              cos(radians(p.longitude) - radians(%s)) +
              sin(radians(%s)) * sin(radians(p.latitude))
          )) AS distance
      """
      # 쿼리에 들어갈 파라미터 (현재 위도, 현재 경도, 현재 위도)
      params.extend([current_lat, current_lng, current_lat])
    else:
        # 위치 정보가 없으면 거리는 0으로 처리
        select_clause += ", 0 AS distance "

    # 특정 친구의 장소들만 확인
    from_where_clause = """
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        WHERE sp.user_id = %s
    """
    params.append(user_id)

    query = select_clause + from_where_clause

    # 카테고리 쿼리 추가 
    if category_filter and category_filter in valid_categories:
      query += " AND p.list = %s"
      params.append(category_filter)

    if current_lat is not None and current_lng is not None and current_distance is not None:
        query += " HAVING distance <= %s"
        params.append(current_distance)

    logger.debug(f"쿼리 내 %s 개수: {query.count('%s')}")
    logger.debug(f"params 리스트 개수: {len(params)}")
    logger.debug(f"params 구성 데이터: {params}")
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    # 데이터 없으면 바로 리턴
    if not rows:
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            places_dict[pid] = {
                "placeId": pid,
                "name": row['name'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "distance": round(row['distance'], 2) if 'distance' in row else 0.0, # 계산된 거리 포함
                "list": row['category']    
            }
    
    result_list = list(places_dict.values())

    return jsonify(result_list), 200

    
# ME: 내 저장 장소 목록 조회
# /main/me/places?lat=00.00&lng=00.00&category=cafe&sort=distance
@bp.route('/main/me/places', methods=['GET'])
@jwt_required()
def get_my_places():
    """
    내가 저장한 장소 목록 조회
    ---
    tags:
      - Main
    security:
      - Bearer: []
    parameters:
      - name: lat
        in: query
        type: number
        format: float
        description: 현재 위치 위도 (입력 시 거리 계산)
      - name: lng
        in: query
        type: number
        format: float
        description: 현재 위치 경도 (입력 시 거리 계산)
    responses:
      200:
        description: 내 장소 목록 반환 성공
        schema:
          type: array
          items:
            type: object
            properties:
              placeId:
                type: integer
                description: "장소 PK"
              gId:
                type: string
                description: "장소의 고유 식별자 (Google Place ID)"
              name:
                type: string
                description: "장소명"
              address:
                type: string
                description: "장소 주소"
              latitude:
                type: number
                format: float
                description: "장소 위도"
              longitude:
                type: number
                format: float
                description: "장소 경도"
              list:
                type: string
                description: "장소 카테고리"
              photo:
                type: string
                description: "장소 사진 URL"
              ratingAvg:
                type: number
                format: float
                description: "해당 장소의 전체 평균 별점"
              myRating:
                type: number
                format: float
                description: "내가 준 별점"
              isMarked:
                type: boolean
                description: "내 저장 여부 (내가 저장한 목록이므로 항상 true)"
              distance:
                type: number
                format: float
                description: "현재 위치와의 거리 (km, 위치 정보 없을 시 0.0)"
                example: 1.25
              saversCount:
                type: integer
                description: "이 장소를 저장한 내 친구들의 총 수"
              savers:
                type: array
                description: "이 장소를 저장한 친구들 목록 (최신순)"
                items:
                  type: object
                  properties:
                    nickname:
                      type: string
                      description: "친구 닉네임"
                    profileImageUrl:
                      type: string
                      description: "친구 프로필 이미지 URL"
    """
    user_id = get_jwt_identity()

    # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng = None, None

    friends = Friend.query.filter_by(member_id=user_id, status='friend').all()
    friend_ids = [f.friend_id for f in friends]
    logger.debug(f"내 친구 목록: {friend_ids}")

    #valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    db = get_db()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # 2. 쿼리 작성
    # - 메인: 내가(user_id) 저장한 saved_place (my_sp)
    # - 조인: 해당 장소를 저장한 친구들의 정보 (f_sp, f_k)
    select_clause = """
        SELECT
            p.id AS placeId,
            p.name,
            p.gid,
            p.address,
            p.latitude,
            p.longitude,
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            my_sp.rating AS myRating,
            my_sp.updated_at AS my_updated_at,
            f_k.spot_nickname AS friend_nickname,
            f_k.photo AS friend_photo,
            f_sp.updated_at AS friend_updated_at
    """

    params = [] # 파라미터 담을 리스트 초기화

    if current_lat is not None and current_lng is not None:
        # 6371: 지구의 반지름 (km)
        select_clause += """,
            (6371 * acos(
                cos(radians(%s)) * cos(radians(p.latitude)) *
                cos(radians(p.longitude) - radians(%s)) +
                sin(radians(%s)) * sin(radians(p.latitude))
            )) AS distance
        """
        params.extend([current_lat, current_lng, current_lat])
    else:
        # 위치 정보가 없으면 거리는 0으로 처리
        select_clause += ", 0 AS distance "

    # 2. FROM 및 WHERE 절 구성
    if friend_ids:
        placeholders = ', '.join(['%s'] * len(friend_ids))
        from_where_clause = f"""
            FROM saved_place my_sp
            JOIN place p ON my_sp.place_id = p.id
            LEFT JOIN saved_place f_sp ON p.id = f_sp.place_id AND f_sp.user_id IN ({placeholders})
            LEFT JOIN kakao_mem f_k ON f_sp.user_id = f_k.id
            WHERE my_sp.user_id = %s
        """
        params.extend(friend_ids)
        params.append(user_id)
    else:
        from_where_clause = """
            FROM saved_place my_sp
            JOIN place p ON my_sp.place_id = p.id
            LEFT JOIN saved_place f_sp ON p.id = f_sp.place_id AND 1=0
            LEFT JOIN kakao_mem f_k ON f_sp.user_id = f_k.id AND 1=0
            WHERE my_sp.user_id = %s
        """
        params.append(user_id)

    '''additional_where = ""
    if category_filter and category_filter in valid_categories:
        additional_where = " AND p.list = %s"
        params.append(category_filter)'''

    order_clause = " ORDER BY my_sp.updated_at DESC"
    '''if sort_by == "distance":
        order_clause = " ORDER BY distance DESC, my_sp.updated_at DESC"
    else:
        order_clause = " ORDER BY my_sp.updated_at DESC"'''

    # 4. 최종 쿼리 병합
    query = select_clause + from_where_clause + order_clause
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            raw_distance = row.get('distance')
            distance = round(float(raw_distance), 1) if raw_distance is not None else 0.0

            places_dict[pid] = {
                "placeId": pid,
                "gId": row['gid'],
                "name": row['name'],
                "address": row['address'],
                "latitude": float(row['latitude']) if row['latitude'] else 0.0,
                "longitude": float(row['longitude']) if row['longitude'] else 0.0,
                "list": row['category'],
                "photo": row['photo'] if row['photo'] else "",
                "ratingAvg": float(row['ratingAvg']) if row['ratingAvg'] else 0.0,
                "myRating": row['myRating'],
                "isMarked": True, # 내 목록이므로 무조건 True
                "distance": distance*1000, # m 단위로
                "saversCount": 0,
                "savers": []
            }

        if row['friend_nickname']:
            places_dict[pid]["savers"].append({
                "nickname": row['friend_nickname'],
                "profileImageUrl": row['friend_photo'] if row['friend_photo'] else "",
                "updated_at": row['friend_updated_at']
            })

    result_list = list(places_dict.values())
    for place in result_list:
        place["savers"].sort(key=lambda x: x['updated_at'], reverse=True)
        place["saversCount"] = len(place["savers"])
        for saver in place["savers"]:
            del saver['updated_at']

    logger.debug(f"savers 관련 업데이트한 최종 장소 정보: {result_list[0]}")
    return jsonify(result_list), 200

# ME: 내 코멘트 조회

# 게시물에 하트
@bp.route('/main/place_like/<int:place_id>', methods=['POST'])
@jwt_required()
def post_place_like(place_id):
    """
    장소 좋아요 토글 
    ---
    tags:
      - Main
    summary: 장소에 좋아요를 누르거나 취소
    description: 이미 좋아요가 있다면 삭제(False)하고, 없다면 생성(True)
    security:
      - Bearer: []
    parameters:
      - name: place_id
        in: path
        type: integer
        required: true
        description: 좋아요를 누를 장소의 고유 ID
    responses:
      201:
        description: 좋아요 성공 (Liked)
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Liked"
            status:
              type: boolean
              example: true
      200:
        description: 좋아요 취소 성공 (Unliked)
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Unliked"
            status:
              type: boolean
              example: false
      404:
        description: 해당 장소 ID가 존재하지 않음
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Place not found"
      500:
        description: 서버 내부 에러
    """
    user_id = get_jwt_identity()
    
    place = Place.query.get(place_id)
    if not place:
        return jsonify({'message': 'Place not found'}), 404

    try:
        existing_like = PlaceLike.query.filter_by(userid_id=user_id, placeid_id=place_id).first()

        if existing_like:
            db.session.delete(existing_like)
            db.session.commit()
            return jsonify({'message': 'Unliked', 'status': False}), 200
        else:
            new_like = PlaceLike(userid_id=user_id, placeid_id=place_id)
            db.session.add(new_like)
            db.session.commit()
            return jsonify({'message': 'Liked', 'status': True}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# sql에서 반경 확인으로 변경 필요
# 거리 확인 로직
def calculate_distance(lat1, lon1, lat2, lon2):

        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 0.0
        R = 6371  # 지구 반지름 (km)
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (math.sin(d_lat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(d_lon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)