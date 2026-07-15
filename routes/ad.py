import base64
import hashlib
import json
import requests
import os
from ecdsa import VerifyingKey, BadSignatureError
from ecdsa.util import sigdecode_der
from flask_jwt_extended import jwt_required, get_jwt_identity
import time
from playwright.async_api import async_playwright
from flask import Blueprint, request, jsonify

from services.redis_helper import redis_client, check_abuse_and_rate_limit, handle_fail_count, add_score_and_check_ad, peek_score_and_target, create_ad_ticket, commit_score, verify_ad_ticket
from services.my_logger import get_my_logger
from routes.instagram import extract_shortcode, check_db_have_url
from services.instagram_text_parser import get_caption_no_login, extract_places_with_gpt, is_place_post
from services.check_post import extract_post_data

# models 파일에서 정의한 클래스들 임포트
from models import db, Place, InstaUrl, UrlPlace

bp = Blueprint('ads', __name__)
logger = get_my_logger(__name__)

KEY_SERVER_URL = "https://www.gstatic.com/admob/reward/verifier-keys.json"

def get_public_keys():
    # 공개키는 24시간 이내 캐시 권장 (수시로 로테이션됨)
    cached = redis_client.get("admob:public_keys")
    if cached:
        return json.loads(cached)
    keys = requests.get(KEY_SERVER_URL, timeout=5).json()["keys"]
    redis_client.set("admob:public_keys", json.dumps(keys), ex=60 * 60 * 12)
    return keys

def verify_ssv(request):
    qs = request.query_string.decode()
    if "&signature=" not in qs:
        return False
    content, _, _ = qs.partition("&signature=")  # signature 직전까지가 서명 대상

    key_id = int(request.args.get("key_id"))
    sig_str = request.args.get("signature")
    sig_str += "=" * (-len(sig_str) % 4)
    signature = base64.urlsafe_b64decode(sig_str)

    key_info = next((k for k in get_public_keys() if k["keyId"] == key_id), None)
    if not key_info:
        return False

    vk = VerifyingKey.from_pem(key_info["pem"])
    try:
        vk.verify(signature, content.encode(), hashfunc=hashlib.sha256, sigdecode=sigdecode_der)
        return True
    except BadSignatureError:
        return False

@bp.route("/extract/eligibility", methods=["POST"])
@jwt_required()
async def extract_eligibility():
    """
    게시물 추출 가능 여부 및 점수 확인
    ---
    tags:
      - Ad
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - url
          properties:
            url:
              type: string
              example: "https://www.instagram.com/p/ABC123/"
    responses:
      200:
        description: 점수 계산 및 광고 필요 여부
        schema:
          type: object
          properties:
            score_cost:
              type: number
              example: 0.2
            current_score:
              type: number
              example: 9.9
            need_ad:
              type: boolean
              example: true
            ticket_id:
              type: string
              example: "3f2a1b4c5d6e7f8a9b0c1d2e3f4a5b6c"
      400:
        description: URL 누락 또는 장소 게시물 아님
      401:
        description: 인증 실패
      429:
        description: 요청 한도 초과
    """
    # 입력: 게시물 URL 로직: 이미 추출된 게시물인지 확인 → 이번 추출로 증가할 점수 계산 → 현재 잔여 점수 조회 응답: { score_cost, current_score, need_ad: bool, ticket_id } — need_ad가 true면 ticket_id도 같이 발급해서 광고에 실어 보냄
    user_id = int(get_jwt_identity())

    if not user_id:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    
    is_allowed, msg = check_abuse_and_rate_limit(user_id)
    if not is_allowed:
        return jsonify({'status': 'error', 'message': msg}), 429
    
    data = request.get_json()
    url = data.get('url') # 프론트한테서 받아옴
    start = time.time()
    post_type, shortcut = extract_shortcode(url)
    logger.info(f"url: {url}, shortcut: {shortcut}")

    if not shortcut:
        return jsonify({'status': 'error', 'message': 'URL is required'}), 400

    url = f"https://www.instagram.com/p/{shortcut}"
    logger.info(f"[Start] 분석 시작: {url}")

    earned_score = 0.0

    # 1. DB에 이미 URL이 있는지 확인
    logger.debug("DB에 존재하는 게시물인지 확인 중...")
    caption_place = [] # 리스트 형태
    extract_type = ""
    url_id, caption, db_places = check_db_have_url(shortcut)
    db_caption = bool(caption)

    if db_places:
        logger.info("[1] DB 캐시 존재 - 추출 전적 존재 0.1점")
        earned_score = 0.1 # 추출 전적 존재 0.1점
        extract_type = "db"
    else:
        logger.debug("[2] 캡션 분석 시도")
        # 2. 장소 확인 후 프론트에게 보낼 장소 정보 준비
        # Q. 가능하면 네이버 검색 돌리기 전에 저장되어있는지 파악하는게 좋을 듯
        # 캡션 추출
        if not db_caption:
            caption = await get_caption_no_login(url)
            if not caption:
                return jsonify({'status': 'error', 'message': 'No caption'}), 400
            is_place = is_place_post(caption)
            if not is_place:
                handle_fail_count(user_id)
                return jsonify({'status': 'error', 'message': "It is not a place post"}), 400

        # db_caption 이든 방금 새로 가져왔든, 캡션이 있으면 항상 검사
        caption_extract_start = time.time()
        caption_place = extract_places_with_gpt(caption) 
        caption_extract_end = time.time()
        logger.info(f"caption time: {caption_extract_end - caption_extract_start: .2f}s")

        if caption_place == []: # caption_place 리스트형태 / 장소 없으면 빈 리스트
            logger.debug("[3] OCR 시도")
            extract_type = "ocr"
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                img_count = await extract_post_data(page, url)
                await page.close()
                await browser.close()

            if img_count == -1:
                img_count = 2  # 캐러셀 확인됐지만 정확한 개수 파악 실패 시 최소값으로 처리
            earned_score = 1.0 * img_count
            logger.info(f"[3] OCR - {earned_score}점")
        else :
            extract_type = "caption"
            logger.info("[2] 캡션 - 0.2점")
            earned_score = 0.2 # caption

    redis_client.delete(f"fail_count:{user_id}")

    end = time.time()
    logger.debug(f"total time: {end-start: .2f}s")

    current_score, target_score, need_ad = peek_score_and_target(user_id, earned_score)

    ticket_id = None
    if need_ad:
        ticket_id = create_ad_ticket(user_id, earned_score)
    else:
        current_score = commit_score(user_id, earned_score)

    # session_id = user_id user_id로 어떤 게시물 추출하려 했는지 확인
    redis_client.set(f"extract_session:{user_id}",json.dumps({
        "user_id": user_id,
        "shortcut": shortcut,               # db 검색용
        "extract_type": extract_type,       # "db" | "caption" | "ocr"
        "gpt_result": caption_place,           # ocr인 경우 []
        'need_ad': need_ad,
        'ticket_id': ticket_id,
        "caption": caption,                 # DB 저장용
        "url": url,                         # ocr일 때 analyze에서 사용
    }), ex=360)
        
    return jsonify({
        'score_cost': earned_score,
        'current_score': current_score,
        'extract_type': extract_type,
        'need_ad': need_ad,
        'ticket_id': ticket_id,
    }), 200

@bp.route("/debug/force-verify/<ticket_id>", methods=["POST"])
@jwt_required()
def debug_force_verify(ticket_id):
    """
    [개발 전용] 광고 ticket 강제 verify
    ---
    tags:
      - Ad (Debug)
    security:
      - Bearer: []
    parameters:
      - in: path
        name: ticket_id
        type: string
        required: true
        description: eligibility에서 발급된 ticket_id
    responses:
      200:
        description: 강제 verify 처리 완료
        schema:
          type: object
          properties:
            status:
              type: string
              example: verified
      400:
        description: ticket이 pending 상태가 아님 (이미 verified/used)
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: "ticket status is 'used', not pending"
      401:
        description: 인증 실패
      403:
        description: 본인 ticket이 아님
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Forbidden
      404:
        description: 운영 환경이거나 ticket 없음/만료
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: ticket not found or expired
    """
    # 운영 환경에서는 완전히 차단
    if os.getenv("FLASK_ENV") == "production":
        return "Not found", 404

    user_id = int(get_jwt_identity())
    data = redis_client.hgetall(f"ad_ticket:{ticket_id}")

    if not data:
        return jsonify({'status': 'error', 'message': 'ticket not found or expired'}), 404

    # 본인 ticket만 강제 verify 가능
    if str(user_id) != str(data.get("user_id")):
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403

    if data.get("status") != "pending":
        return jsonify({
            'status': 'error',
            'message': f"ticket status is '{data.get('status')}', not pending"
        }), 400

    logger.warning(f"[DEBUG] 강제 verify 처리 - ticket_id={ticket_id}, user_id={user_id}")
    result = verify_ad_ticket(ticket_id)
    after = redis_client.hget(f"ad_ticket:{ticket_id}", "status")
    logger.info(f"[DEBUG] 강제 verify 결과 - ticket_id={ticket_id}, after={after}, result={result}")

    return jsonify({'status': after}), 200
@bp.route("/ssv", methods=["GET"])
def ads_ssv_callback():
    """
    AdMob SSV 콜백 (Google 서버 전용 — 직접 호출 불가)
    ---
    tags:
      - Ad
    parameters:
      - in: query
        name: transaction_id
        type: string
      - in: query
        name: custom_data
        type: string
        description: eligibility에서 발급된 ticket_id
      - in: query
        name: signature
        type: string
      - in: query
        name: key_id
        type: integer
    responses:
      200:
        description: 검증 및 크레딧 처리 완료
      400:
        description: 서명 검증 실패 또는 ticket 누락
    """
    tx_id = request.args.get("transaction_id")
    ad_unit = request.args.get("ad_unit")
    ticket_id = request.args.get("custom_data")
    key_id = request.args.get("key_id")

    logger.info(f"[SSV] 수신 - tx_id={tx_id}, ad_unit={ad_unit}, custom_data={ticket_id}, key_id={key_id}")

    try:
        verified = verify_ssv(request)
    except Exception as e:
        logger.error(f"[SSV] 서명 검증 중 예외 - tx_id={tx_id}, error={e}")
        return "invalid signature", 400

    logger.info(f"[SSV] 서명 검증 결과: {verified}")

    if not verified:
        return "invalid signature", 400

    if not ticket_id:
        logger.warning(f"[SSV] custom_data(ticket) 누락 - tx_id={tx_id}")
        return "missing ticket", 400

    before = redis_client.hget(f"ad_ticket:{ticket_id}", "status")

    if redis_client.set(f"admob_tx:{tx_id}", "1", nx=True, ex=86400):
        result = verify_ad_ticket(ticket_id)
        after = redis_client.hget(f"ad_ticket:{ticket_id}", "status")
        logger.info(f"[SSV] 티켓 상태 변경 - ticket_id={ticket_id}, before={before}, after={after}, result={result}")
        if result is None:
            logger.warning(f"[SSV] ad ticket 검증 실패 또는 만료: {ticket_id}")
    else:
        logger.info(f"[SSV] 중복 tx_id 무시 (idempotent) - tx_id={tx_id}")

    return "", 200


@bp.route("/ads/ticket/<ticket_id>/status", methods=["GET"])
@jwt_required()
def ads_ticket_status(ticket_id):
    """
    광고 ticket 상태 폴링
    ---
    tags:
      - Ad
    security:
      - Bearer: []
    parameters:
      - in: path
        name: ticket_id
        type: string
        required: true
    responses:
      200:
        description: ticket 상태 반환 (pending | verified)
        schema:
          type: object
          properties:
            status:
              type: string
              example: verified
      404:
        description: ticket 없음 또는 만료
    """
    data = redis_client.hgetall(f"ad_ticket:{ticket_id}")
    if not data:
        return jsonify({'status': 'expired'}), 404

    # 다른 유저 ticket 조회 방지
    if str(get_jwt_identity()) != str(data.get("user_id")):
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403

    return jsonify({'status': data.get("status")}), 200