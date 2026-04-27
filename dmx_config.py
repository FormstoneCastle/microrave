"""
MicroRave DMX Lighting Configuration v0.0.1

Defines DMX channel assignments and color patterns for the Par 50 lights.
Each light uses 6 channels in DMX mode.
"""

# DMX Configuration Version
DMX_CONFIG_VERSION = "0.0.1"

# ============================================================================
# DMX CHANNEL ASSIGNMENTS
# ============================================================================

# Light 1 - Channels 1-6 (6-channel mode)
LIGHT_1_START_CHANNEL = 1
LIGHT_1_RED_CHANNEL = 1
LIGHT_1_GREEN_CHANNEL = 2
LIGHT_1_BLUE_CHANNEL = 3
LIGHT_1_STROBE_CHANNEL = 4
LIGHT_1_DIMMER_CHANNEL = 5
LIGHT_1_FUNCTION_CHANNEL = 6

# Light 2 - Channels 7-12
LIGHT_2_START_CHANNEL = 7
LIGHT_2_RED_CHANNEL = 7
LIGHT_2_GREEN_CHANNEL = 8
LIGHT_2_BLUE_CHANNEL = 9
LIGHT_2_STROBE_CHANNEL = 10
LIGHT_2_DIMMER_CHANNEL = 11
LIGHT_2_FUNCTION_CHANNEL = 12

# Light 3 - Channels 13-18
LIGHT_3_START_CHANNEL = 13
LIGHT_3_RED_CHANNEL = 13
LIGHT_3_GREEN_CHANNEL = 14
LIGHT_3_BLUE_CHANNEL = 15
LIGHT_3_STROBE_CHANNEL = 16
LIGHT_3_DIMMER_CHANNEL = 17
LIGHT_3_FUNCTION_CHANNEL = 18

# Light 4 - Channels 19-24
LIGHT_4_START_CHANNEL = 19
LIGHT_4_RED_CHANNEL = 19
LIGHT_4_GREEN_CHANNEL = 20
LIGHT_4_BLUE_CHANNEL = 21
LIGHT_4_STROBE_CHANNEL = 22
LIGHT_4_DIMMER_CHANNEL = 23
LIGHT_4_FUNCTION_CHANNEL = 24

# Light 5 - Channels 25-30
LIGHT_5_START_CHANNEL = 25
LIGHT_5_RED_CHANNEL = 25
LIGHT_5_GREEN_CHANNEL = 26
LIGHT_5_BLUE_CHANNEL = 27
LIGHT_5_STROBE_CHANNEL = 28
LIGHT_5_DIMMER_CHANNEL = 29
LIGHT_5_FUNCTION_CHANNEL = 30

# Light 6 - Channels 31-36
LIGHT_6_START_CHANNEL = 31
LIGHT_6_RED_CHANNEL = 31
LIGHT_6_GREEN_CHANNEL = 32
LIGHT_6_BLUE_CHANNEL = 33
LIGHT_6_STROBE_CHANNEL = 34
LIGHT_6_DIMMER_CHANNEL = 35
LIGHT_6_FUNCTION_CHANNEL = 36

# Light 7 - Channels 37-42
LIGHT_7_START_CHANNEL = 37
LIGHT_7_RED_CHANNEL = 37
LIGHT_7_GREEN_CHANNEL = 38
LIGHT_7_BLUE_CHANNEL = 39
LIGHT_7_STROBE_CHANNEL = 40
LIGHT_7_DIMMER_CHANNEL = 41
LIGHT_7_FUNCTION_CHANNEL = 42

# Light 8 - Channels 43-48
LIGHT_8_START_CHANNEL = 43
LIGHT_8_RED_CHANNEL = 43
LIGHT_8_GREEN_CHANNEL = 44
LIGHT_8_BLUE_CHANNEL = 45
LIGHT_8_STROBE_CHANNEL = 46
LIGHT_8_DIMMER_CHANNEL = 47
LIGHT_8_FUNCTION_CHANNEL = 48

# ============================================================================
# COLOR PATTERNS
# ============================================================================

# Startup test pattern - all colors full brightness
STARTUP_TEST_PATTERN = [
    {"color": (255, 255, 255), "duration": 1.0},  # White - 1 second
]

# Main color cycling pattern for music playback
# Blue → Red → Green (each for 5 seconds)
MUSIC_PATTERN = [
    {"color": (0, 0, 255), "duration": 5.0},      # Blue - 5 seconds
    {"color": (255, 0, 0), "duration": 5.0},      # Red - 5 seconds
    {"color": (0, 255, 0), "duration": 5.0},      # Green - 5 seconds
]

# Off state
OFF_COLOR = (0, 0, 0)

# ============================================================================
# GENERAL DMX SETTINGS
# ============================================================================

# All lights use same pattern (future: per-light patterns)
LIGHTS_TO_CONTROL = [1, 2, 3, 4, 5, 6, 7, 8]

# DMX frame update rate (Hz)
DMX_UPDATE_RATE = 30

# USB device detection
# Auto-detect Enttec Open DMX USB device
AUTO_DETECT_DEVICE = True

# Manual device override (set to None for auto-detect)
MANUAL_DEVICE_PATH = None

# ============================================================================
# CHANNEL VALUE DEFAULTS
# ============================================================================

# Default values for all 512 channels
DEFAULT_CHANNEL_VALUE = 0

# Strobe channel default (channels 4 per light)
DEFAULT_STROBE_VALUE = 0

# Dimmer channel default (channels 5 per light)
# Use 255 for full brightness, 0 for off
DEFAULT_DIMMER_VALUE = 255

# Function channel default (channels 6 per light)
DEFAULT_FUNCTION_VALUE = 0
