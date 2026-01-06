import os
import sys
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

import pymysql
from urllib.parse import quote_plus
from flask import Flask
from flask_cors import CORS 
from dotenv import load_dotenv
from flasgger import Swagger
from flask_jwt_extended import JWTManager

# 1. 모델과 DB 객체 임포트
from models import db

# 2. 라우트 임포트
from routes.instagram import bp as instagram_bp
from routes.places import user_places_bp
from routes.friend import bp as friend_bp

# .env 파일 로드
load_dotenv()

# pymysql 설정
pymysql.install_as_MySQLdb()



def create_app():
    app = Flask(__name__)

    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET")
    app.config["JWT_ALGORITHM"] = os.getenv("JWT_ALGORITHM")
    jwt = JWTManager(app)
    
    # CORS 설정
    CORS(app, resources={r"*": {"origins": "*"}}) 

    # [중요] 스웨거 초기화 (이게 없으면 Fetch Error 뜸)
    app.config['SWAGGER'] = {
        'title': 'Spot Extract API',
        'uiversion': 3
    }
    swagger = Swagger(app)

    # 3. 데이터베이스 설정
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "3306")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME")

    if db_pass:
        encoded_pass = quote_plus(db_pass)
    else:
        encoded_pass = ""

    db_uri = f"mysql+pymysql://{db_user}:{encoded_pass}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JSON_AS_ASCII'] = False 

    # 4. DB 초기화
    db.init_app(app)

    # 5. 블루프린트 등록
    app.register_blueprint(instagram_bp)  
    app.register_blueprint(user_places_bp)
    app.register_blueprint(friend_bp)

    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        try:
            db.create_all()
            print("AWS RDS 연결 성공!")
        except Exception as e:
            print(f"DB 연결 실패: {e}")

    app.run(host='0.0.0.0', port=8001, debug=True)