from datetime import datetime, timedelta
import json, csv
from StringIO import StringIO
from decimal import Decimal
from functools import update_wrapper
from hashlib import sha1

from sqlalchemy.orm.query import Query
from werkzeug.exceptions import NotFound
from werkzeug.http import is_resource_modified
#from formencode.variabledecode import NestedVariables
from flask import Response, request, current_app, make_response
from flask import stream_with_context

MIME_TYPES = {
        'text/html': 'html',
        'text/csv': 'csv',
        'application/xhtml+xml': 'html',
        'application/json': 'json',
        'text/javascript': 'json',
        }

def request_format(request):
    """
    Determine the format of the request content. This is slightly
    ugly as Flask has excellent request handling built in and we
    begin to work around it.
    """
    return MIME_TYPES.get(request.content_type, 'html')


def request_content(request):
    """
    Handle a request and return a generator which yields all rows
    in the incoming set.
    """
    format = request_format(request)
    if format == 'json':
        return json.loads(request.data)
    else:
        return request.form
        #nv = NestedVariables()
        #return nv.to_python(request.form)


class JSONEncoder(json.JSONEncoder):
    """ This encoder will serialize all entities that have a to_dict
    method by calling that method and serializing the result. """

    def __init__(self, shallow=False):
        self.shallow = shallow
        super(JSONEncoder, self).__init__()

    def encode(self, obj):
        #if self.shallow and hasattr(obj, 'as_shallow'):
        #    return obj.as_shallow()
        #if hasattr(obj, 'to_dict'):
        #    obj = obj.to_dict()
        return super(JSONEncoder, self).encode(obj)

    def default(self, obj):
        if self.shallow and hasattr(obj, 'as_shallow'):
            return obj.as_shallow()
        if hasattr(obj, 'as_dict'):
            return obj.as_dict()
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, Query):
            return list(obj)
        raise TypeError("%r is not JSON serializable" % obj)


def jsonify(obj, status=200, headers=None, shallow=False):
    """ Custom JSONificaton to support obj.to_dict protocol. """
    jsondata = JSONEncoder(shallow=shallow).encode(obj)
    if 'callback' in request.args:
        jsondata = '%s(%s)' % (request.args.get('callback'), jsondata)
    return Response(jsondata, headers=headers,
                    status=status, mimetype='application/json')

def stream_csv(source, headers=None, status=200, filename=None):
    def generate_csv():
        headers = None
        for entry in source:
            row = {}
            for k, v in entry.items():
                if v == None:
                    v = ""
                if isinstance(v, (list, tuple, dict)):
                    continue
                elif isinstance(v, datetime):
                    v = v.isoformat()
                elif isinstance(v, float):
                    v = u'%.2f' % v
                row[unicode(k).encode('utf8')] = unicode(v).encode('utf8')
            sio = StringIO()
            writer = csv.writer(sio)
            if headers is None:
                headers = row.keys()
                writer.writerow(headers)
            writer.writerow([row.get(k) for k in headers])
            yield sio.getvalue()
    if filename:
        headers = headers if headers is not None else {}
        headers['Content-Disposition'] = 'attachment; filename=%s' % filename

    return Response(stream_with_context(generate_csv()), headers=headers,
                    status=status, mimetype='text/csv')

# quite hackish:
def _response_format_from_path(request):
    # This means: using <format> for anything but dot-notation is really
    # a bad idea here.
    adapter = current_app.create_url_adapter(request)
    try:
        return adapter.match()[1].get('format')
    except NotFound:
        return None


def response_format(request):
    """  Use HTTP Accept headers (and suffix workarounds) to
    determine the representation format to be sent to the client.
    """
    fmt = _response_format_from_path(request)
    if fmt in MIME_TYPES.values():
        return fmt
    neg = request.accept_mimetypes.best_match(MIME_TYPES.keys())
    return MIME_TYPES.get(neg)


class NotModified(Exception):
    pass


def validate_cache(request):
    etag = sha1(repr(sorted(request.cache_key.items()))).hexdigest()
    mod_time = request.cache_key.get('modified')
    if request.method != 'GET':
        return etag, mod_time
    if not is_resource_modified(request.environ, etag=etag, last_modified=mod_time):
        raise NotModified()
    if request.if_none_match == etag:
        raise NotModified()
    return etag, mod_time


# from http://flask.pocoo.org/snippets/56/
def crossdomain(origin=None, methods=None, headers=None,
                max_age=21600, attach_to_all=True,
                automatic_options=True):
    if methods is not None:
        methods = ', '.join(sorted(x.upper() for x in methods))
    if headers is not None and not isinstance(headers, basestring):
        headers = ', '.join(x.upper() for x in headers)
    if not isinstance(origin, basestring):
        origin = ', '.join(origin)
    if isinstance(max_age, timedelta):
        max_age = max_age.total_seconds()

    def get_methods():
        if methods is not None:
            return methods

        options_resp = current_app.make_default_options_response()
        return options_resp.headers['allow']

    def decorator(f):
        def wrapped_function(*args, **kwargs):
            if automatic_options and request.method == 'OPTIONS':
                resp = current_app.make_default_options_response()
            else:
                resp = make_response(f(*args, **kwargs))
            if not attach_to_all and request.method != 'OPTIONS':
                return resp

            h = resp.headers

            h['Access-Control-Allow-Origin'] = origin
            h['Access-Control-Allow-Methods'] = get_methods()
            h['Access-Control-Max-Age'] = str(max_age)
            if headers is not None:
                h['Access-Control-Allow-Headers'] = headers
            return resp

        f.provide_automatic_options = False
        return update_wrapper(wrapped_function, f)
    return decorator