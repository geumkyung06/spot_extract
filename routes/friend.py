import os
import pymysql
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from dotenv import load_dotenv

load_dotenv()

bp = Blueprint('friend', __name__)

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

# 친구 목록 전체 조회
@bp.route('/friends/list', methods=['GET'])
@jwt_required()
def get_friends_list():
    """
    친구 목록 전체 조회
    ---
    tags:
      - Friend
    security:
      - Bearer: []
    responses:
      200:
        description: 친구 목록 반환 성공
        schema:
          type: object
          properties:
            friends:
              type: array
              items:
                type: object
                properties:
                  friend_id:
                    type: integer
                  nickname:
                    type: string
                  profile_url:
                    type: string
                  updated_at:
                    type: string
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    db = get_db()
    cursor = db.cursor()

    # friend 테이블(member_id, friend_id) JOIN kakao_mem(id, photo, nickname)
    query = """
        SELECT f.friend_id AS friend_id, k.nickname, k.photo AS profile_url, f.updated_at
        FROM friend f
        JOIN kakao_mem k ON f.friend_id = k.id
        WHERE f.member_id = %s
        ORDER BY f.updated_at DESC
    """
    cursor.execute(query, (user_id,))
    friends = cursor.fetchall()  

    return jsonify({'friends': friends}), 200


# 특정 친구 상세 정보 조회 = 특정 친구 프로필 확인
@bp.route('/main/profile/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_detail(friend_id):
    """
    특정 친구 프로필 상세 조회
    ---
    tags:
      - Friend
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 친구 ID
    responses:
      200:
        description: 조회 성공
        schema:
          type: object
          properties:
            user_id:
              type: integer
            spot_nickname:
              type: string
            profile_url:
              type: string
            comment:
              type: string
            email:
              type: string
      404:
        description: 친구를 찾을 수 없음
    """
    db = get_db()
    cursor = db.cursor()

    # kakao_mem 테이블 사용, info가 comment 역할
    query = """
        SELECT id AS user_id, spot_nickname, photo AS profile_url, info AS comment, email
        FROM kakao_mem
        WHERE id = %s
    """
    cursor.execute(query, (friend_id,))
    friend = cursor.fetchone()

    if not friend:
        return jsonify({'error': 'friend not found'}), 404

    return jsonify(friend), 200


# 친구 삭제 (언팔로우)
@bp.route('/friends/<int:friend_id>', methods=['DELETE'])
@jwt_required()
def delete_friend_unfollow(friend_id):
    """
    친구 삭제 (언팔로우)
    ---
    tags:
      - Friend
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 삭제할 친구 ID
    responses:
      200:
        description: 삭제 성공
      404:
        description: 친구 관계가 없거나 이미 삭제됨
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({"message": "user_id required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # friend 테이블, 컬럼명 userid, friendid
        cursor.execute("""
            DELETE FROM friend WHERE member_id = %s AND friend_id = %s
        """, (user_id, friend_id))
        
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"message": "Friend relationship not found or already deleted"}), 404

        return jsonify({
            "message": "Unfollow success",
            "friend_id": friend_id
        }), 200
        
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


# 친구 신고 기능 - 추가만?
@bp.route('/friends/report/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_friend_report(friend_id):
    """
    친구 신고하기 (수정 필요)
    ---
    tags:
      - Friend
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            reason:
              type: string
              description: 신고 사유
    responses:
      201:
        description: 신고 접수 완료
      409:
        description: 이미 신고한 사용자
    """
    data = request.get_json()
    user_id = get_jwt_identity()
    reason = data.get('reason')

    if not user_id or not reason:
        return jsonify({"message": "user_id and reason are required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # kakao_mem 테이블의 id를 참조하는 user_report 테이블 사용
        query = """
            INSERT INTO user_report (reporter_id, reported_id, reason, created_at)
            VALUES (%s, %s, %s, NOW())
        """
        cursor.execute(query, (user_id, friend_id, reason))
        db.commit()

        return jsonify({"message": "Report submitted successfully"}), 201

    except pymysql.err.IntegrityError:
        return jsonify({"message": "Already reported"}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

# 친구 차단 기능
@bp.route('/friends/block/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_friend_block(friend_id):
    """
    친구 차단하기
    ---
    tags:
      - Friend
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
    responses:
      201:
        description: 차단 성공
      409:
        description: 이미 차단된 사용자
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({"message": "user_id is required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # 1. 차단 목록 status update
        # member_id, friend_id, status 순서로 매핑
        insert_query = """
        INSERT INTO friend (member_id, friend_id, status, created_at, updated_at)
        VALUES (%s, %s, 'block', NOW(), NOW())
        """
        cursor.execute(insert_query, (user_id, friend_id))

        # 2. 친구 목록(friend)에서 삭제 연결해야함
        '''
        delete_query = """
            DELETE FROM friend
            WHERE (user_id = %s AND friend_id = %s) 
               OR (user_id = %s AND friend_id = %s)
        """
        cursor.execute(delete_query, (user_id, friend_id, friend_id, user_id))
        '''
        db.commit()

        return jsonify({"message": "User blocked and unfriended successfully"}), 201

    except pymysql.err.IntegrityError:
        return jsonify({"message": "Already blocked"}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


# 친구가 저장한 장소 목록 조회
@bp.route('/main/places/<int:friend_id>', methods=['GET'])
@jwt_required()
@jwt_required()
def get_friend_places(friend_id):
    """
    친구가 저장한 장소 목록 조회
    ---
    tags:
      - Friend
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
              savers:
                type: array
                items:
                  type: object
                  properties:
                    nickname:
                      type: string
                    profileImageUrl:
                      type: string
    """    

    user_id = get_jwt_identity()

    # 정렬 및 필터 파라미터
    sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["dessert", "etc", "cafe", "bar", "exhibition", "restaurant", "activity", "prop_shop", "clothing_store"]

    db = get_db()
    cursor = db.cursor()

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
            p.list AS list,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        JOIN kakao_mem k ON sp.user_id = k.id
        LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
        WHERE sp.user_id = %s
    """
    
    # 내 아이디: JOIN용, 친구 아이디 : WHERE용
    params = [user_id, friend_id]

    if category_filter and category_filter in valid_categories:
        query += " AND p.list = %s"
        params.append(category_filter)

    # 정렬 방법
    # 최신순(latest)이 기본, 별점순(star) 선택 가능
    if sort_by == "star":
        order_clause = "sp.rating DESC, sp.updated_at DESC"
    else:
        order_clause = "sp.updated_at DESC"

    query += f" ORDER BY {order_clause}"

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    # 3. 데이터 가공 (JSON 구조 맞추기)
    result_places = []
    
    for row in rows:
        place_data = {
            "placeId": row['placeId'],
            "gId": row['gid'], # gId가 명확하지 않아 placeId를 문자열로 대체 (필요시 수정)
            "name": row['name'],
            "address": row['address'],
            "latitude": row['latitude'] if row['latitude'] else 0.0,
            "longitude": row['longitude'] if row['longitude'] else 0.0,
            "list": row['list'],
            "photo": row['photo'] if row['photo'] else "",
            "ratingAvg": row['ratingAvg'] if row['ratingAvg'] else 0.0,
            "ratingCount": row['ratingCount'] if row['ratingCount'] else 0,
            "myRating": row['friendRating'], # 친구가 매긴 거
            "savers": [
                {
                    "nickname": row['friend_nickname'],
                    "profileImageUrl": row['friend_photo'] if row['friend_photo'] else ""
                }
            ],
            "distance": 0, # 현재 내 위치 좌표가 없으므로 0 처리
            "isMarked": bool(row['isMarked']) # 0/1 -> True/False로 변환
        }
        result_places.append(place_data)

    return jsonify(result_places), 200


# 친구가 남긴 코멘트 전체 조회
@bp.route('/main/comment/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_comments(friend_id):
    """
    친구가 남긴 코멘트 전체 조회
    ---
    tags:
      - Friend
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 친구 ID
      - name: sort
        in: query
        type: string
        description: 정렬 기준 (기본값 latest)
        default: latest
    responses:
      200:
        description: 코멘트 조회 성공

    """

    # 리스폰스 result 내용 보내는 거 수정
    # 하트 구현

    sort = request.args.get('sort', 'latest') 

    db = get_db()
    cursor = db.cursor() # DictCursor는 get_db()에서 설정됨

    # 정렬 기준
    order_by = "c.id DESC" 

    # comments -> pins -> place 조인
    query = f"""
        SELECT 
            c.id AS comment_id,
            c.content AS comment,
            c.user_id,
            p.id AS place_id,
            p.name AS place_name,
            p.address AS place_address,
            p.photo AS place_thumbnail
        FROM comments c
        LEFT JOIN pins pin ON c.pin_id = pin.id
        LEFT JOIN place p ON pin.place_id = p.id
        WHERE c.user_id = %s
        ORDER BY {order_by}
    """
    
    cursor.execute(query, (friend_id,))
    comments = cursor.fetchall()

    # 사진 가져오기 (photos 테이블)
    results = []
    for c in comments:
        cursor.execute("""
            SELECT url FROM photos
            WHERE comment_id = %s
        """, (c['comment_id'],))
        
        photo_rows = cursor.fetchall()
        photo_urls = [row['url'] for row in photo_rows]

        results.append({
            "comment_id": c["comment_id"],
            "comment": c["comment"],
            "place": {
                "place_id": c.get("place_id"),
                "name": c.get("place_name"),
                "address": c.get("place_address"),
                "thumbnail_url": c.get("place_thumbnail")
            },
            "photos": photo_urls
        })

    return jsonify({"friend_id": friend_id, "comments": results}), 200

