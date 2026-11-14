"""Strict JSON for new write contracts; existing callers opt in explicitly."""
import json


def unique_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate JSON member')
        value[key]=item
    return value


def invalid_constant(value):
    raise ValueError('Non-finite JSON number')


def strict_loads(raw):
    try:value=json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_constant)
    except RecursionError as error:raise ValueError('JSON nesting exceeds the input contract') from error
    stack=[(value,1)]
    while stack:
        item,depth=stack.pop()
        if depth>32:raise ValueError('JSON nesting exceeds the input contract')
        if isinstance(item,dict):stack.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):stack.extend((child,depth+1) for child in item)
    return value
