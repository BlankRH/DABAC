from flask import Flask, request, make_response, jsonify, session
import jwt
import json
import sys

app = Flask(__name__)

app.config['SECRET_KEY'] = '123456'


def get_time_range_data(all_data, time_range_start, time_range_end):
    res = []
    for data in all_data:
        start = data['start']
        end = data['end']
        if (time_range_start is None or time_range_start <= start) and \
                (time_range_end is None or time_range_end >= end):
            res.append(data)
        elif (time_range_start and time_range_start > end) or (time_range_end and time_range_end < start):
            continue
        else:
            data['start'] = max(start, time_range_start)
            data['end'] = min(end, time_range_end)
            res.append(data)
    return res


@app.route('/', methods=['GET'])
def index():
    sample_data = {
        "urn:dev:wot:com:example:servient:11": {
            "properties": {
                "property1": [
                    {
                        "data": 2,
                        "start": 2,
                        "end": 5
                    }
                ],
                "property2": [
                    {
                        "data": 10,
                        "start": 2,
                        "end": 3
                    }
                ]
            }
        },
        "urn:dev:wot:com:example:servient:10": {
            "properties": {
                "property1": [
                    {
                        "data": 5,
                        "start": 2,
                        "end": 3
                    }
                ],
                "property2": [
                    {
                        "data": 8,
                        "start": 2,
                        "end": 3
                    }
                ]
            }
        }
    }
    thing_id = request.args.get('thing_id')
    if thing_id not in sample_data:
        return make_response('Invalid thing id', 400)
    else:
        data_field_list = request.args.get('data_field_list').split('.')
        res = sample_data[thing_id]
        for data_field in data_field_list:
            res = res[data_field]
        res = get_time_range_data(res, int(request.args.get('start')), int(request.args.get('end')))
        return jsonify(res), 200


if __name__ == '__main__':
    app.run(debug=True, port=6002)
