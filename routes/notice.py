import os
import pymysql
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Device

from services.my_logger import get_my_logger

bp = Blueprint('notification', __name__)
logger = get_my_logger(__name__)

"""
로그아웃/만료 토큰 비활성화 DELETE /push-tokens
알림 읽음 처리 POST /notifications/read
안 읽은 알람 개수 GET /notifications/unread-count
"""

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

@bp.route('/push-tokens', methods=['POST'])
@jwt_required()
def save_push_token():
    """
    푸시 알림 기기 토큰 저장
    ---
    tags:
      - Notification
    summary: 사용자 기기의 푸시 토큰 정보를 저장하거나 업데이트
    description: 앱 실행 시 발급받은 Expo Push Token을 서버에 저장. 동일한 토큰이 이미 존재하면 기기 정보를 업데이트하고, 없으면 새로 생성
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - in: body
        name: body
        description: 푸시 토큰 및 기기 정보
        required: true
        schema:
          type: object
          required:
            - expo_push_token
          properties:
            expo_push_token:
              type: string
              description: Expo에서 발급받은 고유 푸시 토큰
            device_type:
              type: string
              description: 기기 운영체제 타입
              example: "ios"
            app_version:
              type: string
              description: 설치된 앱의 버전 정보
            is_active:
              type: boolean
              description: 알림 수신 동의 여부 (기본값 True)
              default: true
    responses:
      200:
        description: 토큰 저장 또는 업데이트 성공
        schema:
          type: object
          properties:
            message:
              type: string
              example: "푸시 토큰이 성공적으로 저장되었습니다."
      400:
        description: 필수 파라미터 누락 (expo_push_token 등)
        schema:
          type: object
          properties:
            error:
              type: string
              example: "토큰이 필요합니다."
      500:
        description: 서버 내부 에러 (DB 저장 실패 등)
    """
    user_id = get_jwt_identity()
    
    if not user_id :
        return jsonify({'error': 'user_id is required'}), 400
    
    data = request.json
    token = data.get('expo_push_token')
    device_type = data.get('device_type')
    app_version = data.get('app_version')
    is_active = data.get('is_active', True)

    if not token:
        return jsonify({"error": "토큰이 필요합니다."}), 400

    # 유저 정보 확인
    device = Device.query.filter_by(
        user_id=user_id, 
        expo_push_token=token
    ).first()

    logger.debug(f"현재 device: {device}")

    if device:
        device.device_type = device_type
        device.app_version = app_version
        device.is_active = is_active

        logger.debug(f"업데이트 정보: {device}")
    else:
        new_device = Device(
            user_id=user_id,
            expo_push_token=token,
            device_type=device_type,
            app_version=app_version,
            is_active=is_active
        )

        logger.debug(f"업데이트 정보: {new_device}")
        db.session.add(new_device)

    # DB 저장
    db.session.commit()

    return jsonify({"message": "푸시 토큰이 성공적으로 저장되었습니다."}), 200

@bp.route('/push-tokens', methods=['DELETE'])
@jwt_required()
def delete_push_token():
    """
    로그아웃/만료 토큰 비활성화
    ---
    tags:
      - Notification
    summary: 푸시 토큰 비활성화
    description: 로그아웃 또는 토큰 만료 시 해당 기기의 푸시 토큰을 비활성화.
                 expo_push_token을 전달하면 해당 토큰만, 없으면 유저의 모든 토큰 비활성화
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - in: body
        name: body
        description: 비활성화할 푸시 토큰 (선택)
        required: false
        schema:
          type: object
          properties:
            expo_push_token:
              type: string
              description: 비활성화할 특정 토큰 (없으면 전체 비활성화)
    responses:
      200:
        description: 토큰 비활성화 성공
      404:
        description: 토큰을 찾을 수 없음
      500:
        description: 서버 내부 에러
    """
    user_id = get_jwt_identity()

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400
        
    data = request.get_json(silent=True) or {}
    token = data.get('expo_push_token')

    try:
        if token:
            # 특정 토큰만 비활성화
            device = Device.query.filter_by(
                user_id=user_id,
                expo_push_token=token
            ).first()

            if not device:
                return jsonify({"error": "토큰을 찾을 수 없습니다."}), 404

            device.is_active = False
            logger.debug(f"토큰 비활성화: user_id={user_id}, token={token}")

        else:
            # 유저의 모든 토큰 비활성화 (전체 로그아웃)
            updated = Device.query.filter_by(
                user_id=user_id,
                is_active=True
            ).update({"is_active": False})

            logger.debug(f"전체 토큰 비활성화: user_id={user_id}, count={updated}")

            if updated == 0:
                return jsonify({"error": "활성화된 토큰이 없습니다."}), 404

        db.session.commit()
        return jsonify({"message": "푸시 토큰이 비활성화되었습니다."}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"토큰 비활성화 실패: {e}")
        return jsonify({"error": "서버 오류가 발생했습니다."}), 500

# 알림창 들어갈 때 전체 읽음으로 변경
@bp.route('/notifications/read', methods=['POST'])
@jwt_required()
def check_read_notification():
    user_id = get_jwt_identity()

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            UPDATE notifications
            SET is_read = TRUE
            WHERE user_id = %s AND is_read = FALSE
        """, (user_id,))

        db.commit()
        return jsonify({"message": "읽음 처리 완료"}), 200

    except Exception as e:
        db.rollback()
        logger.error(f"읽음 처리 실패: {e}")
        return jsonify({"error": "서버 오류"}), 500
    finally:
        cursor.close()


@bp.route('/notifications/unread-count', methods=['GET'])
@jwt_required()
def read_unread_notification():
    user_id = get_jwt_identity()

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            SELECT COUNT(*) AS cnt
            FROM notifications
            WHERE user_id = %s AND is_read = FALSE
        """, (user_id,))
        row = cursor.fetchone()
        unread_count = row['cnt'] if row else 0

        return jsonify({"unread_count": unread_count}), 200

    except Exception as e:
        logger.error(f"미읽음 조회 실패: {e}")
        return jsonify({"error": "서버 오류"}), 500
    finally:
        cursor.close()

# GET /notification >> 알림창에 알림정보들 떠야함 - user_id가 받은 알림들만
# friend_id, 프로필사진, spot_id, spot_nickname, 한줄소개 필요
@bp.route('', methods=['GET'])
@jwt_required()
def check_notification():
    user_id = get_jwt_identity()

    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute("""
            SELECT 
                n.id            AS notification_id,
                n.type,
                n.is_read,
                n.created_at,
                m.id            AS sender_id,
                m.photo,
                m.spot_id,
                m.spot_nickname,
                m.one_line
            FROM notifications n
            JOIN kakao_mem m ON m.id = n.sender_id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
        """, (user_id,))

        rows = cursor.fetchall()
        return jsonify({"notifications": rows}), 200

    except Exception as e:
        logger.error(f"알림 조회 실패: {e}")
        return jsonify({"error": "서버 오류"}), 500
    finally:
        cursor.close()