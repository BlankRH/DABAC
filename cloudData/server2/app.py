from flask import Flask, request, make_response, jsonify, session
import jwt
import json
import sys

app = Flask(__name__)

app.config['SECRET_KEY'] = '123456'


@app.route('/', methods=['GET'])
def index():
    sample_data = {
        "urn:dev:wot:com:example:servient:11": {
            "properties": {
                "property1": [
                    {
                        "data": 5,
                        "start": 1,
                        "end": 2
                    }
                ],
                "property2": {
                    {
                        "data": 8,
                        "start": 1,
                        "end": 2
                    }
                }
            }
        }
    }
    thing_id = request.args.get('thing_id')
    if thing_id not in sample_data:
        return make_response('Invalid thing id', 400)
    else:
        data_field_list = request.args.get('data_field_list')
        res = sample_data[thing_id]
        for data_field in data_field_list:
            res = res[data_field]
        return jsonify(res), 200


if __name__ == '__main__':
    app.run(debug=True, port=6001)
