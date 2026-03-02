import redis
import os
from datetime import datetime, timedelta

# EC2 내부에 띄운 로컬 Redis에 접속
REDIS_HOST = os.getenv("REDIS_HOST")
redis_client = redis.Redis(host=REDIS_HOST, port=6379, db=0, decode_responses=True)

def check_abuse_and_rate_limit(user_id):
    """10분 차단 여부 및 분당 요청 횟수 체크"""
    # 차단 여부 확인
    if redis_client.get(f"block:{user_id}"):
        return False, "연속 실패로 인해 10분간 요청이 차단되었습니다."
    
    # 10분당 6회 제한 확인(5~7)
    rate_key = f"rate_limit:{user_id}"
    current_req = redis_client.get(rate_key)
    
    if current_req and int(current_req) >= 7:
        return False, "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."
        
    if not current_req:
        redis_client.set(rate_key, 1, ex=60)
    else:
        redis_client.incr(rate_key)
        
    return True, "OK"

def handle_fail_count(user_id):
    """실패 시 카운트 증가 및 5회 도달 시 차단"""
    fail_key = f"fail_count:{user_id}"
    current_fail = redis_client.incr(fail_key)
    
    if current_fail == 1:
        redis_client.expire(fail_key, 600) # 10분 내 연속 실패만 카운트
        
    if current_fail >= 5:
        redis_client.set(f"block:{user_id}", "blocked", ex=600)
        redis_client.delete(fail_key)

def add_score_and_check_ad(user_id, score_to_add):
    """점수 누적 및 광고 노출 여부 반환 (자정 초기화)"""
    # 다음 광고 목표 점수 (10 + 7n)
    score_key = f"user_score:{user_id}"
    target_key = f"ad_target:{user_id}" 
       
    # 자정까지 남은 시간 계산
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = int((tomorrow - now).total_seconds())

    # 점수 더하기
    if not redis_client.exists(score_key):
        redis_client.set(score_key, score_to_add, ex=ttl)
        redis_client.set(target_key, 10, ex=ttl) # 초기 목표 10점
        current_score = score_to_add
    else:
        current_score = redis_client.incrbyfloat(score_key, score_to_add)

    # 광고 조건 확인
    target_score = float(redis_client.get(target_key) or 10)
    show_ad = False
    
    if current_score >= target_score:
        show_ad = True
        redis_client.incrbyfloat(target_key, 7) # 다음 목표 +7점
        
    return show_ad