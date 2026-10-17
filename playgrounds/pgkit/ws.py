"""
pgkit.ws — minimal WebSocket framing on top of a plain socket.

Only what we need for short JSON text frames:

    · the opening handshake (Sec-WebSocket-Accept computation)
    · reading one frame at a time (opcode + length + mask + payload)
    · writing one text frame at a time

No extensions, no continuation frames, no binary frames, no ping /
pong handling beyond treating them as harmless traffic.  A browser
or a CLI that speaks plain RFC 6455 will interoperate; anything
fancy (permessage-deflate, subprotocols) is out of scope.
"""

import base64
import hashlib
import struct


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def handshake_response(sec_websocket_key):
    """Given the value of the client's Sec-WebSocket-Key header,
    return the raw 101 Switching Protocols response as bytes."""
    accept = base64.b64encode(
        hashlib.sha1((sec_websocket_key + GUID).encode("ascii")).digest()
    ).decode("ascii")
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n"
        "\r\n"
    ).encode("ascii")


def read_frame(sock):
    """Read one frame.  Returns (opcode, payload_bytes) or (None, None)
    on a clean close or socket error.

    Handles 2-byte and 8-byte length encodings, and unmasks client
    frames (the only kind the server side sees)."""
    header = _recv_exact(sock, 2)
    if not header:
        return None, None
    b1 = header[0]
    b2 = header[1]
    opcode  = b1 & 0x0F
    masked  = (b2 & 0x80) != 0
    length  = b2 & 0x7F

    if length == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None, None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None, None
        length = struct.unpack(">Q", ext)[0]

    mask = b""
    if masked:
        mask = _recv_exact(sock, 4)
        if not mask:
            return None, None

    payload = b""
    if length:
        payload = _recv_exact(sock, length)
        if payload is None:
            return None, None

    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return opcode, payload


def write_text_frame(sock, text):
    """Write one unmasked text frame (server-to-client)."""
    data = text.encode("utf-8")
    header = bytearray()
    header.append(0x81)  # FIN + text opcode
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(struct.pack(">H", n))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", n))
    sock.sendall(bytes(header) + data)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf
