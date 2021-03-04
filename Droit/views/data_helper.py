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


def get_data_field(thing_description, data_field_list):
    """Get the field specified by 'data_field_list' from each thing description

    Args:
        data_field_list(list): list of str that specified the hierarchical field names
            For example, if the parameter value is ['foo', 'bar', 'foobar'], then this
            function will try to get thing_description['foo']['bar']['foobar'] and return the value
            If any of the field does not exist, an error will occur
    Returns:
        object: the content specified by the data field
    """
    for data_field in data_field_list:
        thing_description = thing_description[data_field]
    return thing_description


def get_compressed_list(thing_list, operation, data_field):
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
            return_thing_desc["_query_data"] = thing_description["_query_data"] if "_query_data" in thing_description else get_data_field(thing_description, data_field_list)
        except:
            return None

        return return_thing_desc

    return list(filter(lambda item: item is not None, map(compress_function, thing_list)))


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
        result["result"] = min([thing_description["_query_data"] for thing_description in thing_list])
    elif operation == "MAX":
        result["result"] = max([thing_description["_query_data"] for thing_description in thing_list])
    elif operation == "AVG":
        result["result"] = sum([thing_description["_query_data"] for thing_description in thing_list]) / len(thing_list)
    elif operation == "SUM":
        result["result"] = sum([thing_description["_query_data"] for thing_description in thing_list])

    return result