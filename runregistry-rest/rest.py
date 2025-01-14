__author__ = "Roland Sipos"
__credits__ = [""]
__version__ = "0.0.1"
__maintainer__ = "Roland Sipos"
__email__ = "roland.sipos@cern.ch"

import os, io, gzip, tarfile
import flask
from flask import Flask, render_template, request, redirect, url_for, send_file

from flask_restful import Api, Resource
from flask_httpauth import HTTPBasicAuth
from flask_redis import FlaskRedis
from flask_caching import Cache

import queries
import backend as db
from authentication import auth

'''
Specs
'''
# Create an APISpec
#spec = APISpec(
#    title='Swagger Sensors',
#    version='1.0.0',
#    openapi_version='2.0',
#    plugins=[
#        FlaskPlugin(),
#        MarshmallowPlugin(),
#    ],
#)

'''
Main app
'''
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1000 * 1000
app.config['UPLOAD_EXTENSIONS'] = ['.gz', '.tgz']
app.config['UPLOAD_PATH'] = 'uploads'
app.config['CACHE_TYPE'] = 'redis' # easier to scale it and async disk writes provides DB dumps.
cache = Cache(app)
api = Api(app)

'''
For REDIS cache
'''
def cache_key():
    args = flask.request.args
    key = flask.request.path + '?' + urllib.urlencode([
       (k, v) for k in sorted(args) for v in sorted(args.getlist(k))
    ])
    return key

'''
Append schema to results
'''
def add_schema_as_element(rowres):
    rowres.insert(0, queries.schema)

# $ curl -u fooUsr:barPass -X GET np04-srv-021:5005/runregistry/getRunMeta/2 
@api.resource("/runregistry/getRunMeta/<int:runNum>")
class getRunMeta(Resource):
    @auth.login_required
    def get(self, runNum):
        rowRes = []
        try:
            db.perform_query(queries.getRunMeta, {'run_num':runNum}, rowRes)
        except Exception as e:
            err_obj, = e.args
            print("Exception:", err_obj.message)
            resp = flask.make_response(flask.jsonify({"Exception": err_obj.message}))
            return resp
        #print(rowRes)
        add_schema_as_element(rowRes)
        resp = flask.make_response(flask.jsonify(rowRes))
        return resp

# $ curl -u fooUsr:barPass -X GET np04-srv-021:5005/runregistry/getRunMetaLast/100 
@api.resource("/runregistry/getRunMetaLast/<int:amount>")
class getRunMetaLast(Resource):
    @auth.login_required
    def get(self, amount):
        rowRes = []
        try:
            db.perform_query(queries.getRunMetaLast, {'amount':amount}, rowRes)
        except Exception as e:
            err_obj, = e.args
            print("Exception:", err_obj.message)
            resp = flask.make_response(flask.jsonify({"Exception": err_obj.message}))
            return resp
        #print(rowRes)
        add_schema_as_element(rowRes)
        resp = flask.make_response(flask.jsonify(rowRes))
        return resp

# $ curl -u fooUsr:barPass -X GET -O -J np04-srv-021:5005/runregistry/getRunBlob/2 
@api.resource("/runregistry/getRunBlob/<int:runNum>")
class getRunBlob(Resource):
    @auth.login_required
    @cache.cached(timeout=0, key_prefix=cache_key, query_string=True)
    def get(self, runNum):
        rowRes = []
        try:
            db.perform_query(queries.getRunBlob, {'run_num':runNum}, rowRes)
        except Exception as e:
            err_obj, = e.args
            print('Exception:', err_obj.message)
            resp = flask.make_response(flask.jsonify({"Exception": err_obj.message}))
            return resp
        filename = rowRes[0][0][0]
        blob = rowRes[0][0][1]
        resp = flask.make_response(blob.read())
        resp.headers["Content-Type"] = "application/octet-stream"
        resp.headers["Content-Disposition"] = "attachment; filename=%s" % filename
        return resp

# $ curl -u fooUsr:barPass -F "file=@sspconf.tar.gz" -F "run_num=4" -F "det_id=foo" -F "run_type=bar" -F "software_version=dunedaq-vX.Y.Z" -X POST http://localhost:5005/runregistry/insertRun/
@api.resource("/runregistry/insertRun/")
class insertRun(Resource):
    @auth.login_required
    def post(self):
        filename = ""
        try:
            # Ensure form fields
            run_num = request.form['run_num']
            stop_time = None
            det_id = request.form['det_id']
            run_type = request.form['run_type']
            software_version = request.form['software_version']
            uploaded_file = request.files['file']
            filename = uploaded_file.filename

            # Save uploaded file temporarily
            if filename != '':
                file_ext = os.path.splitext(filename)[1]
                if file_ext not in app.config['UPLOAD_EXTENSIONS']:
                    error = "Unknown file extension! File needs to be .tar.gz or .tgz file! \n"
                    return flask.make_response(error, 400)
                local_file_name = os.path.join(app.config['UPLOAD_PATH'], filename)
                if os.path.isfile(local_file_name):
                    error = "BLOB insert is ongoing with the same file name! Try again a bit later."
                    return flask.make_response(error, 400)
                uploaded_file.save(local_file_name)
            else:
                error = "Expected file (conf blob) name is missing in form! \n"
                return flask.make_response(error, 400)

            # Read in file to memory
            with open(local_file_name, 'rb') as fin:
                data = io.BytesIO(fin.read())

            # Perform insert
            query_list = []
            bind_vars = []
            query_list.append(queries.insertRunRegistryMeta)
            query_list.append(queries.insertRunRegistryBlob)
            bind_vars.append({'run_num':run_num, 'det_id':det_id, 'run_type':run_type, 'filename':filename, 'software_version':software_version})
            bind_vars.append({'run_num':run_num, 'config_blob':data.getvalue()})
            db.perform_transaction_multi(query_list, bind_vars) 
            rowRes = []
            db.perform_query(queries.getRunMeta, {'run_num':run_num}, rowRes)
            resp = flask.make_response(flask.jsonify(rowRes))
            # remove uploaded temp file
            os.remove(local_file_name)
            return resp
        except Exception as e:
            print("Exception:", e)
            return flask.make_response(str(e), 400)

@api.resource("/runregistry/updateStopTime/<int:runNum>")
class updateStopTimestamp(Resource):
    @auth.login_required
    def get(self, runNum):
        rowRes = []
        try:
            db.perform_transaction(queries.updateStopTime, {'run_num':runNum})
            db.perform_query(queries.getRunMeta, {'run_num':runNum}, rowRes)
        except Exception as e:
            err_obj, = e.args
            print("Exception:", str(e))
            resp = flask.make_response(flask.jsonify({"Exception": err_obj.message}))
            return resp
        print(rowRes)
        resp = flask.make_response(flask.jsonify(rowRes))
        return resp

@app.route('/')
def index():
    return "Best thing since sliced bread!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)

# As a testserver.
#app.run(host= '0.0.0.0', port=5000, debug=True)
# Normally spawned by gunicorn
