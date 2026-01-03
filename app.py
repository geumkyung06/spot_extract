import os
import pymysql
from urllib.parse import quote_plus
from flask import Flask
from flask_cors import CORS  # 프론트엔드 연동을 위해 필요 (선택사항)
from dotenv import load_dotenv

# 1. 모델과 DB 객체 임포트 (models.py에서 만든 db 객체 가져오기)
from routes.models import db

# 2. 라우트 임포트 (우리가 만든 routes 폴더 안의 파일들)
from routes.instagram import bp as instagram_bp
from routes.places import user_places_bp
from routes.friend import bp as friend_bp

# .env 파일 로드
load_dotenv()

# pymysql 설정 (MySQL 드라이버)
pymysql.install_as_MySQLdb()

def create_app():
    app = Flask(__name__)
    
    # CORS 설정 (프론트엔드에서 API 호출 허용)
    CORS(app) 

    # ---------------------------------------------------------
    # 3. 데이터베이스 설정 (AWS RDS)
    # ---------------------------------------------------------
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "3306")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME")

    # 비밀번호 특수문자 처리 (URL Encoding)
    if db_pass:
        encoded_pass = quote_plus(db_pass)
    else:
        encoded_pass = ""

    # SQLAlchemy 설정
    db_uri = f"mysql+pymysql://{db_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JSON_AS_ASCII'] = False # 한글 깨짐 방지

    # ---------------------------------------------------------
    # 4. DB 초기화 (App과 연결)
    # ---------------------------------------------------------
    db.init_app(app)

    # ---------------------------------------------------------
    # 5. 블루프린트 등록
    # ---------------------------------------------------------
    app.register_blueprint(instagram_bp)  
    app.register_blueprint(user_places_bp)
    app.register_blueprint(friend_bp)

    return app

# 메인 실행 블록
if __name__ == '__main__':
    app = create_app()

    with app.app_context():
        try:
            db.create_all()
            print("AWS RDS 데이터베이스 연결 및 테이블 생성 완료!")
        except Exception as e:
            print(f"DB 연결 실패: {e}")

    app.run(host='0.0.0.0', port=5000, debug=True)