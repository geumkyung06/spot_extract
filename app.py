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
from services.browser import browser_service
import asyncio
import logging

# 모델과 DB 객체 임포트
from models import db

load_dotenv()

# 라우트 임포트
from routes.instagram import bp as instagram_bp
from routes.places import user_places_bp
from routes.friend import bp as friend_bp

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)

# pymysql 설정
pymysql.install_as_MySQLdb()


def create_app():
    app = Flask(__name__)
    app.json.ensure_ascii = False

    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET")
    app.config["JWT_ALGORITHM"] = os.getenv("JWT_ALGORITHM")
    jwt = JWTManager(app)
    
    # CORS 설정
    CORS(app, resources={r"*": {"origins": "*"}}) 

    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": 'apispec_1',
                "route": '/apispec_1.json',
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/apidocs/"
    }

    template = {
        "swagger": "2.0",
        "info": {
            "title": "Spot Extract API",
            "description": "Instagram & Place API with JWT Authorization",
            "version": "1.0.0"
        },

        "host": "13.125.197.83:8001", 
        "basePath": "/", 
        "schemes": ["http"],
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Authorization header using the Bearer scheme. Example: \"Bearer {token}\""
            }
        },
        "security": [{"Bearer": []}] 
    }

    swagger = Swagger(app, config=swagger_config, template=template)

    # 데이터베이스 설정
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

    # DB 초기화
    db.init_app(app)

    # 블루프린트 등록
    app.register_blueprint(instagram_bp)  
    app.register_blueprint(user_places_bp)
    app.register_blueprint(friend_bp)

    @app.before_request
    async def startup_browser():
        if not browser_service.browser:
            await browser_service.start()

    return app

if __name__ == '__main__':
    app = create_app()

    with app.app_context():
        try:
            db.create_all() # 나중에 flask-migra로 변경
            print("AWS RDS 연결 성공!")
        except Exception as e:
            print(f"DB 연결 실패: {e}")

    app.run(host='0.0.0.0', port=8001, debug=True)