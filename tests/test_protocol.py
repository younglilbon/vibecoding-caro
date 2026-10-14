"""Unit tests for MessageProtocol network framing and encoding."""

import socket
import struct
import unittest

from Code.Shared.protocol import (
    ConnectionClosedError,
    InvalidMessageError,
    MessageProtocol,
    MessageTooLargeError,
)
from Code.Shared.message_types import MessageType


class TestMessageProtocol(unittest.TestCase):
    def test_encode_and_decode_single_message(self):
        payload = {"type": MessageType.JOIN.value, "player": "Alice"}
        encoded = MessageProtocol.encode_message(payload)

        header_length = struct.unpack(">I", encoded[:4])[0]
        self.assertEqual(header_length, len(encoded) - 4)

        buffer = bytearray(encoded)
        decoded, remaining = MessageProtocol.decode_buffer(buffer)
        self.assertEqual(decoded, payload)
        self.assertEqual(len(remaining), 0)

    def test_partial_data_fragmentation(self):
        payload = {"type": MessageType.MOVE.value, "x": 5, "y": 10}
        encoded = MessageProtocol.encode_message(payload)

        part1 = encoded[:6]
        part2 = encoded[6:]

        buffer = bytearray(part1)
        decoded, remaining = MessageProtocol.decode_buffer(buffer)
        self.assertIsNone(decoded)
        self.assertEqual(remaining, bytearray(part1))

        buffer.extend(part2)
        decoded, remaining = MessageProtocol.decode_buffer(buffer)
        self.assertEqual(decoded, payload)
        self.assertEqual(len(remaining), 0)

    def test_multiple_messages_in_single_buffer(self):
        payload_1 = {"type": MessageType.CHAT.value, "text": "Hello"}
        payload_2 = {"type": MessageType.CHAT.value, "text": "World"}

        buffer = bytearray(
            MessageProtocol.encode_message(payload_1)
            + MessageProtocol.encode_message(payload_2)
        )

        message_1, buffer = MessageProtocol.decode_buffer(buffer)
        self.assertEqual(message_1, payload_1)

        message_2, buffer = MessageProtocol.decode_buffer(buffer)
        self.assertEqual(message_2, payload_2)
        self.assertEqual(len(buffer), 0)

    def test_message_too_large_on_encode(self):
        big_payload = {"data": "x" * (1024 * 1024 + 1)}
        with self.assertRaises(MessageTooLargeError):
            MessageProtocol.encode_message(big_payload)

    def test_message_too_large_on_decode_header(self):
        invalid_header = struct.pack(">I", 1024 * 1024 + 1)
        with self.assertRaises(MessageTooLargeError):
            MessageProtocol.decode_buffer(bytearray(invalid_header))

    def test_invalid_json_payload(self):
        bad_json = b"{invalid_json:"
        header = struct.pack(">I", len(bad_json))
        buffer = bytearray(header + bad_json)

        with self.assertRaises(InvalidMessageError):
            MessageProtocol.decode_buffer(buffer)

    def test_send_and_receive_message_over_socketpair(self):
        sender, receiver = socket.socketpair()
        try:
            payload = {"type": MessageType.HEARTBEAT.value}
            MessageProtocol.send_message(sender, payload)
            received, remaining = MessageProtocol.receive_message(receiver, bytearray())
            self.assertEqual(received, payload)
            self.assertEqual(len(remaining), 0)
        finally:
            sender.close()
            receiver.close()

    def test_connection_closed_mid_message(self):
        sender, receiver = socket.socketpair()
        try:
            encoded = MessageProtocol.encode_message({"type": MessageType.MOVE.value})
            sender.sendall(encoded[:5])
            sender.close()
            with self.assertRaises(ConnectionClosedError):
                MessageProtocol.receive_message(receiver, bytearray())
        finally:
            receiver.close()

    def test_timeout_returns_none_and_keeps_buffer(self):
        sender, receiver = socket.socketpair()
        try:
            receiver.settimeout(0.01)
            received, remaining = MessageProtocol.receive_message(receiver, bytearray())
            self.assertIsNone(received)
            self.assertEqual(len(remaining), 0)
        finally:
            sender.close()
            receiver.close()


if __name__ == "__main__":
    unittest.main()
