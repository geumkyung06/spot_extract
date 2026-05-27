from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Device

from services.my_logger import get_my_logger

bp = Blueprint('notification', __name__)
logger = get_my_logger(__name__)

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