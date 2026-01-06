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
    parameters:
      - name: user_id
        in: query
        type: integer
        required: true
        description: 내 유저 ID (이 유저의 친구 목록을 조회)
    responses:
      200:
        description: 친구 목록 조회 성공
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

    # friend 테이블(userid, friendid) JOIN kakao_mem(id, photo, nickname)
    query = """
        SELECT f.friendid AS friend_id, k.nickname, k.photo AS profile_url, f.updated_at
        FROM friend f
        JOIN kakao_mem k ON f.friendid = k.id
        WHERE f.userid = %s
        ORDER BY f.updated_at DESC
    """
    cursor.execute(query, (user_id,))
    friends = cursor.fetchall()  

    return jsonify({'friends': friends}), 200


# 특정 친구 상세 정보 조회
@bp.route('/main/places/<int:friend_id>', methods=['GET'])
@jwt_required()
def get_friend_detail(friend_id):
    """
    특정 친구 상세 정보 조회
    ---
    tags:
      - Friend
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 조회할 친구의 ID
    responses:
      200:
        description: 조회 성공
        schema:
          type: object
          properties:
            user_id:
              type: integer
            nickname:
              type: string
            profile_url:
              type: string
            comment:
              type: string
              description: 코멘트(소개글)
            email:
              type: string
      404:
        description: 친구를 찾을 수 없음
    """
    db = get_db()
    cursor = db.cursor()

    # kakao_mem 테이블 사용, info가 comment 역할
    query = """
        SELECT id AS user_id, nickname, photo AS profile_url, info AS comment, email
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
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 삭제할 친구의 ID
      - name: user_id
        in: query
        type: integer
        required: true
        description: 내 유저 ID
    responses:
      200:
        description: 언팔로우 성공
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
            DELETE FROM friend
            WHERE userid = %s AND friendid = %s
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


# 친구 신고 기능
@bp.route('/friends/report/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_friend_report(friend_id):
    """
    친구 신고 기능
    ---
    tags:
      - Friend
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 신고할 대상(친구) ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            user_id:
              type: integer
              description: 신고하는 사람(나) ID
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
    친구 차단 기능
    ---
    tags:
      - Friend
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 차단할 대상(친구) ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            user_id:
              type: integer
              description: 차단하는 사람(나) ID
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
        # 1. 차단 목록(user_block)에 추가
        insert_query = """
            INSERT INTO user_block (blocker_id, blocked_id, created_at)
            VALUES (%s, %s, NOW())
        """
        cursor.execute(insert_query, (user_id, friend_id))

        # 2. 친구 목록(friend)에서 삭제 로직은 주석 처리됨 (필요시 주석 해제)
        '''
        delete_query = """
            DELETE FROM friend
            WHERE (userid = %s AND friendid = %s) 
               OR (userid = %s AND friendid = %s)
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
def get_friend_places(friend_id):
    """
    친구가 저장한 장소 목록 조회
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
        description: 정렬 기준 (latest, star)
        default: latest
      - name: category
        in: query
        type: string
        description: "카테고리 필터 (옵션: 'dessert', 'etc', 'cafe', 'bar', 'exhibition', 'restaurant', 'activity', 'prop_shop', 'clothing_store')"
    responses:
      200:
        description: 조회 성공
        schema:
          type: object
          properties:
            friend_id:
              type: integer
            applied_filter:
              type: string
            applied_sort:
              type: string
            places:
              type: array
              items:
                type: object
                properties:
                  place_id:
                    type: integer
                  name:
                    type: string
                  address:
                    type: string
                  photo:
                    type: string
                  category:
                    type: string
                  star:
                    type: integer
                  updated_at:
                    type: string
    """
    # 정렬 및 필터 파라미터
    sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["dessert", "etc", "cafe", "bar", "exhibition", "restaurant", "activity", "prop_shop", "clothing_store"]

    db = get_db()
    cursor = db.cursor() # DictCursor는 get_db()에서 설정됨

    # saved_place, place 테이블 조인
    query = """
        SELECT
            p.id AS place_id,
            p.name,
            p.address,
            p.photo,
            p.list AS category,
            sp.updated_at,
            sp.rating AS star
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        WHERE sp.user_id = %s
    """
    
    params = [friend_id]

    # [필터링 로직]
    if category_filter and category_filter in valid_categories:
        query += " AND p.list = %s"
        params.append(category_filter)

    # [정렬 로직]
    if sort_by == "star":
        order_clause = "sp.rating DESC, sp.updated_at DESC"
    else:
        order_clause = "sp.updated_at DESC"

    query += f" ORDER BY {order_clause}"

    cursor.execute(query, tuple(params))
    places = cursor.fetchall()

    return jsonify({
        "friend_id": friend_id,
        "applied_filter": category_filter if category_filter else "all",
        "applied_sort": sort_by,
        "places": places
    }), 200


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


# 북마크 저장 (내 저장소로 가져오기)
@bp.route('/friends/<int:friend_id>/bookmark_places', methods=['POST'])
@jwt_required()
def post_bookmark_places(friend_id):
    """
    친구 장소 내 보관함으로 가져오기 (북마크)
    ---
    tags:
      - Friend
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: (경로용) 친구 ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            user_id:
              type: integer
              description: 내 ID
            place_ids:
              type: array
              items:
                type: integer
              description: 가져올 장소들의 ID 리스트
    responses:
      201:
        description: 저장 성공
    """
    db = get_db()
    cursor = db.cursor()

    data = request.get_json()
    user_id = get_jwt_identity()
    place_ids = data.get('place_ids', [])

    if not user_id or not place_ids:
        return jsonify({"error": "user_id and place_ids are required"}), 400

    for place_id in place_ids:
        # saved_place 테이블 사용
        cursor.execute("""
            SELECT id FROM saved_place
            WHERE user_id = %s AND place_id = %s
        """, (user_id, place_id))
        existing = cursor.fetchone()

        if existing:
            # 이미 있으면 업데이트
            cursor.execute("""
                UPDATE saved_place
                SET updated_at = NOW()
                WHERE id = %s
            """, (existing['id'],)) # DictCursor이므로 key 접근
        else:
            # 없으면 새로 삽입
            cursor.execute("""
                INSERT INTO saved_place (user_id, place_id, created_at, updated_at, rating)
                VALUES (%s, %s, NOW(), NOW(), 0)
            """, (user_id, place_id))

    db.commit()

    return jsonify({
        "message": "Saved successfully",
        "friend_id": friend_id,
        "user_id": user_id,
        "saved_count": len(place_ids)
    }), 201