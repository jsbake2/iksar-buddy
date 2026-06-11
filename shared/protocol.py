"""Wire protocol shared by brain and agent.

Framing: 4-byte unsigned big-endian length prefix, then a UTF-8 JSON body.
This is the *default* transport until the owner's existing socket code lands
(PROJECT.md §10) — if that framing is sound we adopt it and retire this.

Every message is an envelope:
    {"v": 1, "type": <str>, "ts": <float epoch>, "seq": <int>, "data": {...}}

Keep this module dependency-free (stdlib only) so both sides can import it
without dragging in FastAPI / sensor libs.
"""
from __future__ import annotations

import asyncio
import json
import struct
import time
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1
_LEN = struct.Struct(">I")
MAX_FRAME = 8 * 1024 * 1024  # 8 MiB safety cap


# ---- message type constants ------------------------------------------------
# agent -> brain
HELLO = "hello"            # agent identity + capabilities
HEARTBEAT = "heartbeat"    # agent/sensor health
STATE_EVENT = "state_event"  # sensed game state (pixels/ocr/log derived)
ACK = "ack"
LOG = "log"

# brain -> agent
WELCOME = "welcome"
COMMAND = "command"        # action to perform (press/cast/follow/accept/...)
CONFIG = "config"          # push calibration / keybind map
PING = "ping"


@dataclass
class Message:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: float = field(default_factory=time.time)
    v: int = PROTOCOL_VERSION

    def to_bytes(self) -> bytes:
        body = json.dumps(
            {"v": self.v, "type": self.type, "ts": self.ts, "seq": self.seq, "data": self.data},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_FRAME:
            raise ValueError(f"frame too large: {len(body)} bytes")
        return _LEN.pack(len(body)) + body

    @classmethod
    def from_obj(cls, obj: dict[str, Any]) -> "Message":
        return cls(
            type=obj["type"],
            data=obj.get("data", {}) or {},
            seq=int(obj.get("seq", 0)),
            ts=float(obj.get("ts", time.time())),
            v=int(obj.get("v", PROTOCOL_VERSION)),
        )


class ProtocolError(Exception):
    pass


async def read_message(reader: asyncio.StreamReader) -> Message:
    """Read one framed message. Raises asyncio.IncompleteReadError on clean EOF."""
    header = await reader.readexactly(_LEN.size)
    (length,) = _LEN.unpack(header)
    if length == 0 or length > MAX_FRAME:
        raise ProtocolError(f"bad frame length {length}")
    body = await reader.readexactly(length)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"bad json: {e}") from e
    return Message.from_obj(obj)


async def write_message(writer: asyncio.StreamWriter, msg: Message) -> None:
    writer.write(msg.to_bytes())
    await writer.drain()
