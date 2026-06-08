from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ──────────────────────────────────────────────
# КАТЕГОРИИ
# ──────────────────────────────────────────────
class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(200))

    materials = db.relationship('Material', backref='category_rel', lazy=True)

    def __repr__(self):
        return f'<Category {self.name}>'


# ──────────────────────────────────────────────
# 1. ПОЛЬЗОВАТЕЛИ И РОЛИ
# ──────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(200))
    role = db.Column(db.String(20), nullable=False, default='viewer')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('Transaction', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_storekeeper(self):
        return self.role in ('admin', 'storekeeper')

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'


# ──────────────────────────────────────────────
# 2. МЕСТА ХРАНЕНИЯ (подвал / ячейки)
# ──────────────────────────────────────────────
class Location(db.Model):
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))

    batches = db.relationship('Batch', backref='location', lazy=True)

    def __repr__(self):
        return f'<Location {self.code}>'


# ──────────────────────────────────────────────
# 3. СПРАВОЧНИК МАТЕРИАЛОВ
# ──────────────────────────────────────────────
class Material(db.Model):
    __tablename__ = 'materials'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    size = db.Column(db.String(20))
    barcode = db.Column(db.String(100), unique=True)
    min_stock = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    note = db.Column(db.Text)

    batches = db.relationship('Batch', backref='material', lazy=True)

    @property
    def category_name(self):
        return self.category_rel.name if self.category_rel else None

    def __repr__(self):
        return f'<Material {self.name} {self.size}>'


# ──────────────────────────────────────────────
# 4. ПАРТИИ (самое важное!)
# ──────────────────────────────────────────────
class Batch(db.Model):
    __tablename__ = 'batches'

    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    batch_number = db.Column(db.String(100))
    expiry_date = db.Column(db.Date)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    used = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    transactions = db.relationship('Transaction', backref='batch', lazy=True)

    @property
    def remaining(self):
        return self.quantity - self.used

    @property
    def is_expired(self):
        if self.expiry_date:
            return date.today() > self.expiry_date
        return False

    @property
    def days_until_expiry(self):
        if self.expiry_date:
            delta = self.expiry_date - date.today()
            return delta.days
        return None

    def __repr__(self):
        return f'<Batch {self.material.name} #{self.batch_number} rem={self.remaining}>'


# ──────────────────────────────────────────────
# 5. ЖУРНАЛ ОПЕРАЦИЙ (история всех движений)
# ──────────────────────────────────────────────
class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Transaction {self.type} {self.quantity} by {self.user.username}>'