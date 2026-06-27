import os
import pymysql
import random
import math
import threading

from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from services.push_notification import send_expo_push_notification
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
    user_id = int(get_jwt_identity())
    
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
    user_id = int(get_jwt_identity())

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
    user_id = int(get_jwt_identity())
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
    user_id = int(get_jwt_identity())

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
    user_id = int(get_jwt_identity())

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
    user_id = int(get_jwt_identity())

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

        waiting_query = """
            INSERT INTO friend (member_id, friend_id, status, created_at, updated_at) 
            VALUES (%s, %s, 'waiting', NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                status = 'waiting',
                updated_at = NOW()
        """
        cursor.execute(waiting_query, (user_id, friend_id))
        
        db.commit()
        # 푸시 알림

        # 알림 저장
        noti_query = """
            INSERT INTO notifications (user_id, sender_id, type, created_at)
            VALUES (%s, %s, 'follow_request', NOW())
        """
        cursor.execute(noti_query, (friend_id, user_id))
        db.commit()
  
        # 알림 메시지용: "OOO님이 팔로우를 요청했습니다"
        cursor.execute("SELECT spot_nickname FROM kakao_mem WHERE id = %s", (user_id,))
        my_info = cursor.fetchone()
        my_nickname = my_info['spot_nickname'] if my_info else "누군가"

        token_query = """
            SELECT expo_push_token FROM devices 
            WHERE user_id = %s AND is_active = 1 AND expo_push_token IS NOT NULL
        """
        cursor.execute(token_query, (friend_id,))
        target_device = cursor.fetchone()

        # 비동기 실행
        if target_device and target_device['expo_push_token']:
            target_token = target_device['expo_push_token']
            title = "새로운 팔로우 요청"
            body = f"{my_nickname}님이 팔로우를 요청했습니다."
            
            thr = threading.Thread(
                target=send_expo_push_notification, 
                args=(target_token, title, body)
            )
            thr.start()

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
    user_id = int(get_jwt_identity())
    
    db = get_db()
    cursor = db.cursor()

    try:
        # 상대방이 나에게 보낸 'waiting' 요청이 있는지 확인
        # user_id가 요청한 사람(friend_id), friend_id가 나(user_id)여야 함
        query = """
            SELECT * FROM friend
            WHERE member_id = %s AND friend_id = %s AND status = 'waiting'
        """
        cursor.execute(query, (friend_id, user_id))
        request_exist = cursor.fetchone()

        if not request_exist:
            return jsonify({'message': 'There are no pending follow requests'}), 404

        # 상태를 'friend'로 업데이트 (팔로우 허락)
        waiting_query = """
        UPDATE friend
        SET status = 'friend', updated_at = NOW()
        WHERE member_id = %s AND friend_id = %s
        """
        cursor.execute(waiting_query, (friend_id, user_id))

        # 푸시 알림
        db.commit()

        # 알림 저장 (수신자: 요청 보낸 사람 friend_id, 발신자: 수락한 나 user_id)
        noti_query = """
            INSERT INTO notifications (user_id, sender_id, type, created_at)
            VALUES (%s, %s, 'follow_accept', NOW())
        """
        cursor.execute(noti_query, (friend_id, user_id))
        db.commit()
        
        # 알림 메시지용: "OOO님이 팔로우를 수락했습니다"
        cursor.execute("SELECT spot_nickname FROM kakao_mem WHERE id = %s", (friend_id,))
        my_info = cursor.fetchone()
        my_nickname = my_info['spot_nickname'] if my_info else "누군가"

        token_query = """
            SELECT expo_push_token FROM devices 
            WHERE user_id = %s AND is_active = 1 AND expo_push_token IS NOT NULL
        """
        cursor.execute(token_query, (user_id,))
        target_device = cursor.fetchone()

        # 비동기 실행
        if target_device and target_device['expo_push_token']:
            target_token = target_device['expo_push_token']
            title = "팔로우 수락"
            body = f"{my_nickname}님이 팔로우를 수락했습니다."
            
            thr = threading.Thread(
                target=send_expo_push_notification, 
                args=(target_token, title, body)
            )
            thr.start()

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
    user_id = int(get_jwt_identity())
    
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