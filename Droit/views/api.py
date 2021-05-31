import copy
import json
import re
import uuid
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Blueprint, request, url_for, make_response, jsonify, session
from flask import current_app as app
from flask_login import current_user
from flask_login import current_user as user
from py_abac import Policy
from py_abac.storage.mongo import MongoStorage
from pymongo import MongoClient

from .broadcast import delete_local_thing_description, push_up_things, parent_aggregation, get_children_result
from .data_helper import deduplicate_by_id, get_compressed_list, get_final_aggregation
from .frequency import add_frequency
from ..auth.models import auth_db, Policy
from ..models import ThingDescription, DirectoryNameToURL, TypeToChildrenNames, ThingFrequency
from ..utils import get_target_url, is_json_request, clean_thing_description, add_policy_to_storage, \
    delete_policy_from_storage, is_policy_request, is_request_allowed, get_auth_attributes, set_auth_user_attr, \
    generate_jwt

ERROR_JSON = {"error": "Invalid request."}
ERROR_POLICY = {"error": "Invalid policy."}
ERROR_NO_USER = {"error": "Please login."}
OPERATION_COUNT = ""

api = Blueprint('api', __name__)


@api.route('/register', methods=['POST'])
def register():
    """Register thing description at the target location. 

    If the current directory is the target location specified by `location` argument, the operation is processed locally
    Otherwise it will delegate the operation to the next possible directory (if there is ), and return whatever the result it receives
    
    In addition, an extra 'push-up' operation may be called if the publicity is larger than zero. It will send a new register request
    using the same thing description information to its parent directory with publicity decreased by one.
    
    Args:
        All of the following arguments are required and passed in the request URL.
        td (JSON str): the information of the thing description to be registered in JSON format
        location (str): the location where the thing description should be registered
        publicity (number): specify the number of levels that the thing description should be duplicate to upper level directory.
            By default this is zero, means it does not need to be pushed up.

    Returns:
        HTTP Response: if the register is completed, a simple success string with HTTP status code 200 is returned
            Otherwise a reason is returned in the response and HTTP status code is set to 400
    """

    # 1-2. check and parse input
    if not is_json_request(request, ["td", "location", "publicity"]):
        return jsonify(ERROR_JSON), 400
    body = request.get_json()
    location = body['location']
    thing_description = body['td']
    publicity = int(body['publicity']) if 'publicity' in body else 0
    headers = {
        'Content-Type': 'application/json',
        'Accept-Charset': 'UTF-8'
    }
    # 3. check if the location is current directory
    local_server_name = app.config['HOST_NAME'] if 'HOST_NAME' in app.config else "Unknown"
    if local_server_name == location:
        thing_description = clean_thing_description(thing_description)

        # 3a. register locally
        # when this API is called by 'relocate', publicity is in the thing_description object
        # remove it to avoid duplicate key error when creating new object
        registration_result = True
        thing_description.pop("publicity", None)
        try:
            new_td = ThingDescription(publicity=publicity, **thing_description)
            new_td.save()
            new_freq = ThingFrequency(thing_id=new_td.thing_id, timestamps={})
            new_freq.save()
        except Exception as e:
            print(e)
            registration_result = False

        # 3b. push up thing description and update parent directory's aggregation data
        push_up_result = push_up_things(thing_description, publicity)
        aggregation_result = parent_aggregation("add",
                                                thing_description["thing_type"], local_server_name)

        # 3c. return result
        if push_up_result and registration_result and aggregation_result:
            return make_response("Created", 200)
        else:
            return make_response("Register failed - Internal database error", 400)

    # otherwise, the request should be redirected to other directory
    register_api = url_for("api.register")

    target_url = get_target_url(location, register_api)

    # check if any of above condition is satisfied
    if target_url is not None:
        master_response = requests.post(
            target_url, data=json.dumps(body), headers=headers)
        return make_response(master_response.reason, master_response.status_code)

    # Otherwise the input location is invalid, return
    return jsonify(ERROR_JSON), 400


@api.route('/policy', methods=['POST'])
def policy():
    """Register a new policy using the py_abac format. 
    
    Args:
        All of the following arguments are required and passed in the request URL.
        td (JSON str): the information of the policy to be registered in JSON format
        location (str): the location where the thing description should be registered

    Returns:
        HTTP Response: if the register is completed, a simple success string with HTTP status code 200 is returned
            Otherwise a reason is returned in the response and HTTP status code is set to 400
    """

    # 1-2. check and parse input
    if not is_json_request(request, ["td", "location"]):
        return jsonify(ERROR_JSON), 400

    json = request.get_json()
    policy_json = json['td']
    location = json['location']
    # Does not allow customized uid, it should be auto generated by uuid
    if 'uid' in policy_json:
        return jsonify({'error': 'Cannot customize uid'})
    uid = str(uuid.uuid4())
    if not is_policy_request(policy_json, ["description", "effect", "rules", "targets", "priority"]):
        return jsonify(ERROR_POLICY), 400

    if not user.get_id():
        return jsonify(ERROR_NO_USER), 400
    policy_json['uid'] = uid
    if add_policy_to_storage(policy_json, location):
        new_policy = Policy(uid=uid,
                            location=location,
                            policy_json=str(policy_json),
                            user_id=int(user.get_user_id()))  # local policy register
        auth_db.session.add(new_policy)
        auth_db.session.commit()
        return make_response("Created Policy", 200)

    return jsonify(ERROR_JSON), 400


@api.route('/policy_attribute_auth', methods=['POST'])
def policy_attr_auth():
    if not is_json_request(request, ["thing_id", "thing_type", "action"]):
        return jsonify(ERROR_JSON), 400

    request_json = request.get_json()
    thing_id = request_json['thing_id']
    policy_location = request_json['location']

    client = MongoClient()
    storage = MongoStorage(client, db_name=policy_location)
    add_user_scope_str = ""
    add_server_scope_str = ""
    for p in storage.get_for_target("", str(thing_id), ""):
        subject_rules = get_attr_list(p.rules.subject)
        context_rules = get_attr_list(p.rules.context)
        print("[API] (policy_attr_auth)")
        auth_attributes = get_auth_attributes()
        auth_user_attributes = auth_attributes[0]
        auth_server_attributes = auth_attributes[1]
        # initialize user attributes from profile info, if already exists
        if not auth_user_attributes["address"]:
            set_auth_user_attr("address", user.get_address())
        if not auth_user_attributes["phone_number"]:
            set_auth_user_attr("phone_number", user.get_phone())
        add_user_scope_str = get_auth_scopes([], subject_rules, auth_user_attributes)
        add_server_scope_str = get_auth_scopes([], context_rules, auth_server_attributes)
    print("add_user_scope_str: ", add_user_scope_str)
    print("add_server_scope_str: ", add_server_scope_str)

    # authorize and access the required attributes
    if len(add_user_scope_str) > 0 or len(add_server_scope_str) > 0:
        # initialize 'info_authorize' to zero to indicate authorization not yet started
        session['info_authorize'] = 0
        # pass scopes by session
        session['add_user_scope'] = add_user_scope_str
        session['add_server_scope'] = add_server_scope_str
        return url_for("auth.info_authorize"), 300

    return make_response("Request Succeed", 200)


def get_attr_list(policy_rules):
    if isinstance(policy_rules, list):
        rule_dict = {}
        for rule in policy_rules:
            rule_dict.update(rule)
        res = rule_dict.keys()
    else:
        res = policy_rules.keys()
    res = list(res)
    for i, key in enumerate(res):
        if key[:5] == '$.geo':
            res[i] = 'position'
    return res


@api.route('/policy_decision', methods=['POST'])
def policy_decision():
    """Determines whether a request is allowed or not

    Args:
        The function uses HTTP request directly. These argument must be contained in the request:
        thing_id (str): identification of the thing
        thing_type (str): type of the thing
        action (str): get,delete, or create

    Returns:
        success: HTTP response with a short string indicating of successfulness and status code 
        failure: user id and status code

    """
    if not is_json_request(request, ["thing_id", "thing_type", "action"]):
        return jsonify(ERROR_JSON), 400
    if is_request_allowed(request):
        if not current_user.is_anonymous:
            add_frequency(request.get_json()["thing_id"], str(current_user.get_user_id()))
        td = ThingDescription.objects(thing_id=request.get_json()["thing_id"])
        return jsonify(td), 200
    else:
        return jsonify({"id": user.get_id()}), 400


def get_auth_scopes(auth_scope, attr_list, auth_attributes):
    print("(get_auth_scopes)", auth_scope, attr_list, auth_attributes)
    for s in attr_list:
        attr_name = re.search("[a-zA-Z_]+", s).group().lower()
        if (attr_name not in auth_scope) and (attr_name in auth_attributes):
            # no auth data yet
            if not auth_attributes[attr_name] or attr_name == 'position':
                auth_scope.append(attr_name)
    return " ".join(auth_scope)


@api.route('/delete_policy', methods=['POST'])
def delete_policy():
    """Delete a policy from storage and the database

    Args:
        The function uses HTTP request directly. These argument must be contained in the request:
        uid (str): uid of the policy
        location (str): location the policy is stored

    Returns:
        HTTP response with a short string indicating of successfulness and status code 

    """
    if delete_policy_from_storage(request):
        request_json = request.get_json()
        uid = request_json['uid']
        Policy.query.filter(Policy.uid == uid).delete()
        auth_db.session.commit()
        return make_response("Policy Deleted", 200)
    else:
        return make_response("Error Occurred", 400)


@api.route('/update_aggregate', methods=['POST', 'DELETE'])
def update_type_aggregation():
    """Update local aggregation data when a thing is registered/deleted at any children directory

    If the current directory is the target location specified by `location` argument, the operation is processed locally
    Otherwise it will delegate the operation to the next possible directory (if there is ), and return whatever the result it receives

    Args:
        request.thing_type (str): the type of the thing description may need to be updated.
        request.location (str): specify where the update operation should be done.

    Returns:
        HTTP Response: a brief string explaining the result and corresponding HTTP status code.
            When the update finished, HTTP status code 200 will be return, otherwise 400.
    """
    if request.method == 'POST':
        if not is_json_request(request, ["location", "thing_type"]):
            return jsonify(ERROR_JSON), 400
        body = request.get_json()
        thing_type = body['thing_type']
        location = body['location']

        # 3. update database
        children_locations = TypeToChildrenNames.objects(
            thing_type=thing_type).first()
        # don't need to do any update
        if children_locations is not None and location in children_locations.children_names:
            return "No need to update", 200

        if children_locations is None:
            children_locations = TypeToChildrenNames(
                thing_type=thing_type, children_names=[location])
        elif location not in children_locations.children_names:
            children_locations.children_names.append(location)

        children_locations.save()
        # 4. recursively update the aggregation data at parent's directory
        parent_aggregation('add', thing_type, location)

    elif request.method == 'DELETE':
        location = request.args.get('location')
        thing_type = request.args.get('thing_type')
        if location is None or thing_type is None:
            return "Bad Request(arguments missing).", 400
        # delete location from the thing_type's aggregation list
        children_locations = TypeToChildrenNames.objects(
            thing_type=thing_type).first()
        if children_locations is not None:
            children_locations.children_names.remove(location)
            children_locations.save()
            # recursively delete parent's aggregation data for the same record
            parent_aggregation('delete', thing_type, location)

    return make_response("Update aggregation data successfully.", 200)


@api.route('/adjacent_directory')
def adjacent_directory():
    """Returned the neighbor(one-level apart) and master directory names and URIs of the current directory.

    Returns:
        HTTP Response: a list of directory information in JSON format with HTTP status 200
    """
    return jsonify(DirectoryNameToURL.objects().to_json()), 200


@api.route('/search', methods=['GET'])
def search():
    """Search the thing descriptions according to the conditions from the target directory and return all satisfying thing descriptions
    
    If the current directory is the target location specified by `location` argument, the operation is processed locally
    Otherwise it will delegate the operation to the next possible directory (if there is ), and return whatever the result it receives

    Args:
        location (str): specify the directory where the search operation should be performed. If this is missing, then the current location 
            and all of its descendant locations containing the thing descriptions will be searched.
        type (str): the type of the thing description. Only thing descriptions of this type will be returned. If this is missing, then there
            is no constraint on the type.
        id (str) : the unique thing id of the thing description. Only the thing description having this id will be returned. If this is missing,
            then there is no constraint on the id.

    Returns:
        HTTP Response: If the search operation is complete without error, a list of thing descriptions in JSON format is returned with HTTP code
            setting to 200. Otherwise a string description will be in the response body along with HTTP status code 400 is returned.
    """
    location = request.args.get('location')
    local_server_name = app.config['HOST_NAME'] if 'HOST_NAME' in app.config else "Unknown"
    request_query_string = urlencode(request.args)
    if not location or not location.strip():
        location = local_server_name
    else:
        location = location.strip()

    # 1. starting from current directory
    if location == local_server_name:
        thing_type = request.args.get('thing_type')
        thing_id = request.args.get('thing_id')
        # clean empty input string
        thing_type = None if not thing_type or not thing_type.strip() else thing_type.strip()
        thing_id = None if not thing_id or not thing_id.strip() else thing_id.strip()

        thing_list = []
        # 1. add result in current directory
        things_obj = ThingDescription.objects(thing_type=thing_type) if thing_type else ThingDescription.objects.all()

        local_things = json.loads(things_obj.to_json())

        if local_things is not None:
            thing_list.extend(local_things)

        # 2. get results from children's directory
        children_things = get_children_result(
            thing_type, url_for("api.search"), request_query_string)
        thing_list.extend(children_things)
        # 3. deduplicate by thing_id
        thing_id_set = set()
        result_list = []
        for thing in thing_list:
            if thing["thing_id"] not in thing_id_set and (thing_id is None or thing["thing_id"] == thing_id):
                thing_id_set.add(thing["thing_id"])
                if 'url' in thing:
                    response = requests.get(thing['url'])
                    for attr in response.json():
                        if attr not in thing:
                            thing[attr] = response.json()[attr]
                result_list.append(thing)
        return jsonify(result_list), 200

    # 2. redirect to the target location
    target_url = get_target_url(location, url_for('api.search'))
    if target_url is None:
        return "Search failed", 400
    iterative = request.args.get('iterative')
    # if the request is 'iterative', it simply returns the redirect URL path
    # otherwise, it will send request on behalf of the caller to the URL
    if iterative:
        return target_url, 302
    else:
        request_url = f"{target_url}?{request_query_string}"
        try:
            response = requests.get(request_url)
        except:
            return "Search failed", 400

        if response.status_code == 200:
            return jsonify(response.json()), 200

    return "Search failed", 400


@api.route('/jwt', methods=['GET'])
def get_jwt():
    """Generate jwt of the requested thing with minimal inforamtion in the payload`

    Args:
        This method receive arguments from HTTP request body, which must be JSON format containing following properties
        thing_id (str): uniquely identify the thing description to be deleted
        Also, user must be logged in for username attribute.
    Returns:
        HTTP Response: The response is a jwt in JSON format with corresponding HTTP status code indicating the result
        if the deletion is performed succesfully, or there is no such thing description in the target directory,
        the deletion is complete with HTTP status code 200 being returned. Otherwise HTTP status code 400 is returned.
    """

    thing_id = request.args.get('thing_id')
    if not user.get_id():
        return "Please login", 400

    username = user.get_username()
    timestamp = datetime.now().timestamp()
    payload = {"thing_id": thing_id, "username": username, "timestamp": timestamp}
    if not thing_id:
        return "Invalid input", 400

    # generate jwt
    encoded_jwt, priv_key, pub_key = generate_jwt(payload)
    session['pub_key'] = pub_key

    jwt_json = payload.copy()
    jwt_json['encoding'] = str(encoded_jwt)
    return jsonify(jwt_json), 200


@api.route('/jwt_send', methods=['POST'])
def send_jwt():
    if not is_json_request(request, ['jwt_token', 'url']):
        return make_response("Invalid request", 400)

    body = request.get_json()
    target_url = body['url']
    data = {
        'jwt_token': body['jwt_token'][2:-1],
        'pub_key': session.get('pub_key').decode()
    }

    # send and get response
    try:
        response = requests.post(target_url, data=json.dumps(data))
    except Exception as e:
        return make_response("Request Failed", 400)

    # if successful
    if response.status_code == 200:
        return make_response("Successfully sent", 200)
    else:
        return make_response(response.content, 400)


@api.route('/delete', methods=['DELETE'])
def delete():
    """Delete the thing description specified by `thing_id` argument and from directory specified by the argument `location`

    If the current directory is the target location specified by `location` argument, the operation is processed locally
    Otherwise it will delegate the operation to the next possible directory (if there is ), and return whatever the result it receives
    
    Args:
        This method receive arguments from HTTP request body, which must be JSON format containing following properties
        thing_id (str): uniquely identify the thing description to be deleted
        location (str): specify the location where the thing description located is
    Returns:
        HTTP Response: The response is a pure string HTTP response with corresponding HTTP status code indicating the result
        if the deletion is performed succesfully, or there is no such thing description in the target directory,
        the deletion is complete with HTTP status code 200 being returned. Otherwise HTTP status code 400 is returned.
    """

    location = request.args.get('location')
    thing_id = request.args.get('thing_id')
    if not location or not thing_id or not location.strip() or not location.strip():
        return '', 200

    location = location.strip()
    thing_id = thing_id.strip()
    local_server_name = app.config['HOST_NAME'] if 'HOST_NAME' in app.config else "Unknown"
    if location == local_server_name:
        if delete_local_thing_description(thing_id) == 404:
            return "Invalid thing id", 404

        return "Deleted", 200

    # if not aiming at current directory, send a request to the correct target location
    target_url = get_target_url(location, url_for("api.delete"))
    if target_url is not None:
        request_url = f"{target_url}?{urlencode(request.args)}"
        try:
            response = requests.delete(request_url)
        except:
            return "", 400
        if response.status_code == 200:
            return "", 200

    return "", 400


@api.route('/relocate', methods=['POST'])
def relocate():
    """Relocate a thing specified by the `thing_id` from the location specified by `from` to the location specified by `to`

    If the current directory is the target location specified by `location` argument, the operation is processed locally
    Otherwise it will delegate the operation to the next possible directory (if there is ), and return whatever the result it receives

    This method performs search operation using the `thing_id` from the `from` directory
    Then the thing description is removed locally, followed by an insertion operation using the same thing description content in `to` directory
    
    Caution: Currently this two steps are not performed as one transaction, which means even if the second operation (insertion) failed, 
    the first operation (deletion) is already finished an irrevocable.

    Args:
        This method receive arguments from HTTP request body, which must be JSON format containing following properties
        thing_id (str): uniquely identify the thing description to be relocated
        from (str): specify the location where the thing description located is
        to (str): specify the location that the thing description should be relocated to

    Returns:
        HTTP Response: The response is a pure string HTTP response with corresponding HTTP status code indicating the result
        if the relocation operation is completed, 200 is returned. Otherwise 400 is returned.
    """
    if not is_json_request(request, ["thing_id", "from", "to"]):
        return jsonify(ERROR_JSON), 400
    body = request.get_json()
    thing_id = body['thing_id']
    from_location = body['from']
    to_location = body['to']

    headers = {
        'Content-Type': 'application/json',
        'Accept-Charset': 'UTF-8'
    }

    local_server_name = app.config['HOST_NAME'] if 'HOST_NAME' in app.config else "Unknown"
    if local_server_name == from_location:
        relocate_thing = ThingDescription.objects(thing_id=thing_id).first()
        target_url = get_target_url(to_location, url_for("api.register"))
        if relocate_thing is None or target_url is None:
            return jsonify(ERROR_JSON), 400
        # 1. insert this thing description at 'to_location'
        request_data = {
            "td": json.loads(relocate_thing.to_json()),
            "location": to_location,
            "publicity": relocate_thing.publicity
        }
        try:
            response = requests.post(
                target_url, data=json.dumps(request_data), headers=headers)
            pass
        except:
            return "Relocate failed", 400
        # 2. delete this thing description at 'from_location'
        delete_local_thing_description(thing_id)

        return "", 200

    # delegate the request to other directory
    request_url = get_target_url(from_location, url_for("api.relocate"))
    if request_url is None:
        return "Request failed", 400
    try:
        response = requests.post(
            request_url, data=json.dumps(body), headers=headers)
    except:
        return "Request failed", 400

    return "", response.status_code


@api.route('/custom_query', methods=['GET'])
def custom_query():
    """Return all thing descriptions from the target directory and its descandant directories that satisfy the filter conditions

    Args:
        operation (str):
        type (str):
        data (str):
        location (str): optional, specify the root directory to be searched. 
        filter (JSON str): 
    Returns:
        HTTP Response:
    """
    script = request.args.get('data')
    try:
        script_json = json.loads(script)
    except:
        return jsonify({"error": "Invalid input format"}), 400

    SCRIPT_OPERATION = ["SUM", "AVG", "MIN", "MAX", "COUNT"]  # Allowed operation of the customized script query

    # check input combination: type and operation are required
    if "operation" not in script_json or "type" not in script_json or type(script_json["operation"]) != str:
        return jsonify(ERROR_JSON), 400

    script_json["operation"] = script_json["operation"].upper()

    if script_json["operation"] not in SCRIPT_OPERATION or (
            script_json["operation"] != "COUNT" and "data" not in script_json):
        return jsonify(ERROR_JSON), 400

    # 2. Clean parameters
    local_server_name = app.config['HOST_NAME'] if 'HOST_NAME' in app.config else "Unknown"
    location = local_server_name if "location" not in script_json else script_json["location"].strip()
    # the filters itself is a dictionary, in order to manipulate(delete/udpate) items in the filter
    # here must be a deepcopy rather than a merely reference to the filed in script_json
    filters = copy.deepcopy(script_json["filter"]) if "filter" in script_json else {}
    data_field = script_json["data"] if "data" in script_json else None
    time_range = {"start": script_json.get("start"), "end": script_json.get("end")}

    # 3. filter result.
    if location == local_server_name:
        operation = script_json["operation"].strip()
        thing_type = script_json["type"].strip()

        filter_map = {}
        # add geographical filter condition
        if "polygon" in filters and type(filters["polygon"]) == list and len(filters["polygon"]) >= 3:
            # properties__geo__coordinates represents field properties.geo.coordinates
            # geo_within_polygon: query string for geospatial query
            filter_map["properties__geo__coordinates__geo_within_polygon"] = filters.pop("polygon")
            # An example of mongodb query is: db.td.find({ "properties.geo.coordinates": { $geoWithin: {$polygon: [[-75,40],[-75,41],[-70,41],[-70,40]]}}})

        for filter_name in filters:
            filter_map[filter_name.replace(".", "__")] = filters[filter_name]

        try:
            thing_list = json.loads(ThingDescription.objects(thing_type=thing_type, **filter_map).to_json())
        except:
            return jsonify({"reason": "filter condition error."}), 400

        # 3. get children result.
        # "_sub_dir" field checks whether current directory is a recursive node
        # if this field is true, which means the request must return a compressed thing list results
        # otherwise, return the final aggregation result
        is_sub_dir = "_sub_dir" in script_json
        script_json["_sub_dir"] = True  # Give hint to children directory
        # delete the "location" field in the query string, then each children will treat themselves as the target dir
        if "location" in script_json:
            del script_json["location"]
        children_result_list = get_children_result(thing_type, url_for(
            "api.custom_query"), f"data={json.dumps(script_json)}")
        thing_list.extend(children_result_list)
        thing_list = deduplicate_by_id(thing_list)
        #
        # [{id, properties, ..., ..}, {id, propertis..}, {}, {}]
        # COUNT: [{id1}, {id2}, {id3}, ...]
        # MIN,MAX,SUM,AVG: [{id, data: a}, {id, data: b}]
        compressed_thing_list = get_compressed_list(thing_list, operation, data_field, time_range)

        # 4. return data
        # return the aggregation result if current directory is the root
        # otherwise return the compressed list
        if not is_sub_dir:
            return jsonify(get_final_aggregation(compressed_thing_list, operation, time_range)), 200
        else:
            return jsonify(compressed_thing_list), 200

    # when location is not here, delegate to other directories
    request_url = get_target_url(
        script_json["location"], url_for("api.custom_query"))
    if request_url is None:
        return jsonify("Request failed(location does not exist.)"), 400
    try:
        response = requests.get(f"{request_url}?data={script}")
    except:
        return jsonify("Request failed(target location is not running.)"), 400

    if response.status_code == 200:
        return jsonify(response.json()), 200

    return jsonify("Request failed(from other location)"), 400
