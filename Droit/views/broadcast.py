import json
import requests
from flask import url_for
from flask import current_app as app
from urllib.parse import urljoin, urlencode

from ..models import ThingDescription, DirectoryNameToURL, TypeToChildrenNames, TargetToChildName


def delete_local_thing_description(thing_id: str):
    """Delete the thing description with the specific 'thing_id' in local directory and return whether the deletion is complete.

    This is the function that perform the real thing description deletion oepration. It will do it locally by deleting the
    thing description specified by `thing_id` field. If the to-be-delete thing description has publicity larger than 1, it
    will send addition request to its parent directory to totally remove the record. This is a recursive request and only
    until its finished, this function should return.

    Args:
        thing_id (str): ID for thing description to be deleted

    Return:
        bool: True if the deletion is complete, if any error happens in the whole prcesss, then return False
    """
    delete_thing = ThingDescription.objects(thing_id=thing_id).first()
    if delete_thing is None:
        return
    delete_thing.delete()
    # 1. if the publicity is larger than 0, it needs to recursively delete the thing in parent's directory
    if delete_thing.publicity > 0:
        delete_up_things(delete_thing.thing_id)
    # 2. if current directory has no other thing_description of this type,
    # should update parent's aggregation information to delete this one
    dir_remaining_count = ThingDescription.objects(
        thing_type=delete_thing.thing_type).count()
    if dir_remaining_count == 0:
        parent_aggregation('delete', delete_thing.thing_type, app.config['HOST_NAME'])


def push_up_things(thing_description: dict, publicity: int):
    """
    Send register request to parent directory, only if the publicity is larger than 0 and current directory has parent

    Args:
        thing_description (dict): the thing description may need to be pushed up
        publicity (int): how many levels the thing needs to be pushed up

    Return:
        bool: boolean value indicating the push up result. If succeed, return True, otherwise False
    """
    parent_directory = DirectoryNameToURL.objects(
        relationship='parent').first()
    # 1. only do push-up when the publicity is larger than 0, and it has parent
    if publicity == 0 or parent_directory is None:
        return True

    # 2. send push up request to the parent url
    parent_url = urljoin(parent_directory.url, url_for('api.register'))
    request_data = {
        "td": thing_description,
        "location": parent_directory.directory_name,
        "publicity": publicity - 1
    }

    response = requests.post(parent_url, data=json.dumps(request_data), headers={
        'Content-Type': 'application/json',
        'Accept-Charset': 'UTF-8'
    })

    return response.status_code == 200


def delete_up_things(thing_id: str) -> bool:
    """Send delete request to parent's directory's /delete API, asking to delete the thing description.

    Args:
        thing_id (str): Unique identifer of thing description that specify the thing description to be deleted.
    Return:
        bool: True if the deletion is complete, otherwise False.
    """
    parent_dir = DirectoryNameToURL.objects(relationship='parent').first()
    response = None
    if parent_dir is not None:
        query_parameters = urlencode(
            {"location": parent_dir.directory_name, "thing_id": thing_id})
        request_url = f"{urljoin(parent_dir.url, url_for('api.delete'))}?{query_parameters}"
        try:
            response = requests.delete(request_url)
        except:
            return False
    return response is None or response.status_code == 200


def parent_aggregation(operation: str, thing_type: str, location: str) -> bool:
    """Send a post request to parent's directory to update the aggregation data.

    Args:
        thing_type(str): Specify the type of the aggregation.
        location(str): the directory name that the aggregation should be using to update.

    Returns:
        bool: True if the update is complete, otherwise False.
    :return: boolean value indicating the update result. return True if update successfully
    """

    parent_dir = DirectoryNameToURL.objects(relationship='parent').first()
    if parent_dir is None:
        return True

    request_body = {"location": location, "thing_type": thing_type}
    response = None

    if operation == "add":
        request_url = urljoin(parent_dir.url, url_for(
            'api.update_type_aggregation'))
        response = requests.post(request_url, data=json.dumps(request_body), headers={
            'Content-Type': 'application/json',
            'Accept-Charset': 'UTF-8'
        })
    elif operation == "delete":
        request_url = f"{urljoin(parent_dir.url, url_for('api.update_type_aggregation'))}?{urljoin(request_body)}"
        response = requests.delete(request_url)

    return response and response.status_code == 200


def get_children_result(thing_type: str, api: str, query_string: str) -> list:
    """Get thing descriptions from all children directories and return the result

    This operation is done recursively using a DFS serach algorithm. The request is sent to the endpoint of
    children directory specified by `api` argument along with `query_string` as the query parameters.
    If `thing_type` is specified, then only thing descriptions matching the `thing_type` argument is collected.

    Args:
        thing_type(str): Type of thing descriptions to return. Is this is missing, then no filtering will be doing.
        api(str): The API endpoint of the children directories.
        query_string(str): Query string of the of the requests sent to children directories.

    Returns:
        list: the list of thing descriptions that meet the filter condition. Each thing description is a dict object.
    """
    children_directories = DirectoryNameToURL.objects(
        relationship='child').all()
    # Get children names that contains only the 'thing_type' according to the aggregation stats
    descendant_names_with_type = TypeToChildrenNames.objects(thing_type=thing_type).first() \
        if thing_type is not None else TypeToChildrenNames.objects.first()
    descendant_to_child_mappings = TargetToChildName.objects().all()
    # Send request to each child node that has thing descriptions with this [thing_type] and get result as a list
    result_list = []
    if children_directories and descendant_names_with_type:
        child_name_to_url_map = {
            child_directory.directory_name: child_directory.url for child_directory in children_directories
        }
        if descendant_to_child_mappings:
            for mapping in descendant_to_child_mappings:
                child_name_to_url_map[mapping.target_name] = child_name_to_url_map[mapping.child_name]

        for descendant_directory_name in descendant_names_with_type.children_names:
            child_url = child_name_to_url_map[descendant_directory_name]
            para_dict = dict(k.split('=') for k in query_string.split('&'))
            if 'location' in para_dict.keys():
                # tmpstr = query_string.split('&', 1)[1]
                # new_query_string = f"location={descendant_directory_name}&{tmpstr}"
                para_dict['location'] = descendant_directory_name
                new_query_string = urlencode(para_dict)
                request_url = f"{urljoin(child_url, api)}?{new_query_string}"
            else:
                request_url = f"{urljoin(child_url, api)}?{query_string}"
            response = requests.get(request_url)
            if response.status_code != 200:
                continue
            child_result = response.json()
            if type(child_result) == list:
                result_list.extend(child_result)
            else:
                result_list.append(child_result)
    return result_list

