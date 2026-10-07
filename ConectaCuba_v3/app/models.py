from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    province = db.Column(db.String(80), nullable=False, default="Santiago de Cuba")
    municipality = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    approved = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    def set_password(self, p):
        self.password_hash = generate_password_hash(p)

    def check_password(self, p):
        return check_password_hash(self.password_hash, p)

class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    product = db.Column(db.String(120), nullable=False, index=True)
    custom_product = db.Column(db.String(120))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)
    province = db.Column(db.String(80), nullable=False, default="Santiago de Cuba")
    municipality = db.Column(db.String(80), nullable=False)
    coverage = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    owner = db.relationship("User", backref="listings")

    @property
    def display_product(self):
        return self.custom_product if self.product == "Otro" and self.custom_product else self.product

    @property
    def display_kind(self):
        return "Venta" if self.kind == "oferta" else "Compra"

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    listing_id = db.Column(db.Integer)
    context_listing_a_id = db.Column(db.Integer, nullable=True)
    context_listing_b_id = db.Column(db.Integer, nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read_at = db.Column(db.DateTime, nullable=True)
    context_listing_a_id = db.Column(db.Integer, nullable=True)
    context_listing_b_id = db.Column(db.Integer, nullable=True)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    commission = db.Column(db.Float, default=5.0)
