"""Constants for the Blackmagic SmartView, SmartScope and Audio Monitor integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "blackmagic_smartview"
MANUFACTURER: Final = "Blackmagic Design"

# Device families. They share one text protocol, on different TCP ports.
DEVICE_TYPE_SMARTVIEW: Final = "smartview"  # SmartView / SmartScope
DEVICE_TYPE_AUDIO_MONITOR: Final = "audio_monitor"  # Audio Monitor 12G (G3)
DEFAULT_PORT: Final = 9992  # SmartView / SmartScope
DEFAULT_PORT_AUDIO_MONITOR: Final = 9996
# Port per device family; "detect" tries them in this order
DEVICE_PORTS: Final[dict[str, int]] = {
    DEVICE_TYPE_SMARTVIEW: DEFAULT_PORT,
    DEVICE_TYPE_AUDIO_MONITOR: DEFAULT_PORT_AUDIO_MONITOR,
}
DETECT_PORTS: Final = (DEFAULT_PORT, DEFAULT_PORT_AUDIO_MONITOR)
# Config flow field: which device to look for
CONF_DEVICE: Final = "device"
DEVICE_DETECT: Final = "detect"

# Stored in the config entry so the integration can start while the monitor is off
CONF_CACHED_MODEL: Final = "model"
CONF_CACHED_NAME: Final = "name"
CONF_CACHED_MONITORS: Final = "monitors"
CONF_DEVICE_TYPE: Final = "device_type"
DEFAULT_NAME: Final = "SmartView"

CONNECT_TIMEOUT: Final = 10.0
CONNECT_ATTEMPTS: Final = 4
CONNECT_RETRY_DELAY: Final = 2.0
COMMAND_TIMEOUT: Final = 3.0
INITIAL_DUMP_TIMEOUT: Final = 10.0
# TCP keepalive: probe after 30 s idle, every 10 s, give up after 3 misses
KEEPALIVE_IDLE: Final = 30
KEEPALIVE_INTERVAL: Final = 10
KEEPALIVE_COUNT: Final = 3
# Drop the connection when sent data stays unacknowledged this long (ms)
TCP_USER_TIMEOUT_MS: Final = 60_000
RECONNECT_MIN_DELAY: Final = 5.0
RECONNECT_MAX_DELAY: Final = 60.0

# Limits on what the device may send. Data from the device is treated as
# untrusted: anything beyond these limits is dropped instead of stored.
READ_LINE_LIMIT: Final = 4096  # bytes per line; longer lines drop the connection
MAX_LINES_PER_BLOCK: Final = 64
MAX_KEY_LENGTH: Final = 64
MAX_VALUE_LENGTH: Final = 255  # Home Assistant's maximum state length
MAX_OTHER_BLOCKS: Final = 16  # unknown blocks kept for diagnostics
MAX_MONITORS: Final = 8  # monitors A to H
# State changes pushed by the device are passed on at most this often
NOTIFY_INTERVAL: Final = 0.1

# Device name (text entity): printable characters only, no line breaks
NAME_MAX_LENGTH: Final = 64
NAME_PATTERN: Final = r"^[^\x00-\x1f\x7f]+$"
# Renaming restarts the device's network; refuse a new rename within this time
RENAME_COOLDOWN: Final = 30.0

# Protocol block headers
BLOCK_PREAMBLE: Final = "PROTOCOL PREAMBLE"
BLOCK_DEVICE: Final = "SMARTVIEW DEVICE"
BLOCK_AUDIOMONITOR_DEVICE: Final = "AUDIOMONITOR DEVICE"
BLOCK_NETWORK: Final = "NETWORK"
BLOCK_MONITOR_PREFIX: Final = "MONITOR "
BLOCK_END_PRELUDE: Final = "END PRELUDE"
BLOCK_AUDIO_METER: Final = "AUDIO METER"
BLOCK_AUDIO_INPUT: Final = "AUDIO INPUT"
BLOCK_AUDIO_OUTPUT: Final = "AUDIO OUTPUT"
BLOCK_ST2110: Final = "ST2110"
# Audio Monitor blocks are always kept, apart from the limit on unknown blocks
AUDIO_MONITOR_BLOCKS: Final = frozenset(
    {BLOCK_AUDIO_METER, BLOCK_AUDIO_INPUT, BLOCK_AUDIO_OUTPUT, BLOCK_ST2110}
)

# Audio Monitor fields. "Gain: Speaker Stereo 114" is stored as
# "Gain Speaker Stereo" = "114", so both gains fit in one block.
FIELD_METER_MODE: Final = "Meter Mode"
FIELD_ROUTING: Final = "Routing"
ROUTING_DESTINATION: Final = "Speaker Stereo"
FIELD_GAIN: Final = "Gain"
GAIN_SPEAKER: Final = "Speaker Stereo"
GAIN_HEADPHONE: Final = "Headphone Stereo"
FIELD_MUTE: Final = "Mute"
FIELD_SOLO: Final = "Solo"
FIELD_AUDIO_DELAY_MS: Final = "Audio delay in ms"
FIELD_SDI_OUTPUT_LEVEL: Final = "SDI Output Level"

# Monitor block fields
FIELD_BRIGHTNESS: Final = "Brightness"
FIELD_CONTRAST: Final = "Contrast"
FIELD_SATURATION: Final = "Saturation"
FIELD_IDENTIFY: Final = "Identify"
FIELD_BORDER: Final = "Border"
FIELD_WIDESCREEN_SD: Final = "WidescreenSD"
FIELD_SCOPE_MODE: Final = "ScopeMode"
FIELD_AUDIO_CHANNEL: Final = "AudioChannel"
FIELD_LUT: Final = "LUT"
FIELD_MONITOR_INPUT: Final = "MonitorInput"

# Model families (matched against the "Model" field of the device block)
MODEL_SMARTSCOPE_DUO_4K: Final = "SmartScope Duo 4K"
MODEL_SMARTVIEW_4K: Final = "SmartView 4K"
MODEL_SMARTVIEW_DUO: Final = "SmartView Duo"
MODEL_SMARTVIEW_HD: Final = "SmartView HD"

# Fields every model supports according to the protocol document
COMMON_FIELDS: Final = frozenset({FIELD_BRIGHTNESS, FIELD_BORDER, FIELD_IDENTIFY})

# Extra fields per model family, used when the device does not report them
# in its state dump (the device report always wins when it does).
MODEL_FIELDS: Final[dict[str, frozenset[str]]] = {
    MODEL_SMARTSCOPE_DUO_4K: frozenset(
        {
            FIELD_CONTRAST,
            FIELD_SATURATION,
            FIELD_WIDESCREEN_SD,
            FIELD_SCOPE_MODE,
            FIELD_AUDIO_CHANNEL,
        }
    ),
    MODEL_SMARTVIEW_4K: frozenset({FIELD_LUT, FIELD_MONITOR_INPUT}),
    MODEL_SMARTVIEW_DUO: frozenset({FIELD_CONTRAST, FIELD_SATURATION, FIELD_WIDESCREEN_SD}),
    MODEL_SMARTVIEW_HD: frozenset({FIELD_CONTRAST, FIELD_SATURATION, FIELD_WIDESCREEN_SD}),
}

# option key (used for translations) -> protocol value
SCOPE_MODES: Final[dict[str, str]] = {
    "picture": "Picture",
    "waveform_luma": "WaveformLuma",
    "vector_100": "Vector100",
    "vector_75": "Vector75",
    "parade_rgb": "ParadeRGB",
    "parade_yuv": "ParadeYUV",
    "histogram": "Histogram",
    "audio_dbfs": "AudioDbfs",
    "audio_dbvu": "AudioDbvu",
}

AUDIO_CHANNELS: Final[dict[str, str]] = {
    "ch_1_2": "0",
    "ch_3_4": "1",
    "ch_5_6": "2",
    "ch_7_8": "3",
    "ch_9_10": "4",
    "ch_11_12": "5",
    "ch_13_14": "6",
    "ch_15_16": "7",
}

BORDER_COLORS: Final[dict[str, str]] = {
    "none": "none",
    "red": "red",
    "green": "green",
    "blue": "blue",
    "white": "white",
}
TALLY_DEFAULT_COLOR: Final = "red"

WIDESCREEN_SD: Final[dict[str, str]] = {
    "on": "ON",
    "off": "OFF",
    "auto": "auto",
}

LUTS: Final[dict[str, str]] = {
    "lut_1": "0",
    "lut_2": "1",
    "disabled": "NONE",
}

MONITOR_INPUTS: Final[dict[str, str]] = {
    "sdi_a": "SDI A",
    "sdi_b": "SDI B",
    "optical": "OPTICAL",
}

# Audio Monitor option key -> protocol value
METER_MODES: Final[dict[str, str]] = {
    "vu_20": "VU (-20dBFS Ref)",
    "vu_18": "VU (-18dBFS Ref)",
    "ppm_ebu_20": "PPM EBU (-20dBFS Ref)",
    "ppm_ebu_18": "PPM EBU (-18dBFS Ref)",
    "ppm_bbc_20": "PPM BBC (-20dBFS Ref)",
    "ppm_bbc_18": "PPM BBC (-18dBFS Ref)",
    "loudness_ebu_9": "Loudness (EBU +9 scale)",
    "loudness_ebu_18": "Loudness (EBU +18 scale)",
}

SOLO_MODES: Final[dict[str, str]] = {
    "off": "Off",
    "left": "Left",
    "right": "Right",
}

# The sources the device lists in its HELP reply (protocol 1.0)
AUDIO_SOURCES: Final[dict[str, str]] = {
    **{f"sdi_{n}_{n + 1}": f"SDI Stereo {n}-{n + 1}" for n in range(1, 64, 2)},
    "xlr_aes_ebu": "XLR AES/EBU Stereo 1-2",
    "xlr_analog": "XLR Analog Stereo",
    "rca": "RCA Stereo",
}
# Added by protocol 1.4 (models with ST 2110), offered only when the device
# reports an ST2110 block
AUDIO_SOURCES_ST2110: Final[dict[str, str]] = {
    f"st2110_{n}_{n + 1}": f"ST2110 Stereo {n}-{n + 1}" for n in range(1, 64, 2)
}
