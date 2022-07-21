import os

from flask import Flask, jsonify, request, abort
from flask_openvidu import OpenVidu
from pyopenvidu import OpenViduSessionDoesNotExistsError, OpenViduConnectionDoesNotExistsError
from flask_redis import FlaskRedis
import bleach
import json
import time
from flask_cors import CORS

app = Flask(__name__)

app.config["OPENVIDU_URL"] = os.environ["OPENVIDU_URL"]
app.config["OPENVIDU_SECRET"] = os.environ["OPENVIDU_SECRET"]
app.config["REDIS_URL"] = os.environ["REDIS_URL"]
app.config["CALLBACK_SECRET"] = os.environ["CALLBACK_SECRET"]
app.config["CORS_ORIGINS"] = ["https://caffe.marcsello.com"]

CORS(app)

ov = OpenVidu(app)
redis_client = FlaskRedis(app)


@app.route('/api/room/<room>/token', methods=['POST'])
def tokengenerate(room):
    if not request.json:
        return abort(400)

    is_screenshare = False
    try:
        is_screenshare = bool(request.json['screenshare'])
    except KeyError:
        pass

    # create session if necessary
    try:
        username = bleach.clean(request.json['username'][:32].replace('%', '').replace('/', ''), tags=[])
    except KeyError:
        return abort(400)

    if not username:
        return abort(400)

    is_creator = False
    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        is_creator = True
        session = ov.connection.create_session(room)

    potato_mode = bool(request.json.get('potato_mode', False))
    data = json.dumps(
        {'username': username, 'join_time': time.time(), 'is_creator': is_creator, 'screenshare': is_screenshare})

    if potato_mode:
        connection = session.create_webrtc_connection(
            data=data,
            video_max_recv_bandwidth=400,
            video_min_recv_bandwidth=150,
            video_max_send_bandwidth=500,
            video_min_send_bandwidth=150
        )
    else:
        connection = session.create_webrtc_connection(data=data)

    token = connection.token
    return jsonify({"token": token})


@app.route('/api/room/<room>/connections', methods=['GET'])
def connection_id_list(room):
    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(404)

    conns = []
    for conn in session.connections:
        if conn.publishers:
            stream_id = conn.publishers[0].stream_id
        else:
            stream_id = None

        conns.append({"id": conn.id, "data": json.loads(conn.server_data), "stream_id": stream_id})

    return jsonify(conns)


@app.route('/api/room/<room>/connections/<_id>', methods=['GET'])
def connection_id_single(room, _id):
    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(404)

    try:
        conn = session.get_connection(_id)
    except OpenViduConnectionDoesNotExistsError:
        return abort(404)

    connjson = {
        'id': _id,
        'stream_id': conn.publishers[0].stream_id
    }

    return jsonify(connjson)


@app.route('/api/room/<room>/motd', methods=['GET'])
def get_motd(room):
    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(404)

    motd = redis_client.get("motd" + room)
    if motd:
        motd = motd.decode('utf-8')

    return jsonify({"motd": motd})


@app.route('/api/room/<room>/motd', methods=['POST'])
def update_motd(room):
    if not request.json:
        return abort(400)

    # create session if necessary
    try:
        motd = bleach.clean(request.json['motd'][:256], tags=['b', 'i'])
    except KeyError:
        return abort(400)

    if not motd:
        return abort(400)

    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(404)

    redis_client.set("motd" + room, motd.encode('utf-8'), ex=12 * 60 * 60)  # Motd expires after 12 hours

    session.signal("MOTD", motd)

    return jsonify({"motd": motd})


def cmd_ping(room, args):
    return "pong"


def cmd_updatemotd(room, args):
    try:
        motd = bleach.clean(args[:256], tags=['b', 'i'])
    except KeyError:
        return abort(400)

    if not motd:
        return abort(400)

    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(400)

    redis_client.set("motd" + room, motd.encode('utf-8'), ex=12 * 60 * 60)  # Motd expires after 12 hours

    session.signal("MOTD", motd)

    return "motd applied"


def cmd_list(room, args):
    try:
        session = ov.connection.get_session(room)
    except OpenViduSessionDoesNotExistsError:
        return abort(400)

    conns = []
    for conn in session.connections:
        name = json.loads(conn.server_data)['username']
        conns.append(f"ID: {conn.id} NAME: {name}")

    return '\n'.join(conns)


@app.route('/api/room/<room>/cmd/<cmd>', methods=['POST'])
def execute_command(room, cmd):
    if not request.json:
        return abort(400)

    try:
        args = bleach.clean(request.json['args'][:1024], tags=[])
    except KeyError:
        return abort(400)

    try:
        function = {
            'ping': cmd_ping,
            'motd': cmd_updatemotd,
            'list': cmd_list
        }[cmd]
    except KeyError:
        return abort(404)

    return jsonify({'output': function(room, args)})


# this is protected by the reverse proxy lol
@app.route('/internal/webhook', methods=['POST'])
def cleanup_after_session():
    if request.headers.get("Authorization") != app.config["CALLBACK_SECRET"]:
        return abort(401)

    data = request.json

    if data and data['event'] == 'sessionDestroyed':
        room = data['sessionId']
        redis_client.delete("motd" + room)

    return ""


if __name__ == '__main__':
    app.run()
