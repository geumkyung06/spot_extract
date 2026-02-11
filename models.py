from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import BigInteger, Integer
from sqlalchemy.ext.compiler import compiles

db = SQLAlchemy()

# 👇 [추가 2] 이 함수를 추가하세요 (모델 클래스 정의하기 전에)
@compiles(BigInteger, 'sqlite')
def compile_big_int_sqlite(type_, compiler, **kw):
    """
    SQLite 환경에서는 BigInteger를 INTEGER로 변환하여
    AUTOINCREMENT가 정상 작동하도록 함
    """
    return 'INTEGER'

# insta_url table
class InstaUrl(db.Model):
    __tablename__ = 'insta_url'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    url = db.Column(db.String(255))      # 인스타 링크
    image = db.Column(db.String(255))    # 썸네일
    texts = db.Column(db.Text)           # 캡션 전체 저장

# place table
class Place(db.Model):
    __tablename__ = 'place'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    name = db.Column(db.String(255)) 
    address = db.Column(db.String(255))
    
    gid = db.Column(db.String(255), unique=True, nullable=True)

    latitude = db.Column(db.Float)  
    longitude = db.Column(db.Float)
    
    list = db.Column(db.Enum('accessory','bar','cafe','cloth','etc','restaurant')) 
    photo = db.Column(db.String(1000))
    
    rating_avg = db.Column(db.Float, default=0.0)
    rating_count = db.Column(db.Integer, default=0, nullable=False)
    saved_count = db.Column(db.Integer, default=0, nullable=False)
    score = db.Column(db.Float, default=0.0, nullable=False)
    search_count = db.Column(db.Integer, default=0, nullable=False)
    place_area_id = db.Column(db.BigInteger, nullable=True)

# list : exhibition, activitiy, prop_shop, clothing_store > cloth , dessert, cafe, bar, restaurant, etc

# 3. url_place table
class UrlPlace(db.Model):
    __tablename__ = 'url_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # ERD 컬럼명 준수
    instaurl_id = db.Column(db.BigInteger, db.ForeignKey('insta_url.id'))
    placeid_id = db.Column(db.BigInteger, db.ForeignKey('place.id'))

# 4. saved_place table
class SavedPlace(db.Model):
    __tablename__ = 'saved_place'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user_id = db.Column(db.BigInteger, nullable=False)  # 유저 ID
    place_id = db.Column(db.BigInteger, db.ForeignKey('place.id'), nullable=False) # 장소 ID
    
    rating = db.Column(db.Integer, default=0)           # 유저 별점
    save_type = db.Column(db.String(255), default="spot") # 저장 유형 (instagram, spot)
    
    # 관계 설정 (저장된 장소 정보를 가져오기 위해)
    place = db.relationship('Place', backref='saved_by_users')

# 5. kakao_mem table
class KakaoMem(db.Model):
    __tablename__ = 'kakao_mem'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    email = db.Column(db.String(255), unique=True, nullable=False)
    
    one_line = db.Column(db.String(255)) 
    
    kakao_id = db.Column(db.String(255), unique=True, nullable=False)
    nickname = db.Column(db.String(255), nullable=False) 
    
    password = db.Column(db.String(255)) 
    photo = db.Column(db.String(255)) 
    spot_nickname = db.Column(db.String(255))
    spot_id = db.Column(db.String(255))


# 6. friend table
class Friend(db.Model):
    __tablename__ = 'friend'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    status = db.Enum('block','friend','give','waiting') # block 이면 친구 삭제되게 해야함
    
    friend_id = db.Column(db.BigInteger, db.ForeignKey('kakao_mem.id')) 
    
    member_id = db.Column(db.BigInteger, db.ForeignKey('kakao_mem.id'))  
    
# 7. place_like -> 하트
class PlaceLike(db.Model):
    __tablename__ = 'place_like'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    placeid_id = db.Column(db.BigInteger, nullable=False)
    userid_id = db.Column(db.BigInteger, nullable=False)

# 8. comment
class Comment(db.Model):
    __tablename__ = 'comment'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    content = db.Column(db.String(255))
    user_id = db.Column(db.String(255)) 
    
    kakao_mem_id = db.Column(db.BigInteger) 
    
    place_id = db.Column(db.BigInteger, db.ForeignKey('place.id'))

class SavedSeq(db.Model):
    __tablename__ = 'saved_place_seq'
    
    id = db.Column(db.Integer, primary_key=True) 
    next_val = db.Column(db.BigInteger, default=0)
