from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, KakaoMem

bp = Blueprint("profile", __name__, url_prefix='/profile')

from services.my_logger import get_my_logger
logger = get_my_logger(__name__)

@bp.route("/id_check", methods=["GET"])
@jwt_required()
def check_spot_id():
    """
    Spot ID 중복 확인 API
    ---
    tags:
      - Profile
    summary: "유저의 spot_id 중복 여부를 검사합니다."
    description: "새로운 spot_id를 설정하기 전, 기존 DB에 동일한 아이디가 있는지 확인합니다."
    security:
      - Bearer: []  # Swagger UI에서 자물쇠 아이콘 활성화를 위해 필요
    parameters:
      - in: query
        name: spot_id
        type: string
        required: true
        description: "중복을 확인할 아이디 (예: my_spot)"
    responses:
      200:
        description: "조회 성공"
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            can_use_id:
              type: boolean
              example: true
            message:
              type: string
              example: "사용 가능한 ID입니다."
      400:
        description: "파라미터 누락"
        schema:
          type: object
          properties:
            status:
              type: string
              example: "error"
            message:
              type: string
              example: "spot_id required"
      401:
        description: "인증 실패 (토큰 누락 및 만료)"
    """
    # get. spot_id
    user_id = get_jwt_identity() 
    spot_id = request.args.get('spot_id')

    if not user_id:
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    # spot_id 값이 안 들어왔을 때의 예외 처리
    if not spot_id:
            return jsonify({'status': 'error', 'message': 'spot_id required'}), 400
    
    # spot_id 존재 유무 확인
    existing_spot_id = KakaoMem.query.filter_by(spot_id=spot_id).first()

    # 존재 확인 후 bool로 리턴
    use_id = existing_spot_id is None

    return jsonify({
        "status": "success",
        "can_use_id": use_id,
        "message": "사용 가능한 ID입니다." if use_id else "이미 존재하는 ID입니다."
    }), 200