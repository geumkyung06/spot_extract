import base64
import hashlib
import json
import requests
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
    signature = base64.urlsafe_b64decode(request.args.get("signature") + "==")

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
    is_caption_post = False
    url_id, caption, db_places = check_db_have_url(shortcut)
    db_caption = bool(caption)

    if db_places:
        logger.info("[1] DB 캐시 존재")
        earned_score = 0.1 # 추출 전적 존재 0.1점
    else:
        logger.info("[2] 캡션 분석 시도")
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
        is_caption_post = extract_places_with_gpt(caption) 

        if is_caption_post == []: # is_caption_post 리스트형태 / 장소 없으면 빈 리스트
            logger.info("[3] OCR 시도...")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                img_count = await extract_post_data(page, url)
                await page.close()
                await browser.close()

            if img_count == -1:
                img_count = 2  # 캐러셀 확인됐지만 정확한 개수 파악 실패 시 최소값으로 처리
            earned_score = 1.0 * img_count
        else :
            earned_score = 0.2 # caption

    redis_client.delete(f"fail_count:{user_id}")

    end = time.time()
    logger.debug(f"time: {end-start: .2f}s")

    current_score, target_score, need_ad = peek_score_and_target(user_id, earned_score)
    ticket_id = None
    if need_ad:
        ticket_id = create_ad_ticket(user_id, earned_score)
    else:
        current_score = commit_score(user_id, earned_score)

    return jsonify({
        'score_cost': earned_score,
        'current_score': current_score,
        'need_ad': need_ad,
        'ticket_id': ticket_id,
    }), 200

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
    if not verify_ssv(request):
        return "invalid signature", 400

    tx_id = request.args.get("transaction_id")
    ticket_id = request.args.get("custom_data")
    if not ticket_id:
        return "missing ticket", 400

    if redis_client.set(f"admob_tx:{tx_id}", "1", nx=True, ex=86400):
        result = verify_ad_ticket(ticket_id)
        if result is None:
            logger.warning(f"ad ticket 검증 실패 또는 만료: {ticket_id}")

    return "", 200

# GET /ads/ticket/{ticket_id}/status
# 클라가 광고 닫힌 후 폴링(1~2초 간격, 몇 번 안 되면 timeout 처리). verified 확인하며 redis에 정보 업데이트 진행
