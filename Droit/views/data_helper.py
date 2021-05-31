import json
import requests

def deduplicate_by_id(thing_list):
    """Deduplicate the thing description list according to its 'thing_id' field

    Args:
        thing_list (list): the list of thing description
    Returns:
        list: the deduplicated thing description list
    """
    thing_ids = set()
    unique_thing_list = []
    for thing in thing_list:
        if thing["thing_id"] not in thing_ids:
            thing_ids.add(thing["thing_id"])
            unique_thing_list.append(thing)
    return unique_thing_list


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


def get_data_field(thing_description, data_field_list, time_range):
    """Get the field specified by 'data_field_list' from each thing description

    Args:
        data_field_list(list): list of str that specified the hierarchical field names
            For example, if the parameter value is ['foo', 'bar', 'foobar'], then this
            function will try to get thing_description['foo']['bar']['foobar'] and return the value
            If any of the field does not exist, an error will occur
    Returns:
        object: the content specified by the data field
    """
    thing_id = thing_description['thing_id']
    for data_field in data_field_list:
        thing_description = thing_description[data_field]

    new_thing_description = []
    if 'data' in thing_description:
        res = get_time_range_data(thing_description['data'], time_range['start'], time_range['end'])
        new_thing_description.extend(res)

    if 'forms' in thing_description:
        res = get_time_range_data(thing_description['forms'], time_range['start'], time_range['end'])
        for source in res:
            try:
                response = requests.get(source.get('href'), params={'thing_id': thing_id,
                                                                    'data_field_list': str.join('.', data_field_list),
                                                                    'start': source['start'],
                                                                    'end': source['end']})
            except Exception as e:
                print(e)
            new_thing_description.extend(response.json())
    return new_thing_description


def get_compressed_list(thing_list, operation, data_field, time_range):
    """Get a compressed version of thing list input, keeping only thing_id and 'data_field'

    Args:
        thing_list(list): the list of thing description
        operation(str): one of the five aggregation operations
        data_field(str): property names. If it contains hierarchical property, then seperate each part using dot '.'

    Returns:
        list: compressed version of the input thing list
    """
    if operation == "COUNT":
        return list(map(lambda item: {"thing_id" : item["thing_id"]}, thing_list))

    data_field_list = data_field.split(".")

    def compress_function(thing_description):
        return_thing_desc = {"thing_id" : thing_description["thing_id"]}
        try:
            return_thing_desc["_query_data"] = thing_description["_query_data"] \
                if "_query_data" in thing_description else get_data_field(thing_description, data_field_list, time_range)
        except:
            return None

        return return_thing_desc

    return list(filter(lambda item: item is not None, map(compress_function, thing_list)))


def _weighted_sum(thing_list):
    s = 0
    start = float('inf')
    end = -1
    for thing_description in thing_list:
        for data in thing_description['_query_data']:
            s += data['data'] * (data['end'] - data['start'])
            start = min(data['start'], start)
            end = max(data['end'], end)
    return s, start, end


def _extract_data(thing_list):
    res = []
    for thing_description in thing_list:
        for data in thing_description["_query_data"]:
            res.append(data['data'])
    return res


def get_final_aggregation(thing_list, operation):
    """Generate the HTTP response content according to the operation and the result thing list

    Args:
        thing_list(list): the list of thing description
        operation(str): one of the five aggregation operations

    Returns:
        dict: formatted result containing the aggregation data
    """
    if operation != "COUNT" and len(thing_list) == 0:
        return {"operation": operation, "result": "unknown"}

    result = {"operation": operation}
    if operation == "COUNT":
        result["result"] = len(thing_list)
    elif operation == "MIN":
        result["result"] = min(_extract_data(thing_list))
    elif operation == "MAX":
        result["result"] = max(_extract_data(thing_list))
    elif operation == "AVG":
        s, start, end = _weighted_sum(thing_list)
        if end == start:
            return result
        result["result"] = s / (len(thing_list) * (end - start))
    elif operation == "SUM":
        result["result"], _, _ = _weighted_sum(thing_list)
    print(result)

    return result