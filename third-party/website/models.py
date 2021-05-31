import time
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.sqla_oauth2 import (
    OAuth2ClientMixin,
    OAuth2AuthorizationCodeMixin,
    OAuth2TokenMixin,
)

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True)
    position_X = db.Column(db.Float)
    position_Y = db.Column(db.Float)

    def __str__(self):
        return self.username

    def get_user_id(self):
        return self.id

    def get_user_position_x(self):
        return self.position_X

    def get_user_position_y(self):
        return self.position_Y

    def check_password(self, password):
        return password == 'valid'


class OAuth2Client(db.Model, OAuth2ClientMixin):
    __tablename__ = 'oauth2_client'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))
    user = db.relationship('User')


class OAuth2AuthorizationCode(db.Model, OAuth2AuthorizationCodeMixin):
    __tablename__ = 'oauth2_code'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))
    user = db.relationship('User')


class OAuth2Token(db.Model, OAuth2TokenMixin):
    __tablename__ = 'oauth2_token'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))
    uid_user = db.relationship('User', foreign_keys=[user_id])
    temperature = db.Column(
        db.Integer, db.ForeignKey('weather.temperature', ondelete='CASCADE'))
    temp_weather = db.relationship('Weather', foreign_keys=[temperature])
    rainfall = db.Column(
        db.Integer, db.ForeignKey('weather.rainfall', ondelete='CASCADE'))
    rain_weather = db.relationship('Weather', foreign_keys=[rainfall])
    position_X = db.Column(
        db.Float, db.ForeignKey('user.position_X', ondelete='CASCADE'))
    position_x_user = db.relationship('User', foreign_keys=[position_X])
    position_Y = db.Column(
        db.Float, db.ForeignKey('user.position_Y', ondelete='CASCADE'))
    position_y_user = db.relationship('User', foreign_keys=[position_Y])


    def is_refresh_token_active(self):
        if self.revoked:
            return False
        expires_at = self.issued_at + self.expires_in * 2
        return expires_at >= time.time()


class Weather(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True)
    temperature = db.Column(db.Integer)
    rainfall = db.Column(db.Integer)

    def get_temperature(self):
        return self.temperature

    def get_rainfall(self):
        return self.rainfall
