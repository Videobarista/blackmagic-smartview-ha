"""Tests for the protocol client (no Home Assistant dependency).

The client is driven against MockSmartView on 127.0.0.1, and its parser is
fed hostile input directly: the device is treated as untrusted.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
import logging

import pytest

from custom_components.blackmagic_smartview import client as client_module
from custom_components.blackmagic_smartview.client import (
    SmartViewClient,
    SmartViewCommandError,
    SmartViewConnectionError,
    SmartViewValueError,
    mask_mac,
)
from custom_components.blackmagic_smartview.const import (
    MAX_LINES_PER_BLOCK,
    MAX_OTHER_BLOCKS,
    MAX_VALUE_LENGTH,
)

from .mock_smartview import (
    AUDIO_MONITOR_UNIQUE_ID,
    MockSmartView,
    audio_monitor_dump_text,
    dump_text,
)


@pytest.fixture(autouse=True)
def auto_socket_enabled(socket_enabled: None) -> Generator[None]:
    """Allow real local TCP: these tests talk to MockSmartView on 127.0.0.1."""
    yield


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the client's timers so the tests run quickly."""
    monkeypatch.setattr(client_module, "COMMAND_TIMEOUT", 0.3)
    monkeypatch.setattr(client_module, "CONNECT_RETRY_DELAY", 0.01)
    monkeypatch.setattr(client_module, "INITIAL_DUMP_TIMEOUT", 1.0)
    monkeypatch.setattr(client_module, "RECONNECT_MIN_DELAY", 0.05)
    monkeypatch.setattr(client_module, "NOTIFY_INTERVAL", 0.02)


@pytest.fixture
async def device() -> AsyncGenerator[MockSmartView]:
    """A running fake SmartScope Duo 4K."""
    server = MockSmartView()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def client(device: MockSmartView) -> AsyncGenerator[SmartViewClient]:
    """A client for the fake device (not connected yet)."""
    smartview = SmartViewClient(device.host, device.port)
    yield smartview
    await smartview.close()


@pytest.fixture
async def parser() -> AsyncGenerator[SmartViewClient]:
    """A client without a connection, for feeding the parser directly."""
    smartview = SmartViewClient("192.0.2.10", 9992)
    smartview._connected = True  # so state changes are passed on
    yield smartview
    await smartview.close()


async def _wait_for(condition, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


# --------------------------------------------------------------- connecting


async def test_connect_reads_the_state_dump(client: SmartViewClient) -> None:
    """The dump is parsed exactly as the device spells it."""
    await client.connect()

    assert client.connected
    state = client.state
    assert state.protocol_version == "1.3"
    assert state.model == "SmartScope Duo 4K"
    assert state.monitor_count == 2
    assert set(state.monitors) == {"A", "B"}
    assert state.monitors["B"]["ScopeMode"] == "AudioDbfs"
    assert state.network["Current address"] == "192.0.2.10"


async def test_busy_device_is_retried(client: SmartViewClient, device: MockSmartView) -> None:
    """A device that refuses a connection right after a disconnect is retried."""
    device.refuse_connections = 2
    await client.connect()
    assert client.connected
    assert device.connection_count == 3


async def test_permanently_busy_device_fails(
    client: SmartViewClient, device: MockSmartView
) -> None:
    """A device that keeps refusing gives a clear error, not a hang."""
    device.refuse_connections = 100
    with pytest.raises(SmartViewConnectionError, match="keeps closing"):
        await client.connect()
    assert not client.connected


async def test_unreachable_device_fails(device: MockSmartView) -> None:
    """Nothing listening on the port gives a connection error."""
    port = device.port
    await device.stop()
    with pytest.raises(SmartViewConnectionError, match="Cannot connect"):
        await SmartViewClient.probe("127.0.0.1", port)


async def test_probe_returns_state_and_disconnects(device: MockSmartView) -> None:
    """The config flow probe reads the dump and closes its connection."""
    state = await SmartViewClient.probe(device.host, device.port)
    assert state.model == "SmartScope Duo 4K"
    await _wait_for(lambda: not device._writers)


async def test_reconnects_after_device_drop(client: SmartViewClient, device: MockSmartView) -> None:
    """After a device restart the supervisor connects again by itself."""
    changes: list[bool] = []
    client.add_listener(lambda: changes.append(client.connected))
    await client.connect()
    await client.start()

    await device.drop_all()
    await _wait_for(lambda: device.connection_count == 2 and client.connected)

    assert False in changes  # the drop was passed on
    assert changes[-1] is True


async def test_oversized_line_drops_connection_and_reconnects(
    client: SmartViewClient, device: MockSmartView
) -> None:
    """A line beyond the read limit ends that connection instead of growing memory."""
    device.oversized_line_once = True
    await client.connect()
    await client.start()
    await _wait_for(lambda: device.connection_count == 2 and client.connected)
    assert client.state.monitors["A"]["Border"] == "None"


# ----------------------------------------------------------------- commands


async def test_acknowledged_setting_is_stored(
    client: SmartViewClient, device: MockSmartView
) -> None:
    """The device does not echo changes, so an ACK'd value is stored by the client."""
    await client.connect()
    await client.set_monitor("A", {"ScopeMode": "Vector75", "AudioChannel": "3"})

    assert device.received[-1] == ("MONITOR A", {"ScopeMode": "Vector75", "AudioChannel": "3"})
    assert client.state.monitors["A"]["ScopeMode"] == "Vector75"
    assert client.state.monitors["A"]["AudioChannel"] == "3"


async def test_identify_is_not_stored(client: SmartViewClient) -> None:
    """Identify is transient on the device and is not kept as state."""
    await client.connect()
    await client.set_monitor("A", {"Identify": "true"})
    assert client.state.monitors["A"]["Identify"] == "false"


async def test_rejected_setting_raises_and_is_not_stored(client: SmartViewClient) -> None:
    """A NAK raises and leaves the state alone."""
    await client.connect()
    with pytest.raises(SmartViewCommandError):
        await client.set_monitor("A", {"ScopeMode": "Bogus"})
    assert client.state.monitors["A"]["ScopeMode"] == "ParadeRGB"


async def test_error_line_rejects_at_once(client: SmartViewClient) -> None:
    """A '# Error' reply counts as a rejection without waiting for a timeout."""
    await client.connect()
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(SmartViewCommandError):
        await client.send_block("PING", {"Test": "1"})
    assert loop.time() - started < client_module.COMMAND_TIMEOUT


async def test_no_answer_raises_and_is_not_stored(
    client: SmartViewClient, device: MockSmartView
) -> None:
    """Without an ACK the setting is reported as failed, not pretended."""
    await client.connect()
    device.silent = True
    with pytest.raises(SmartViewConnectionError, match="No answer"):
        await client.set_monitor("A", {"Border": "red"})
    assert client.state.monitors["A"]["Border"] == "None"


async def test_command_without_connection_raises() -> None:
    """Sending while disconnected fails cleanly."""
    smartview = SmartViewClient("192.0.2.10", 9992)
    with pytest.raises(SmartViewConnectionError, match="Not connected"):
        await smartview.set_monitor("A", {"Border": "red"})


@pytest.mark.parametrize(
    ("monitor", "fields"),
    [
        ("A", {"Border": "red\nDynamic IP: false"}),
        ("A", {"Border": "red\r"}),
        ("A", {"Border": "red\x00"}),
        ("A", {"Bor\nder": "red"}),
        ("A", {"Border: red\nX": "y"}),
        ("A", {"": "red"}),
        ("A", {"Border": "x" * (MAX_VALUE_LENGTH + 1)}),
        ("A", {}),
        ("Z", {"Border": "red"}),
        ("A\nB", {"Border": "red"}),
    ],
)
async def test_injection_is_refused_before_sending(
    client: SmartViewClient,
    device: MockSmartView,
    monitor: str,
    fields: dict[str, str],
) -> None:
    """Values can never add lines or blocks to a command; nothing reaches the device."""
    await client.connect()
    with pytest.raises(SmartViewValueError):
        await client.set_monitor(monitor, fields)
    assert device.received == []


async def test_device_name_injection_is_refused(
    client: SmartViewClient, device: MockSmartView
) -> None:
    """A rename cannot change network settings through an embedded line break."""
    await client.connect()
    with pytest.raises(SmartViewValueError):
        await client.set_device({"Name": "Studio\nDynamic IP: false\nStatic address: 10.9.9.9"})
    assert device.received == []


# ------------------------------------------------- untrusted data from device


async def test_endless_block_is_dropped(parser: SmartViewClient) -> None:
    """A block that never ends is not kept in memory, nor stored."""
    parser._process_line("MONITOR A:")
    for index in range(10_000):
        parser._process_line(f"Key{index}: value")
    assert len(parser._body) == MAX_LINES_PER_BLOCK
    parser._process_line("")
    assert "A" not in parser.state.monitors


async def test_unknown_blocks_are_limited(parser: SmartViewClient) -> None:
    """Only a few unknown blocks are kept for diagnostics."""
    parser.feed("".join(f"BLOCK {index}:\nKey: value\n\n" for index in range(1_000)))
    assert len(parser.state.other_blocks) == MAX_OTHER_BLOCKS


async def test_only_monitors_a_to_h_are_accepted(parser: SmartViewClient) -> None:
    """Made-up monitor names never create state (or entities)."""
    parser.feed(
        "MONITOR H:\nBorder: red\n\nMONITOR I:\nBorder: red\n\nMONITOR M5:\nBorder: red\n\n",
    )
    assert set(parser.state.monitors) == {"H"}
    assert parser.state.other_blocks == {}


@pytest.mark.parametrize(
    ("reported", "expected"),
    [("2", 2), ("3000000", 8), ("0", None), ("-1", None), ("two", None)],
)
async def test_monitor_count_is_limited(
    parser: SmartViewClient, reported: str, expected: int | None
) -> None:
    """A huge or invalid monitor count is clamped or ignored."""
    parser.feed(f"SMARTVIEW DEVICE:\nMonitors: {reported}\n\n")
    assert parser.state.monitor_count == expected


async def test_values_keys_and_fields_are_limited(parser: SmartViewClient) -> None:
    """Long values are cut, long keys ignored, and new keys per block capped."""
    long_key = "K" * 100
    parser.feed(f"SMARTVIEW DEVICE:\nName: {'N' * 5000}\n{long_key}: x\n\n")
    assert len(parser.state.device["Name"]) == MAX_VALUE_LENGTH
    assert long_key not in parser.state.device

    for start in range(0, 500, 50):
        parser.feed("NETWORK:\n" + "".join(f"K{n}: v\n" for n in range(start, start + 50)) + "\n")
    assert len(parser.state.network) == MAX_LINES_PER_BLOCK


async def test_stray_lines_and_garbage_are_ignored(parser: SmartViewClient) -> None:
    """Lines outside a block, lines without a colon and odd bytes do no harm."""
    parser.feed("garbage\n\x00\x01\nMONITOR A:\nno colon here\nBorder: red\n\n")
    assert parser.state.monitors["A"] == {"Border": "red"}


async def test_device_updates_are_bundled(parser: SmartViewClient) -> None:
    """A flood of updates from the device reaches listeners only a few times."""
    calls: list[int] = []
    parser.add_listener(lambda: calls.append(1))
    parser.feed("".join(f"MONITOR A:\r\nBrightness: {n % 256}\r\n\r\n" for n in range(1_000)))
    assert calls == []
    await asyncio.sleep(client_module.NOTIFY_INTERVAL * 5)
    assert len(calls) == 1
    assert parser.state.monitors["A"]["Brightness"] == str(999 % 256)


async def test_listener_error_does_not_break_the_client(
    parser: SmartViewClient, caplog: pytest.LogCaptureFixture
) -> None:
    """One failing entity never stops other entities or the connection."""
    called: list[int] = []

    def broken() -> None:
        raise ValueError("entity state too long")

    parser.add_listener(broken)
    parser.add_listener(lambda: called.append(1))
    parser._notify(immediate=True)

    assert called == [1]
    assert "Error while updating a SmartView entity" in caplog.text


async def test_removed_listener_is_not_called(parser: SmartViewClient) -> None:
    """A removed listener gets no more updates."""
    called: list[int] = []
    remove = parser.add_listener(lambda: called.append(1))
    remove()
    remove()  # removing twice is harmless
    parser._notify(immediate=True)
    assert called == []


async def test_close_cancels_a_pending_update(parser: SmartViewClient) -> None:
    """Closing never leaves a timer behind."""
    parser.feed("MONITOR A:\nBorder: red\n\n")
    assert parser._flush_handle is not None
    await parser.close()
    assert parser._flush_handle is None


# --------------------------------------------------------------------- privacy


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SmartScopeDuo-7c2e0d000001", "SmartScopeDuo-xxxxxxxxxxxx"),
        ("mac 7C:2E:0D:00:00:01 end", "mac xxxxxxxxxxxx end"),
        ("7c-2e-0d-00-00-01", "xxxxxxxxxxxx"),
        ("1234567890abcd", "1234567890abcd"),  # 14 hex digits: not a MAC
        ("Brightness 128", "Brightness 128"),
    ],
)
def test_mask_mac(text: str, expected: str) -> None:
    """MAC addresses are masked, other text is left alone."""
    assert mask_mac(text) == expected


async def test_debug_log_masks_the_mac_address(
    parser: SmartViewClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The hostname contains the MAC address; debug logs never show it."""
    caplog.set_level(logging.DEBUG, logger=client_module.__name__)
    parser.feed(dump_text())
    assert "SmartScopeDuo-xxxxxxxxxxxx" in caplog.text
    assert "7c2e0d000001" not in caplog.text
    assert parser.state.device["Hostname"] == "SmartScopeDuo-7c2e0d000001"


# --------------------------------------------------------------------- offline


async def test_preload_limits_cached_values() -> None:
    """Cached identity is clamped like live data."""
    smartview = SmartViewClient("192.0.2.10", 9992)
    smartview.preload("SmartScope Duo 4K", "N" * 1000, 50)
    assert smartview.state.monitor_count == 8
    assert len(smartview.state.device["Name"]) == MAX_VALUE_LENGTH
    assert not smartview.connected


# ------------------------------------------------------------- Audio Monitor


@pytest.fixture
async def audio_device() -> AsyncGenerator[MockSmartView]:
    """A running fake Audio Monitor 12G."""
    server = MockSmartView(kind="audio_monitor")
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def audio_client(audio_device: MockSmartView) -> AsyncGenerator[SmartViewClient]:
    """A connected client for the fake Audio Monitor."""
    smartview = SmartViewClient(audio_device.host, audio_device.port)
    await smartview.connect()
    yield smartview
    await smartview.close()


async def test_audio_monitor_is_detected(audio_client: SmartViewClient) -> None:
    """The device type, identity and settings come from the dump; END PRELUDE ends it."""
    state = audio_client.state
    assert audio_client.connected
    assert state.is_audio_monitor
    assert state.prelude_done
    assert state.model == "Blackmagic Audio Monitor 12G"
    assert state.name == "Studio Audio Monitor"
    assert state.unique_id == AUDIO_MONITOR_UNIQUE_ID.lower()
    assert state.monitors == {}
    assert state.monitor_count is None
    assert state.block("AUDIO OUTPUT") == {
        "Gain Speaker Stereo": "114",
        "Gain Headphone Stereo": "0",
        "Mute": "false",
        "Solo": "Off",
    }
    assert state.block("AUDIO INPUT")["Routing"] == "Speaker Stereo XLR AES/EBU Stereo 1-2"
    assert "END PRELUDE" not in state.other_blocks


async def test_audio_monitor_setting_follows_the_device(
    audio_client: SmartViewClient, audio_device: MockSmartView
) -> None:
    """A change is shown as the device reports it, after asking for the block."""
    await audio_client.send_block("AUDIO OUTPUT", {"Gain": "Speaker Stereo 120"})
    await _wait_for(
        lambda: audio_client.state.block("AUDIO OUTPUT")["Gain Speaker Stereo"] == "120"
    )

    assert audio_device.received == [
        ("AUDIO OUTPUT", {"Gain": "Speaker Stereo 120"}),
        ("AUDIO OUTPUT", {}),
    ]
    assert audio_client.state.block("AUDIO OUTPUT")["Gain Headphone Stereo"] == "0"


async def test_audio_monitor_invalid_value_is_not_stored(
    audio_client: SmartViewClient, audio_device: MockSmartView
) -> None:
    """The device ACKs an invalid value but keeps its state; so does the client."""
    await audio_client.send_block("AUDIO OUTPUT", {"Solo": "Middle"})
    await asyncio.sleep(0.1)
    assert audio_client.state.block("AUDIO OUTPUT")["Solo"] == "Off"


async def test_audio_monitor_front_panel_changes_are_followed(
    audio_client: SmartViewClient, audio_device: MockSmartView
) -> None:
    """Volume knob and mute button changes arrive as partial blocks and are merged."""
    await audio_device.push(
        "AUDIO OUTPUT:\nGain: Speaker Stereo 200\n\nAUDIO OUTPUT:\nMute: true\n\n"
    )
    await _wait_for(lambda: audio_client.state.block("AUDIO OUTPUT")["Mute"] == "true")
    output = audio_client.state.block("AUDIO OUTPUT")
    assert output["Gain Speaker Stereo"] == "200"
    assert output["Gain Headphone Stereo"] == "0"


async def test_audio_monitor_rename_uses_label(
    audio_client: SmartViewClient, audio_device: MockSmartView
) -> None:
    """The Audio Monitor's name field is Label, in the AUDIOMONITOR DEVICE block."""
    await audio_client.set_name("Desk Monitor")
    await _wait_for(lambda: audio_client.state.name == "Desk Monitor")
    assert audio_device.received[0] == ("AUDIOMONITOR DEVICE", {"Label": "Desk Monitor"})


async def test_audio_monitor_unknown_block_is_rejected(audio_client: SmartViewClient) -> None:
    """The Audio Monitor answers NAK to a block it does not know."""
    with pytest.raises(SmartViewCommandError):
        await audio_client.send_block("SMARTVIEW DEVICE", {"Name": "x"})


async def test_audio_monitor_gain_lines_are_validated(parser: SmartViewClient) -> None:
    """Gain lines that do not end in a number are ignored."""
    parser.feed(
        "AUDIO OUTPUT:\nGain: Speaker Stereo 50\nGain: nonsense\nGain: Headphone Stereo x\n\n"
    )
    assert parser.state.block("AUDIO OUTPUT") == {"Gain Speaker Stereo": "50"}


async def test_audio_monitor_blocks_are_kept_beyond_the_unknown_limit(
    parser: SmartViewClient,
) -> None:
    """Unknown blocks cannot push out the blocks the Audio Monitor entities need."""
    parser.feed("".join(f"BLOCK {index}:\nKey: value\n\n" for index in range(100)))
    parser.feed(audio_monitor_dump_text())
    assert parser.state.block("AUDIO METER") == {"Meter Mode": "VU (-20dBFS Ref)"}
    assert parser.state.is_audio_monitor


async def test_audio_monitor_unique_id_is_masked_in_debug_log(
    parser: SmartViewClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The Unique ID is the MAC address; debug logs never show it."""
    caplog.set_level(logging.DEBUG, logger=client_module.__name__)
    parser.feed(audio_monitor_dump_text())
    assert AUDIO_MONITOR_UNIQUE_ID not in caplog.text
    assert "Unique ID': 'xxxxxxxxxxxx'" in caplog.text


async def test_preload_audio_monitor() -> None:
    """Cached identity of an Audio Monitor restores type and Label, never monitors."""
    smartview = SmartViewClient("192.0.2.30", 9996)
    smartview.preload("Blackmagic Audio Monitor 12G", "Studio Audio Monitor", 2, "audio_monitor")
    assert smartview.state.is_audio_monitor
    assert smartview.state.name == "Studio Audio Monitor"
    assert smartview.state.monitor_count is None
