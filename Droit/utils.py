"""
Functions declared in this file are helper functions that can be shared by all other modules
"""
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import flask
import jwcrypto.jwk as jwk
import jwt
from flask_login import current_user
from py_abac import PDP, Policy, AccessRequest
from py_abac.pdp import EvaluationAlgorithm
from py_abac.provider.base import AttributeProvider
from py_abac.storage.mongo import MongoStorage
from pymongo import MongoClient
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon

from .auth import User, AuthAttribute
from .auth.models import auth_user_attr_default, auth_server_attr_default
from .models import DirectoryNameToURL, TargetToChildName, ThingFrequency


def is_json_request(request: flask.Request, properties: list = []) -> bool:
    """Check whether the request's body could be parsed to JSON format, and all necessary properties specified by `properties` are in the JSON object

    Args:
        request (flask.Request): the flask request object wrapping the real HTTP request data
        properties (list[str]): list of property names to check. By default its an empty list

    Returns:
        boolean: whether the request is a JSON-content request and contains all the properties
    """
    try:
        body = request.get_json()
    except TypeError:
        return False
    if body is None:
        return False
    for prop in properties:
        if prop not in body:
            return False
    return True


def clean_thing_description(thing_description: dict) -> dict:
    """Change the property name "@type" to "thing_type" and "id" to "thing_id" in the thing_description

    Args:
        thing_description (dict): dict representing a thing description

    Returns:
        dict: the same dict with "@type" and "id" keys are mapped to "thing_type" and "thing_id"
    """
    if "@type" in thing_description:
        thing_description["thing_type"] = thing_description.pop("@type")
    if "id" in thing_description:
        thing_description["thing_id"] = thing_description.pop("id")
    return thing_description


def get_target_url(location: str, api: str = "") -> str:
    """Check the next possible location to request in order to get the 'location' directory

    It will check database if the 'location' is an adjacent directory to the current one.
    If it is, then return the concatenated URI using this adjacent directory's URI.

    Then it will check whether this 'location' is one of descendants directories.
    If it is, then return.

    Finally if current directory is not master, then it will return the URI using master directory's location

    Args:
        location (str): the target location to be searched
        api(str): url path after the host name, such as /register, /search. It is highly encouraged to form this parameter using 'url_for'

    Returns:
        str: if the location is possible, return the concatenated URI along with the 'api', otherwise return None

    """
    target_url = None
    # 1. check whether the location is known to current directory (parent or direct children)
    known_directory = DirectoryNameToURL.objects(
        directory_name=location).first()
    if known_directory is not None:
        target_url = urljoin(known_directory.url, api)

    # 2. check if the location is its descendants
    elif TargetToChildName.objects(target_name=location).first() is not None:
        descendant_directory = TargetToChildName.objects(
            target_name=location).first()
        target_url = urljoin(DirectoryNameToURL.objects(
            directory_name=descendant_directory.child_name).first().url, api)

    # 3. if current is not master directory, return the url of master directory
    elif DirectoryNameToURL.objects(relationship="parent").first() is not None:
        master_url = DirectoryNameToURL.objects(
            directory_name="master").first().url
        target_url = urljoin(master_url, api)

    return target_url


def add_policy_to_storage(policy: dict, location: str) -> bool:
    # json = request.get_json()
    policy = Policy.from_json(policy)
    client = MongoClient()
    storage = MongoStorage(client, db_name=location)
    try:
        storage.add(policy)
    except:
        return False
    return True


def delete_policy_from_storage(request: flask.Request) -> bool:
    """Delete a policy from storage. 

    Args:
        The function uses HTTP request directly. These argument must be contained in the request:
        request.uid (str): uid of the policy
        request.location (str): location the policy is stored

    Returns:
        True if the removal is successful. 

    """
    request_json = request.get_json()
    uid = request_json['uid']
    location = request_json['location']
    client = MongoClient()
    storage = MongoStorage(client, db_name=location)
    storage.delete(uid)
    return True


# check if the request is allowed by policy in the current level
def is_request_allowed(request: flask.Request) -> bool:
    """Check whether a request is allowed or not. 
    Attributes are retrieved by the attribute providers and in order of the providers. 

    Args:
        request (JSON): request to the thing with identifications about the thing

    Returns:
        1/0 (int): indicating a request is allowed or not

    """

    request_json = request.get_json()
    thing_id = request_json['thing_id']
    policy_location = request_json['location']
    try:
        user_id = current_user.get_user_id()
        user = User.query.filter_by(id=user_id).first()
        user_email = user.get_email()
    except:
        user_id = ""
        user_email = ""

    class TimestampAttributeProvider(AttributeProvider):
        def get_attribute_value(self, ace, attribute_path, ctx):
            if attribute_path == "$.timestamp":
                print(f"accessed, current timestamp:{datetime.now().timestamp()}")
                return datetime.now().timestamp()
            return None

    class GeoAttributeProvider(AttributeProvider):
        def get_attribute_value(self, ace: str, attribute_path: str, ctx: 'EvaluationContext'):
            if ace == "subject" and attribute_path[:5] == "$.geo":
                str = attribute_path[6:-1]
                point_list = str.split(' ')
                polygon_list = []
                for point in point_list:
                    tmp = point.split(',')
                    x = int(tmp[0])
                    y = int(tmp[1])
                    polygon_list.append((x, y))
                polygon = Polygon(polygon_list)

                auth_attributes = get_auth_attributes()
                auth_user_attributes = auth_attributes[0]
                position = auth_user_attributes.get('position', None)
                print("position: ", position)
                # if location is None:
                #    location = (0,0)
                point = Point(position)

                return int(polygon.contains(point))

            return None

    class TimespanAttributeProvider(AttributeProvider):
        def get_attribute_value(self, ace: str, attribute_path: str, ctx: 'EvaluationContext'):
            if ace == 'resource' and attribute_path[:10] == '$.timespan':
                timespan = int(attribute_path.partition(': ')[2])
                start = datetime.utcnow() - timedelta(seconds=timespan)
                thing_obj = ThingFrequency.objects(thing_id=thing_id).first()
                cnt = 0
                if str(user_id) in thing_obj.timestamps:
                    for timestamp in thing_obj.timestamps[str(user_id)][::-1]:
                        if timestamp < start:
                            break
                        cnt += 1
                    return cnt
                else:
                    return 0
            return None

    class OtherAttributeProvider(AttributeProvider):
        def get_attribute_value(self, ace: str, attribute_path: str, ctx):
            # Assume attribute_path is in the form "$.attribute_name"
            attr_name = re.search("[a-zA-Z_]+", attribute_path).group().lower()
            print("attribute_path: ", attribute_path)
            print("attr_name: ", attr_name)

            auth_attributes = get_auth_attributes()
            auth_user_attributes = auth_attributes[0]
            auth_server_attributes = auth_attributes[1]
            attr_value = auth_user_attributes.get(attr_name, None) or auth_server_attributes.get(attr_name, None)
            print("attr_value: ", attr_value)
            return attr_value

    client = MongoClient()
    storage = MongoStorage(client, db_name=policy_location)
    pdp = PDP(storage, EvaluationAlgorithm.HIGHEST_PRIORITY,
              [TimestampAttributeProvider(), TimespanAttributeProvider(), GeoAttributeProvider(),
               OtherAttributeProvider()])

    access_request_json = {
        "subject": {
            "id": str(user_id),
            "attributes": {"email": str(user_email)}
        },
        "resource": {
            "id": str(thing_id),
            "attributes": {"thing_type": request.get_json().get("thing_type", None)}
        },
        "action": {
            "id": "",
            "attributes": {"method": "get"}
        },
        "context": {
        }
    }

    access_request = AccessRequest.from_json(access_request_json)
    return pdp.is_allowed(access_request)


def set_auth_user_attr(attr_name, attr_value):
    print("(set_auth_user_attr)")
    user_id = current_user.get_user_id()
    auth_attribute = AuthAttribute.query.filter_by(id=user_id).first()
    auth_user_attributes = auth_attribute.get_user_attributes()
    auth_user_attributes[attr_name] = attr_value
    print("auth_user_attributes: ", auth_user_attributes)
    auth_attribute.set_user_attributes(auth_user_attributes)
    flask.session["info_authorize"] = 1


def set_auth_server_attr(attr_name, attr_value):
    print("set_auth_server_attr")
    user_id = current_user.get_user_id()
    auth_attribute = AuthAttribute.query.filter_by(id=user_id).first()
    auth_server_attributes = auth_attribute.get_server_attributes()
    auth_server_attributes[attr_name] = attr_value
    print("auth_server_attributes: ", auth_server_attributes)
    auth_attribute.set_server_attributes(auth_server_attributes)


def clear_auth_attributes():
    user_id = current_user.get_user_id()
    auth_attribute = AuthAttribute.query.filter_by(id=user_id).first()
    auth_attribute.set_user_attributes(auth_user_attr_default)
    auth_attribute.set_server_attributes(auth_server_attr_default)


def get_auth_attributes():
    print("(get_auth_attributes)")
    if current_user.is_anonymous:
        return "Please Login", 400
    user_id = current_user.get_user_id()
    auth_attribute = AuthAttribute.query.filter_by(id=user_id).first()
    auth_user_attributes = auth_attribute.get_user_attributes()
    auth_server_attributes = auth_attribute.get_server_attributes()
    print("[auth_user_attributes, auth_server_attributes]: ", [auth_user_attributes, auth_server_attributes])
    # return a list of two dictionaries
    return [auth_user_attributes, auth_server_attributes]


def is_policy_request(policy: dict, keys: list = []) -> bool:
    if policy is None:
        return False
    for key in keys:
        if key not in policy:
            return False
    return True


def generate_jwt(payload):
    """Encode given payload to jwt, return encoded jwt, private key, public key

    Args:
        payload (dict): the payload to be encoded in the jwt

    Returns:
        Encoded jwt(json), private key (str) used to encode the payload, public key(str) used to encode the payload

    """

    jwk_key = jwk.JWK.generate(kty='RSA', size=2048)
    priv_key = jwk_key.export_to_pem(private_key=True, password=None)
    pub_key = jwk_key.export_to_pem(password=None)
    encoded_jwt = jwt.encode(payload, priv_key, algorithm='RS256')
    return encoded_jwt, priv_key, pub_key


def jwt_to_thing(encoded_jwt, priv_key, algorithm='RS256'):
    """Decodes the jwt with private key

    Args:
        encoded_jwt (JSON): the encoded jwt created using generate_jwt(payload)
        priv_key (str): private key used to encode

    Returns:
        decoded jwt(json)

    """
    return jwt.decode(encoded_jwt, priv_key, algorithm=algorithm)
