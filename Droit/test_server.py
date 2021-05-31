import jwt
from flask import Flask
from flask import request

app = Flask(__name__)

THING_ID = 'urn:dev:wot:com:example:servient:35'


@app.route('/', methods=['post'])
def index():
    if request.headers.get('jwt'):
        jwt_token = request.headers.get('jwt')
        jwt_id = jwt.decode(jwt_token)
        if THING_ID == jwt_id:
            return request.body, 200
    return 'Invalid jwt token', 400
