"""Constants for the Blackmagic SmartView / SmartScope integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "blackmagic_smartview"
MANUFACTURER: Final = "Blackmagic Design"

DEFAULT_PORT: Final = 9992

# Stored in the config entry so the integration can start while the monitor is off
CONF_CACHED_MODEL: Final = "model"
CONF_CACHED_NAME: Final = "name"
CONF_CACHED_MONITORS: Final = "monitors"
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

# Protocol block headers
BLOCK_PREAMBLE: Final = "PROTOCOL PREAMBLE"
BLOCK_DEVICE: Final = "SMARTVIEW DEVICE"
BLOCK_NETWORK: Final = "NETWORK"
BLOCK_MONITOR_PREFIX: Final = "MONITOR "

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
