from __future__ import absolute_import, division, print_function, with_statement
from __future__ import unicode_literals

from deepstreampy import client
from deepstreampy.record import Record
from deepstreampy.constants import connection_state
from deepstreampy.utils import Undefined

from tornado import testing
import unittest
import sys

if sys.version_info[0] < 3:
    import mock
else:
    from unittest import mock

URL = "ws://localhost:7777/deepstream"


class RecordTest(testing.AsyncTestCase):

    def setUp(self):
        super(RecordTest, self).setUp()
        self.client = client.Client(URL)
        self.handler = mock.Mock()
        self.handler.stream.closed = mock.Mock(return_value=False)
        self.client._connection._state = connection_state.OPEN
        self.client._connection._websocket_handler = self.handler
        self.connection = self.client._connection
        self.io_loop = self.connection._io_loop
        self.options = {'recordReadAckTimeout': 100, 'recordReadTimeout': 200}
        self.record = Record('testRecord',
                             self.connection,
                             self.options,
                             self.client)
        self.record._send_read()
        message = {'topic': 'R', 'action': 'R', 'data': ['testRecord', 0, '{}']}
        self.record._on_message(message)

    def test_create_record(self):
        self.assertEqual(self.record.get(), {})
        self.handler.write_message.assert_called_with(
            "R{0}CR{0}testRecord{1}".format(chr(31), chr(30)).encode())

    def test_send_update_message(self):
        self.record.set({'firstname': 'John'})
        expected = ("R{0}U{0}testRecord{0}1{0}{{\"firstname\":\"John\"}}{1}"
                    .format(chr(31), chr(30)).encode())
        self.handler.write_message.assert_called_with(expected)
        self.assertEqual(self.record.get(), {'firstname': 'John'})
        self.assertEquals(self.record.version, 1)

    def test_send_patch_message(self):
        self.record.set('Smith', 'lastname')
        expected = ("R{0}P{0}testRecord{0}1{0}lastname{0}SSmith{1}"
                    .format(chr(31), chr(30)).encode())
        self.handler.write_message.assert_called_with(expected)

    def test_delete_value(self):
        self.record.set({'firstname': 'John', 'lastname': 'Smith'})
        self.record.set(Undefined, 'lastname')
        self.assertEqual(self.record.get(), {'firstname': 'John'})

    def test_delete(self):
        self.record.delete()
        expected = "R{0}D{0}testRecord{1}".format(chr(31), chr(30)).encode()
        self.handler.write_message.assert_called_with(expected)
        message = {'topic': 'R', 'action': 'A', 'data': ['D', 'testRecord']}
        self.record._on_message(message)
        self.assertTrue(self.record.is_destroyed)
        self.assertFalse(self.record.is_ready)

    def test_invalid(self):
        self.assertRaises(ValueError, self.record.set, Undefined)

    def test_send_update_with_callback(self):
        callback = mock.Mock()
        self.record.set({'firstname': 'John'}, callback=callback)
        expected = ("R{0}U{0}testRecord{0}1{0}{{\"firstname\":\"John\"}}{0}"
                    "{{\"writeSuccess\":true}}{1}"
                    .format(chr(31), chr(30)).encode())
        self.handler.write_message.assert_called_with(expected)
        self.assertEqual(self.record.get(), {'firstname': 'John'})
        self.assertEquals(self.record.version, 1)

    def test_send_patch_message_with_callback(self):
        callback = mock.Mock()
        self.record.set('Smith', 'lastname', callback)
        expected = ("R{0}P{0}testRecord{0}1{0}lastname{0}SSmith{0}"
                    "{{\"writeSuccess\":true}}{1}"
                    .format(chr(31), chr(30)).encode())
        self.handler.write_message.assert_called_with(expected)

    def tearDown(self):
        super(RecordTest, self).tearDown()
        self.handler.mock_reset()

if __name__ == '__main__':
    unittest.main()
