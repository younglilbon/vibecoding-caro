"""Network communication protocol constants and helpers."""

import json
import socket
import struct
from typing import Any

from .constants import HEADER_SIZE, MAX_MESSAGE_SIZE

# Board settings
BOARD_SIZE = 15
WIN_STREAK = 5

# Action types
ACTION_MOVE = "MOVE"
ACTION_CHAT = "CHAT"
ACTION_JOIN = "JOIN"
ACTION_SURRENDER = "SURRENDER"
ACTION_ERROR = "ERROR"


class ConnectionClosedError(ConnectionError):
	"""Raised when the peer closes a connection before a message arrives."""


class InvalidMessageError(ValueError):
	"""Raised when a framed payload is not a JSON object."""


class MessageTooLargeError(InvalidMessageError):
	"""Raised when a message exceeds the shared protocol size limit."""


class MessageProtocol:
	"""Encode and decode messages using a 4-byte length-prefixed JSON frame."""

	@staticmethod
	def encode_message(message: dict[str, Any]) -> bytes:
		"""Serialize a JSON object and prefix its byte length."""
		try:
			payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
		except (TypeError, ValueError) as exc:
			raise InvalidMessageError("Message is not JSON serializable") from exc

		if len(payload) > MAX_MESSAGE_SIZE:
			raise MessageTooLargeError("Message exceeds the maximum size")
		return struct.pack(">I", len(payload)) + payload

	@staticmethod
	def decode_buffer(buffer: bytearray) -> tuple[dict[str, Any] | None, bytearray]:
		"""Decode one complete frame, preserving incomplete or trailing bytes."""
		if len(buffer) < HEADER_SIZE:
			return None, buffer

		payload_length = struct.unpack(">I", buffer[:HEADER_SIZE])[0]
		if payload_length > MAX_MESSAGE_SIZE:
			raise MessageTooLargeError("Message exceeds the maximum size")

		frame_length = HEADER_SIZE + payload_length
		if len(buffer) < frame_length:
			return None, buffer

		payload = bytes(buffer[HEADER_SIZE:frame_length])
		try:
			message = json.loads(payload.decode("utf-8"))
		except (UnicodeDecodeError, json.JSONDecodeError) as exc:
			raise InvalidMessageError("Payload is not valid UTF-8 JSON") from exc

		if not isinstance(message, dict):
			raise InvalidMessageError("Message payload must be a JSON object")
		return message, bytearray(buffer[frame_length:])

	@staticmethod
	def receive_message(
		connection: socket.socket, buffer: bytearray
	) -> tuple[dict[str, Any] | None, bytearray]:
		"""Read and decode one frame; on timeout, keep any partial bytes."""
		while True:
			message, remaining = MessageProtocol.decode_buffer(buffer)
			if message is not None:
				return message, remaining

			try:
				chunk = connection.recv(4096)
			except socket.timeout:
				return None, buffer
			if not chunk:
				raise ConnectionClosedError("Peer closed the connection")
			buffer.extend(chunk)

	@staticmethod
	def send_message(connection: socket.socket, message: dict[str, Any]) -> None:
		"""Send one complete framed message."""
		connection.sendall(MessageProtocol.encode_message(message))
