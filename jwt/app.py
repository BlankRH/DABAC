from flask import Flask, request, make_response, jsonify, session
import jwt
import json

app = Flask(__name__)

app.config['SECRET_KEY'] = '123456'


@app.route('/', methods=['POST'])
def index():
    body = json.loads(request.data)
    if 'jwt_token' not in body or 'pub_key' not in body:
        return make_response("Jwt token and public key required", 400)
    raw_token = body['jwt_token']
    public_key = body['pub_key']

    try:
        decoded_token = jwt.decode(raw_token, algorithms=['RS256'], key=public_key)
    except jwt.PyJWTError:
        return make_response("Invalid jwt token", 400)
    session['data'] = decoded_token
    return make_response(jsonify(decoded_token), 200)

app.run()