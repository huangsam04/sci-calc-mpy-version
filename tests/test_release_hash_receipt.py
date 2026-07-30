import ast

import pytest

from tools import release_device_mpremote as mpadapter
from tools.release_protocol import HashReceipt


class _ReceiptAdapter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def exec_limited(self, code, max_output_bytes, **params):
        self.calls.append((code, max_output_bytes, params))
        return self.response


def test_stream_hash_receipt_decodes_only_the_fixed_wire_format():
    adapter = _ReceiptAdapter("H003004\r\n")
    pairs = (
        ("/boot.py", "0" * 64),
        ("/bootenv.py", "1" * 64),
        ("/main.py", "2" * 64),
    )

    receipt = mpadapter.stream_hash_receipt(adapter, pairs)

    assert receipt == HashReceipt(0x003, 0x004, False)
    assert len(adapter.calls) == 1
    code, limit, params = adapter.calls[0]
    assert code is mpadapter.HASH_PATHS_CODE
    assert limit == 16
    assert ast.literal_eval(params["pairs"]) == pairs


@pytest.mark.parametrize("response", (
    "OK",
    "H000000 trailing",
    "H001001",
    "H008000",
    "E001000",
    "X000000",
))
def test_stream_hash_receipt_rejects_malformed_or_impossible_evidence(
        response):
    with pytest.raises(ValueError, match=r"hash (?:fault )?receipt"):
        mpadapter.stream_hash_receipt(
            _ReceiptAdapter(response),
            (("/boot.py", "0" * 64),),
        )


def test_stream_hash_receipt_uses_repr_safe_path_encoding():
    adapter = _ReceiptAdapter("H001000")
    pairs = (("/sd/quoted'name.py", "a" * 64),)

    mpadapter.stream_hash_receipt(adapter, pairs)

    assert ast.literal_eval(adapter.calls[0][2]["pairs"]) == pairs


def test_stream_hash_receipt_stops_pulling_a_runaway_iterator_early():
    pulled = []

    def pairs():
        for index in range(50):
            pulled.append(index)
            yield ("/sd/file" + str(index) + ".py", "0" * 64)

    with pytest.raises(ValueError, match="hash receipt requires"):
        mpadapter.stream_hash_receipt(_ReceiptAdapter("H000000"), pairs())

    assert len(pulled) <= mpadapter.HASH_RECEIPT_MAX_PAIRS + 1


def test_stream_hash_receipt_bounds_each_query_path():
    digest = "0" * 64
    longest = "/" + "a" * (mpadapter.HASH_PATH_MAX_CHARS - 1)
    adapter = _ReceiptAdapter("H001000")

    assert mpadapter.stream_hash_receipt(
        adapter, ((longest, digest),)).matched_mask == 1

    with pytest.raises(ValueError, match="invalid hash receipt pair"):
        mpadapter.stream_hash_receipt(
            _ReceiptAdapter("H001000"), ((longest + "a", digest),))
    with pytest.raises(ValueError, match="invalid hash receipt pair"):
        mpadapter.stream_hash_receipt(
            _ReceiptAdapter("H001000"), (("/sd/café.py", digest),))


def test_stream_hash_receipt_refuses_an_oversized_repl_query():
    digest = "a" * 64
    # Mixing both quote characters makes repr escape every apostrophe, so
    # ten maximum-length paths inflate past the fixed query byte budget.
    path = "/" + '"' + "'" * (mpadapter.HASH_PATH_MAX_CHARS - 2)
    assert len(path) == mpadapter.HASH_PATH_MAX_CHARS
    pairs = tuple((path, digest) for _ in range(10))
    formatted = mpadapter.HASH_PATHS_CODE.format(pairs=repr(pairs))
    assert len(formatted.encode("utf-8")) > mpadapter.HASH_QUERY_MAX_BYTES
    adapter = _ReceiptAdapter("H3ff000")

    with pytest.raises(ValueError, match="hash receipt query is too large"):
        mpadapter.stream_hash_receipt(adapter, pairs)

    assert adapter.calls == []


class _UnboundedOnlyAdapter:
    def __init__(self):
        self.calls = []

    def exec(self, code, **params):
        self.calls.append(code)
        return "H001000"


def test_stream_hash_receipt_requires_a_bounded_exec_transport():
    device = _UnboundedOnlyAdapter()

    with pytest.raises(ValueError, match="device must provide bounded exec"):
        mpadapter.stream_hash_receipt(device, (("/boot.py", "0" * 64),))

    assert device.calls == []


class _RawTransport:
    def __init__(self, chunks):
        self.chunks = tuple(chunks)

    def exec(self, _code, data_consumer):
        for chunk in self.chunks:
            data_consumer(chunk)


class _MemoryErrorTransport:
    def exec(self, _code, data_consumer):
        raise MemoryError("transport allocation failed")


def test_mpremote_limited_exec_keeps_a_small_success_response():
    device = mpadapter.MpremoteDevice("unused")
    device._transport = _RawTransport((b"H000000\r\n", b"\x04"))

    assert device.exec_limited("pass", 16) == "H000000\r\n"


def test_mpremote_limited_exec_fails_before_retaining_an_oversized_response():
    device = mpadapter.MpremoteDevice("unused")
    device._transport = _RawTransport((b"H000000", b"x" * 10))

    with pytest.raises(OSError, match="byte limit"):
        device.exec_limited("pass", 16)


def test_mpremote_limited_exec_preserves_primary_memory_error():
    device = mpadapter.MpremoteDevice("unused")
    device._transport = _MemoryErrorTransport()

    with pytest.raises(MemoryError, match="transport allocation failed"):
        device.exec_limited("pass", 16)

    assert "except MemoryError:\n    raise\nexcept Exception" in (
        mpadapter.HASH_PATHS_CODE)
