import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a7f3b9c2d1e8f4a6b0c3d5e7f9a1b2c3'
    
    # Путь к базе данных на диске Render
    db_path = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'stock.db'))
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{db_path}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False