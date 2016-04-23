from __future__ import absolute_import, division, print_function, with_statement
from __future__ import unicode_literals
from pyee import EventEmitter
from collections import deque
from deepstreampy.message import message_builder, message_parser
from deepstreampy.constants import connection_state, topic, actions
from deepstreampy.constants import event as event_constants
from deepstreampy.constants import message as message_constants

from tornado import ioloop, tcpclient, concurrent
import socket


class _Connection(object):

    def __init__(self, client, host, port):
        self._io_loop = ioloop.IOLoop.current()

        self._client = client
        self._host = host
        self._port = port
        self._stream = None
        self._url = "{0}:{1}".format(host, port)

        self._auth_params = None
        self._auth_callback = None
        self._auth_future = None
        self._connect_callback = None

        self._message_buffer = ""
        self._deliberate_close = False
        self._too_many_auth_attempts = False
        self._queued_messages = deque()
        self._reconnect_timeout = None
        self._reconnection_attempt = 0
        self._current_packet_message_count = 0
        self._send_next_packet_timeout = None
        self._current_message_reset_timeout = None
        self._state = connection_state.CLOSED

    def connect(self, callback):
        self._connect_callback = callback
        connect_future = tcpclient.TCPClient().connect(self._host,
                                                       self._port,
                                                       socket.AF_INET)
        connect_future.add_done_callback(self._on_connect)
        return connect_future

    def _on_connect(self, f):
        self._stream = f.result()
        self._stream.read_until_close(None, self._on_data)
        self._state = connection_state.AWAITING_AUTHENTICATION

        if self._auth_params is not None:
            self._send_auth_params()

        if self._connect_callback:
            self._connect_callback()

    def start(self):
        self._io_loop.start()

    def stop(self):
        self._io_loop.stop()

    def close(self):
        if self._stream:
            self._stream.close()

    def authenticate(self, auth_params, callback):
        self._auth_params = auth_params
        self._auth_callback = callback
        self._auth_future = concurrent.Future()

        if self._too_many_auth_attempts:
            self._client._on_error(topic.ERROR, event_constants.IS_CLOSED,
                                   "this client's connection was closed")
            return self._auth_future

        elif self._deliberate_close and self._state == connection_state.CLOSED:
            self._connect()
            self._deliberate_close = False
            self._client.once(event_constants.CONNECTION_STATE_CHANGED,
                              lambda: self.authenticate(auth_params, callback))

        if self._state == connection_state.AWAITING_AUTHENTICATION:
            self._send_auth_params()

        return self._auth_future

    def _send_auth_params(self):
        self._state = connection_state.AUTHENTICATING
        raw_auth_message = message_builder.get_message(topic.AUTH,
                                                       actions.REQUEST,
                                                       [self._auth_params])
        self._stream.write(raw_auth_message.encode())

    def _handle_auth_response(self, message):
        message_data = message['data']
        message_action = message['action']
        data_size = len(message_data)
        if message_action == actions.ERROR:
            if (message_data and
                    message_data[0] == event_constants.TOO_MANY_AUTH_ATTEMPTS):
                self._deliberate_close = True
                self._too_many_auth_attempts = True
            else:
                self._set_state(connection_state.AWAITING_AUTHENTICATION)

            auth_data = (self._get_auth_data(message_data[1]) if
                         data_size > 1 else None)

            if self._auth_callback:
                self._auth_callback(False,
                                    message_data[0] if data_size else None,
                                    auth_data)
            if self._auth_future:
                self._auth_future.set_result(
                    {'success': False,
                     'error': message_data[0] if data_size else None,
                     'message': auth_data})

        elif message_action == actions.ACK:
            self._set_state(connection_state.OPEN)

            auth_data = (self._get_auth_data(message_data[0]) if
                         data_size else None)

            # Resolve auth future and callback
            if self._auth_future:
                self._auth_future.set_result(
                    {'success': True, 'error': None, 'message': auth_data})

            if self._auth_callback:
                self._auth_callback(True, None, auth_data)

            self._send_queued_messages()

    def _get_auth_data(self, data):
        if data:
            return message_parser.convert_typed(data, self._client)

    def _set_state(self, state):
        self._state = state
        self._client.emit(event_constants.CONNECTION_STATE_CHANGED, state)

    @property
    def state(self):
        return self._state

    def send(self, raw_message):
        if self._state == connection_state.OPEN:
            self._stream.write(raw_message.encode())
        else:
            self._queued_messages.append(raw_message.encode())

    def _send_queued_messages(self):
        if self._state != connection_state.OPEN:
            return

        while self._queued_messages:
            raw_message = self._queued_messages.popleft()
            self._stream.write(raw_message.encode())

    def _on_data(self, data):
        full_buffer = self._message_buffer + data.decode()
        split_buffer = full_buffer.rsplit(message_constants.MESSAGE_SEPERATOR,
                                          1)
        if len(split_buffer) > 1:
            self._message_buffer = split_buffer[1]
        raw_messages = split_buffer[0]

        parsed_messages = message_parser.parse(raw_messages, self._client)

        for msg in parsed_messages:
            if msg is None:
                continue

            if msg['topic'] == topic.AUTH:
                self._handle_auth_response(msg)
            else:
                self._client._on_message(parsed_messages[0])


class Client(EventEmitter, object):

    def __init__(self, host, port):
        super(Client, self).__init__()
        self._connection = _Connection(self, host, port)

        self._message_callbacks = dict()

        def not_implemented_callback(topic):
            raise NotImplementedError("Topic " + topic + " not yet implemented")

        self._message_callbacks[
            topic.WEBRTC] = lambda x: not_implemented_callback(topic.WEBRTC)

        self._message_callbacks[
            topic.EVENT] = lambda x: not_implemented_callback(topic.EVENT)

        self._message_callbacks[
            topic.RPC] = lambda x: not_implemented_callback(topic.RPC)

        self._message_callbacks[
            topic.RECORD] = lambda x: not_implemented_callback(topic.RECORD)

        self._message_callbacks[
            topic.ERROR] = lambda x: not_implemented_callback(topic.ERROR)

    def connect(self, callback=None):
        return self._connection.connect(callback)

    def start(self):
        self._connection.start()

    def stop(self):
        self._connection.stop()

    def close(self):
        self._connection.close()

    def login(self, auth_params, callback=None):
        return self._connection.authenticate(auth_params, callback)

    def _on_message(self, message):
        # TODO: Call message callback for the topic
        if message['topic'] in self._message_callbacks:
            self._message_callbacks[message['topic']](message)
        else:
            self._on_error(message['topic'],
                           event_constants.MESSAGE_PARSE_ERROR,
                           ('Received message for unknown topic ' +
                            message['topic']))
            return

        if message['action'] == actions.ERROR:
            self._on_error(message['topic'],
                           message['action'],
                           message['data'][0] if len(message['data']) else None)

    def _on_error(self, topic, event, msg):
        if event in (event_constants.ACK_TIMEOUT,
                     event_constants.RESPONSE_TIMEOUT):
            if (self._connection.state ==
                    connection_state.AWAITING_AUTHENTICATION):
                error_msg = ('Your message timed out because you\'re not '
                             'authenticated. Have you called login()?')
                self._connection._io_loop.call_later(
                    0.1, lambda: self._on_error(event.NOT_AUTHENTICATED,
                                                topic.ERROR,
                                                error_msg)
                )

        if self.listeners('error'):
            self.emit('error', msg, event, topic)
            self.emit(event, topic, msg)
        else:
            raw_error_message = event + ': ' + msg

            if topic:
                raw_error_message += ' (' + topic + ')'

            raise ValueError(raw_error_message)

    @property
    def connection_state(self):
        return self._connection.state