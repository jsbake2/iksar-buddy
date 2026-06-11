import asyncio

from shared.protocol import Message, read_message, write_message


def test_envelope_roundtrip():
    m = Message("state_event", {"hp": 0.5, "x": [1, 2]}, seq=7)
    raw = m.to_bytes()
    # 4-byte length prefix + body
    assert int.from_bytes(raw[:4], "big") == len(raw) - 4
    back = Message.from_obj(__import__("json").loads(raw[4:]))
    assert back.type == "state_event"
    assert back.seq == 7
    assert back.data["hp"] == 0.5


def test_stream_roundtrip():
    async def go():
        reader = asyncio.StreamReader()
        # fake writer that feeds the reader
        class W:
            def write(self, b): reader.feed_data(b)
            async def drain(self): pass
        await write_message(W(), Message("ping", {"n": 1}))
        msg = await read_message(reader)
        assert msg.type == "ping" and msg.data["n"] == 1

    asyncio.run(go())
