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
# ПОЛЬЗОВАТЕЛИ И РОЛИ
# ──────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(200))
    role = db.Column(db.String(30), nullable=False, default='viewer')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('Transaction', backref='user', lazy=True)
    created_requests = db.relationship('TransferRequest', foreign_keys='TransferRequest.from_user_id', backref='from_user', lazy=True)
    processed_requests = db.relationship('TransferRequest', foreign_keys='TransferRequest.to_user_id', backref='to_user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_head(self):
        return self.role in ('admin', 'head')

    def is_head_nurse(self):
        return self.role in ('admin', 'head_nurse')

    def is_doctor_storekeeper(self):
        return self.role in ('admin', 'doctor_storekeeper')

    def is_storekeeper(self):
        return self.role in ('admin', 'head_nurse', 'doctor_storekeeper')

    def is_doctor(self):
        return self.role in ('admin', 'head', 'doctor_storekeeper', 'doctor')

    def can_confirm(self):
        return self.role in ('admin', 'head', 'doctor_storekeeper', 'doctor')

    def can_create_draft(self):
        return self.role in ('admin', 'head', 'head_nurse', 'doctor_storekeeper', 'doctor', 'xray_lab')

    def can_create_request(self):
        return self.role in ('admin', 'head_nurse', 'doctor_storekeeper', 'xray_lab')

    def can_process_requests(self):
        return self.role in ('admin', 'head_nurse', 'doctor_storekeeper')

    def can_revision(self):
        return self.role in ('admin', 'head_nurse')

    def can_reports(self):
        return self.role in ('admin', 'head', 'head_nurse', 'doctor_storekeeper')

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'


# ──────────────────────────────────────────────
# МЕСТА ХРАНЕНИЯ (локации)
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
# МАТЕРИАЛЫ
# ──────────────────────────────────────────────
class Material(db.Model):
    __tablename__ = 'materials'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    size = db.Column(db.String(20))
    barcode = db.Column(db.String(100), unique=True)
    min_stock = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    is_reusable = db.Column(db.Boolean, default=False)
    note = db.Column(db.Text)

    batches = db.relationship('Batch', backref='material', lazy=True)

    @property
    def category_name(self):
        return self.category_rel.name if self.category_rel else None

    def __repr__(self):
        return f'<Material {self.name} {self.size}>'


# ──────────────────────────────────────────────
# ПАРТИИ
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
# ЖУРНАЛ ОПЕРАЦИЙ (транзакции)
# ──────────────────────────────────────────────
class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text)
    operation_id = db.Column(db.String(50))
    doctor_name = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Transaction {self.type} {self.quantity} by {self.user.username}>'


# ──────────────────────────────────────────────
# ПЕРЕМЕЩЕНИЯ МЕЖДУ СКЛАДАМИ
# ──────────────────────────────────────────────
class Transfer(db.Model):
    __tablename__ = 'transfers'

    id = db.Column(db.Integer, primary_key=True)
    from_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    to_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    from_location = db.relationship('Location', foreign_keys=[from_location_id])
    to_location = db.relationship('Location', foreign_keys=[to_location_id])
    user = db.relationship('User', foreign_keys=[user_id])
    items = db.relationship('TransferItem', backref='transfer', lazy=True)

    def __repr__(self):
        return f'<Transfer #{self.id} {self.from_location.code} -> {self.to_location.code}>'


class TransferItem(db.Model):
    __tablename__ = 'transfer_items'

    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey('transfers.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    batch = db.relationship('Batch')

    def __repr__(self):
        return f'<TransferItem {self.quantity} of batch #{self.batch_id}>'


# ──────────────────────────────────────────────
# ЗАЯВКИ НА ПЕРЕМЕЩЕНИЕ
# ──────────────────────────────────────────────
class TransferRequest(db.Model):
    __tablename__ = 'transfer_requests'

    id = db.Column(db.Integer, primary_key=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    from_location_code = db.Column(db.String(50))
    to_location_code = db.Column(db.String(50), default='oper')
    status = db.Column(db.String(20), default='pending')
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)

    material = db.relationship('Material')

    def __repr__(self):
        return f'<TransferRequest #{self.id} {self.material.name} x{self.quantity}>'


# ──────────────────────────────────────────────
# ПОДТВЕРЖДЕНИЕ СПИСАНИЯ
# ──────────────────────────────────────────────
class SpendingDraft(db.Model):
    __tablename__ = 'spending_drafts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('materials.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    operation_id = db.Column(db.String(50))
    doctor_name = db.Column(db.String(200))
    status = db.Column(db.String(20), default='pending')
    confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])
    doctor = db.relationship('User', foreign_keys=[confirmed_by])
    material = db.relationship('Material')

    def __repr__(self):
        return f'<SpendingDraft #{self.id} {self.material.name} x{self.quantity} [{self.status}]>'


# ──────────────────────────────────────────────
# РЕВИЗИЯ
# ──────────────────────────────────────────────
class Revision(db.Model):
    __tablename__ = 'revisions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('batches.id'), nullable=False)
    old_quantity = db.Column(db.Integer)
    new_quantity = db.Column(db.Integer)
    old_used = db.Column(db.Integer)
    new_used = db.Column(db.Integer)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')
    batch = db.relationship('Batch')

    def __repr__(self):
        return f'<Revision batch #{self.batch_id} qty {self.old_quantity}->{self.new_quantity}>'