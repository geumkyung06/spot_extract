import os
import pymysql
import random
import math

from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, PlaceLike, Place, Friend, KakaoMem

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
# Q. 추가해야하는 목록: 공통 친구수, 공통친구 프로필 3개(업데이트순)
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
      description: >
        친구 전체 목록 반환
      responses:
        200:
          description: 조회 성공
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
                      description: 친구의 유저 ID
                    nickname:
                      type: string
                      description: 친구의 닉네임
                    profile_url:
                      type: string
                      description: 프로필 이미지 URL
                    comment:
                      type: string
                      description: 상태 메시지 (one_line)
                    spot_id:
                      type: string
                      description: spot_id
                    status:
                      type: string
                      description: 친구 상태(block','friend','give','waiting')
                    updated_at:
                      type: string
                      description: 친구 추가/수정 일시
                    mutual_count:
                      type: integer
                      description: 나와 해당 친구의 공통 친구 수
                    mutual_profiles:
                      type: array
                      items:
                      type: string
                      description: 공통 친구 프로필 이미지 URL 리스트 (최대 3개, 최신순)
        500:
          description: 서버 에러
      """
    user_id = get_jwt_identity()
    
    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    db = get_db()
    cursor = db.cursor()

    query = """
        SELECT k.id AS friend_id, 
               k.spot_nickname AS nickname, 
               k.photo AS profile_url, 
               k.one_line AS comment, 
               k.spot_id, 
               f.updated_at,
               f.status
        FROM friend f
        JOIN kakao_mem k ON f.friend_id = k.id
        WHERE f.member_id = %s
          AND f.status = 'friend'
        ORDER BY f.updated_at DESC
    """

    query_mutual = """
        SELECT k.photo
        FROM friend f1
        JOIN friend f2 ON f1.friend_id = f2.friend_id
        JOIN kakao_mem k ON f1.friend_id = k.id
        WHERE f1.member_id = %s        -- 나
          AND f2.member_id = %s        -- 해당 친구
          AND f1.status = 'friend'
          AND f2.status = 'friend'
        ORDER BY f1.updated_at DESC
    """

    try:
        # 전체 목록 조회
        cursor.execute(query, (user_id,))
        friends = cursor.fetchall()

        for friend in friends:
            target_friend_id = friend['friend_id']

            # 공통 친구 확인
            cursor.execute(query_mutual, (user_id, target_friend_id))
            mutuals = cursor.fetchall() 

            mutual_count = len(mutuals)
            top_3_profiles = [m['photo'] for m in mutuals[:3]]

            friend['mutual_count'] = mutual_count
            friend['mutual_profiles'] = top_3_profiles

        return jsonify({'friends': friends}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

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

# 친구 신고 기능 - Q. 기능 확정되는 대로 수정해야함
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
        # kakao_mem 테이블의 id를 참조하는 user_report 테이블 필요할 듯?
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
    summary: 특정 유저 차단 (기존 친구 관계여도 차단됨)
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 차단할 대상의 유저 ID
    responses:
      201:
        description: 차단 성공
        schema:
          type: object
          properties:
            message:
              type: string
              example: "User blocked successfully"
      409:
        description: 이미 차단된 사용자
      500:
        description: 서버 에러
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({"message": "user_id is required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # 1. 차단 목록 status update
        # member_id, friend_id, status 순서로 매핑
        query = """
        INSERT INTO friend (member_id, friend_id, status, created_at, updated_at)
        VALUES (%s, %s, 'block', NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            status = 'block',
            updated_at = NOW()
        """
        cursor.execute(query, (user_id, friend_id))

        db.commit()

        return jsonify({"message": "User blocked successfully"}), 201

    except pymysql.err.IntegrityError:
        return jsonify({"message": "Already blocked"}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

# 친구 차단 해제
@bp.route('/friends/unblock/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_friend_unblock(friend_id):
    """
    친구 차단 해제하기
    ---
    tags:
      - Friend
    summary: 차단 해제하고 친구 관계 삭제
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 차단 해제할 대상의 유저 ID
    responses:
      200:
        description: 차단 해제 및 관계 삭제 성공
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Unblocked and relationship deleted"
      500:
        description: 서버 에러
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({"message": "user_id is required"}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # 친구 테이블에서 아예 삭제
        query = """
            DELETE FROM friend
            WHERE member_id = %s AND friend_id = %s AND status = 'block'
        """
        cursor.execute(query, (user_id, friend_id))

        db.commit()
        return jsonify({"message": "Unblocked and relationship deleted"}), 200

    except pymysql.err.IntegrityError:
        return jsonify({"message": "Already unblocked"}), 409
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
            dist = calculate_distance(current_lat, current_lng, row['latitude'], row['longitude'])
            
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
                "distance": dist,
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

# 친구가 남긴 코멘트 전체 조회
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
    cursor = db.cursor()

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

# 친구 팔로우 요청하기
@bp.route('/friends/follow/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_request_follow(friend_id):
    """
    친구 팔로우 요청 보내기
    ---
    tags:
      - Friend
    summary: 다른 유저에게 팔로우 요청 보냄
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 팔로우를 요청할 상대방의 유저 ID
    responses:
      201:
        description: 요청 성공 (waiting 상태 생성)
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Send follow"
            friend_id:
              type: integer
      400:
        description: 자기 자신을 팔로우할 수 없음
      409:
        description: 이미 요청했거나 친구 관계임
      500:
        description: 서버 에러
    """
    user_id = get_jwt_identity() 

    print(f"User ID: {user_id} (Type: {type(user_id)})")
    print(f"Friend ID: {friend_id} (Type: {type(friend_id)})")
    
    if int(user_id) == int(friend_id):
        return jsonify({'message': 'It is impossible to follow yourself'}), 400

    db = get_db()
    cursor = db.cursor()

    try:
        # 이미 요청했거나 친구인지 확인
        query = """
            SELECT status FROM friend
            WHERE member_id = %s AND friend_id = %s
        """
        cursor.execute(query, (user_id, friend_id))
        existing_relation = cursor.fetchone()

        if existing_relation:
            return jsonify({'message': f"Already {existing_relation['status']} status"}), 409

        # 상대방 상태엔 DB에 'waiting' 상태로 저장
        waiting_query = """
            INSERT INTO friend (member_id, friend_id, status, created_at, updated_at) 
            VALUES (%s, %s, 'waiting', NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                status = 'waiting',
                updated_at = NOW()
        """
        cursor.execute(waiting_query, (friend_id, user_id))

        # 내가 보낸 건 DB에 'give' 상태로 저장
        giving_query = """
            INSERT INTO friend (member_id, friend_id, status, created_at, updated_at) 
            VALUES (%s, %s, 'give', NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                status = 'give',
                updated_at = NOW()
        """
        cursor.execute(giving_query, (user_id, friend_id))
        
        db.commit()

        return jsonify({'message': 'Send follow', 'friend_id': friend_id}), 201

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

# 친구 팔로우 수락하기
@bp.route('/friends/access_follow/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_accept_follow(friend_id):
    """
    친구 팔로우 수락하기
    ---
    tags:
      - Friend
    summary: 나에게 온 팔로우 요청 수락
    description: friend_id에는 '나에게 요청을 보낸 사람'의 ID를 넣어야 함
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 요청을 보낸 사람의 유저 ID (Requester ID)
    responses:
      200:
        description: 수락 성공
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Follow access"
            friend_id:
              type: integer
      404:
        description: 대기 중인 요청이 없음
      500:
        description: 서버 에러
    """
    user_id = get_jwt_identity()
    
    db = get_db()
    cursor = db.cursor()

    try:
        # 상대방이 나에게 보낸 'waiting' 요청이 있는지 확인
        # user_id가 요청한 사람(friend_id), friend_id가 나(user_id)여야 함
        query = """
            SELECT * FROM friend
            WHERE member_id = %s AND friend_id = %s AND status = 'waiting'
        """
        cursor.execute(query, (user_id, friend_id))
        request_exist = cursor.fetchone()

        if not request_exist:
            return jsonify({'message': 'There are no pending follow requests'}), 404

        # 상태를 'friend'로 업데이트 (팔로우 허락)
        giving_query = """
        UPDATE friend
        SET status = 'friend', updated_at = NOW()
        WHERE member_id = %s AND friend_id = %s
        """
        cursor.execute(giving_query, (friend_id, user_id))
        
        waiting_query = """
        UPDATE friend
        SET status = 'friend', updated_at = NOW()
        WHERE member_id = %s AND friend_id = %s
        """
        cursor.execute(waiting_query, (user_id, friend_id))
        db.commit()

        return jsonify({'message': 'Follow access', 'friend_id': friend_id}), 200

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
    
# 친구 팔로우 거절하기
@bp.route('/friends/decline_follow/<int:friend_id>', methods=['POST'])
@jwt_required()
def post_decline_follow(friend_id):
    """
    친구 팔로우 거절하기
    ---
    tags:
      - Friend
    summary: 나에게 온 팔로우 요청 거절 (데이터 삭제)
    security:
      - Bearer: []
    parameters:
      - name: friend_id
        in: path
        type: integer
        required: true
        description: 요청을 보낸 사람의 유저 ID
    responses:
      200:
        description: 거절 성공 (데이터 삭제됨)
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Declining follow success"
            friend_id:
              type: integer
      404:
        description: 대기 중인 요청이 없음
      500:
        description: 서버 에러
    """
    user_id = get_jwt_identity()
    
    db = get_db()
    cursor = db.cursor()

    try:
        waiting_query = """
        DELETE FROM friend
        WHERE member_id = %s AND friend_id = %s AND status = 'waiting'
        """
        cursor.execute(waiting_query, (user_id, friend_id))

        giving_query = """
            DELETE FROM friend
            WHERE member_id = %s AND friend_id = %s AND status = 'give'
        """
        cursor.execute(giving_query, (friend_id, user_id))
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({'message': 'There are no pending follow requests'}), 404

        return jsonify({
            "message": "Declining follow success",
            "friend_id": friend_id
        }), 200

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

        if not user_id:
          return jsonify({"message": "user_id required"}), 400

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
    
# 전체 저장 장소 목록 조회
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

  # 현재 위치 파라미터 가져오기
    try:
        current_lat = request.args.get("lat", type=float)
        current_lng = request.args.get("lng", type=float)
    except (TypeError, ValueError):
        current_lat, current_lng = None, None

    friends = Friend.query.filter_by(member_id=user_id, status='friend').all() # friend_id 검색
    friend_ids = [f.friend_id for f in friends]

    # 친구가 한 명도 없는 경우 빈 리스트 반환
    if not friend_ids:
        return jsonify([]), 200
    
    # 정렬 및 필터 파라미터
    sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")

    # 유효한 카테고리 목록
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

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
            p.list AS category,
            p.photo,
            p.rating_avg AS ratingAvg,
            p.rating_count AS ratingCount,
            sp.rating AS friendRating,
            sp.updated_at,
            k.spot_nickname AS friend_nickname,
            k.photo AS friend_photo,
            CASE WHEN my_sp.id IS NOT NULL THEN TRUE ELSE FALSE END AS isMarked
        FROM saved_place sp
        JOIN place p ON sp.place_id = p.id
        JOIN kakao_mem k ON sp.user_id = k.id
        LEFT JOIN saved_place my_sp ON p.id = my_sp.place_id AND my_sp.user_id = %s
        WHERE sp.user_id IN ({})
    """.format(', '.join(['%s'] * len(friend_ids)))
    
    # 내 아이디: JOIN용, 친구 아이디 : WHERE용
    params = [user_id] + friend_ids

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

    # 데이터 없으면 바로 리턴
    if not rows:
        return jsonify([]), 200
    
    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            dist = calculate_distance(current_lat, current_lng, row['latitude'], row['longitude'])

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
                "myRating": row['friendRating'], # 가장 최근 혹은 정렬된 첫 번째 친구의 별점
                "isMarked": bool(row['isMarked']),
                "distance": dist,
                "saversCount": 0,
                "savers": []
                
            }
        
        # 중복 방지를 위해 savers 추가 (이미 추가된 친구인지 체크 가능)
        places_dict[pid]["savers"].append({
            "nickname": row['friend_nickname'],
            "profileImageUrl": row['friend_photo'] if row['friend_photo'] else "",
            "updated_at": row['updated_at'] # 정렬용 임시 데이터
        })

        result_list = list(places_dict.values())
    
    for place in result_list:
        place["savers"].sort(key=lambda x: x['updated_at'], reverse=True)
        place["saversCount"] = len(place["savers"])
        
        for saver in place["savers"]:
            del saver['updated_at']

    return jsonify(result_list), 200

# 내 저장 장소 목록 조회
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
      - name: sort
        in: query
        type: string
        enum: [latest, star]
        default: latest
        description: 내 저장 일시 기준(latest) 또는 내 별점 기준(star)
      - name: category
        in: query
        type: string
        enum: [accessory, bar, cafe, cloth, etc, restaurant, dessert, exhibition, experience]
        description: 카테고리 필터
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
              name:
                type: string
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

    friends = Friend.query.filter_by(member_id=user_id, status='friend').all()
    friend_ids = [f.friend_id for f in friends]

    sort_by = request.args.get("sort", "latest")
    category_filter = request.args.get("category")
    valid_categories = ["accessory", "bar", "cafe", "cloth", "etc", "restaurant", "dessert", "exhibition", "experience"]

    db = get_db()
    cursor = db.cursor()

    # 2. 쿼리 작성
    # - 메인: 내가(user_id) 저장한 saved_place (my_sp)
    # - 조인: 해당 장소를 저장한 친구들의 정보 (f_sp, f_k)
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
            my_sp.rating AS myRating,
            my_sp.updated_at AS my_updated_at,
            f_k.spot_nickname AS friend_nickname,
            f_k.photo AS friend_photo,
            f_sp.updated_at AS friend_updated_at
        FROM saved_place my_sp
        JOIN place p ON my_sp.place_id = p.id
        LEFT JOIN saved_place f_sp ON p.id = f_sp.place_id AND f_sp.user_id IN ({})
        LEFT JOIN kakao_mem f_k ON f_sp.user_id = f_k.id
        WHERE my_sp.user_id = %s
    """.format(', '.join(['%s'] * len(friend_ids)) if friend_ids else "NULL")
    
    params = friend_ids + [user_id]

    if category_filter and category_filter in valid_categories:
        query += " AND p.list = %s"
        params.append(category_filter)

    if sort_by == "star":
        query += " ORDER BY my_sp.rating DESC, my_sp.updated_at DESC"
    else:
        query += " ORDER BY my_sp.updated_at DESC"

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    places_dict = {}
    for row in rows:
        pid = row['placeId']
        if pid not in places_dict:
            dist = calculate_distance(current_lat, current_lng, row['latitude'], row['longitude'])

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
                "distance": dist,
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

    return jsonify(result_list), 200

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