"""
MicroRave Configuration
All system settings, GPIO pins, and constants defined in one place.
"""

import os
from pathlib import Path

# ============================================================================
# SYSTEM SETTINGS
# ============================================================================

PROJECT_NAME = "MicroRave"
PROJECT_VERSION = "0.1.0"

# Logging
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE = "/home/MicroRave/logs/microrave.log"

# Network (for Windows Simulator)
SIMULATOR_HOST = "0.0.0.0"  # Listen on all interfaces
SIMULATOR_PORT = 9999
SIMULATOR_ENABLED = True
SIMULATOR_DEBUG = True

# ============================================================================
# GPIO PIN ALLOCATION (Raspberry Pi 5)
# ============================================================================

GPIO_MODE = "BCM"  # Use BCM numbering (vs BOARD physical numbering)

# INPUTS - Button Panel (21 buttons + 1 switch = 22 inputs)
# Microwave Control Layout
BUTTON_PINS = {
    0: "DJ1",           # GPIO 0  - Button 1
    1: "DJ2",           # GPIO 1  - Button 2
    2: "DJ3",           # GPIO 2  - Button 3
    3: "DJ4",           # GPIO 3  - Button 4
    4: "DJ5",           # GPIO 4  - Button 5
    5: "DJ6",           # GPIO 5  - Button 6
    6: "1",             # GPIO 6  - Button 7
    7: "2",             # GPIO 7  - Button 8
    8: "3",             # GPIO 8  - Button 9
    9: "4",             # GPIO 9  - Button 10
    10: "5",            # GPIO 10 - Button 11
    11: "6",            # GPIO 11 - Button 12
    12: "7",            # GPIO 12 - Button 13
    13: "8",            # GPIO 13 - Button 14
    14: "9",            # GPIO 14 - Button 15
    15: "Cancel",       # GPIO 15 - Button 16
    16: "0",            # GPIO 16 - Button 17
    17: "Start",        # GPIO 17 - Button 18
    19: "VolUP",        # GPIO 19 - Button 19 (skip 18 - reserved for WS2812)
    20: "VolDn",        # GPIO 20 - Button 20
    22: "+30s",         # GPIO 22 - Button 21 (skip 21 - reserved for switch)
}

# Single switch (open/closed)
SWITCH_PIN = 21  # GPIO 21
SWITCH_NAME = "OpenClosedSwitch"

# Input Settings
INPUT_DEBOUNCE_MS = 20  # Debounce time in milliseconds
INPUT_PULLUP = True     # Use internal pull-up resistors
INPUT_LOGIC_INVERTED = True  # True if LOW=pressed, HIGH=released

# ============================================================================
# OUTPUTS - GPIO
# ============================================================================

# WS2812 NeoPixel LED Strip (7-segment display)
WS2812_PIN = 18  # GPIO 18 (PWM capable)
WS2812_NUM_LEDS = 56  # 4 characters × 7 segments × 2 LEDs per segment
WS2812_BRIGHTNESS = 100  # 0-255
WS2812_COLOR_ORDER = "RGB"  # or "GRB" depending on LED type
WS2812_FREQUENCY = 800000  # Hz (standard for WS2812)

# Power On/Off Output
POWER_OUTPUT_PIN = 27  # GPIO 27
POWER_OUTPUT_ACTIVE_HIGH = True  # True if HIGH=ON, False if LOW=ON

# ============================================================================
# AUDIO SETTINGS
# ============================================================================

# HDMI Audio Configuration
AUDIO_DEVICE = "hdmi"  # "hdmi", "headphone", "auto"
AUDIO_HDMI_PORT = 0  # Use HDMI 0 (first HDMI port)
AUDIO_SAMPLE_RATE = 48000  # Hz
AUDIO_CHANNELS = 2  # Stereo
AUDIO_VOLUME_DEFAULT = 70  # 0-100
AUDIO_BUFFER_SIZE = 4096

# Audio file paths
AUDIO_PATH = "/home/MicroRave/audio/"
AUDIO_EXTENSIONS = [".wav", ".mp3", ".flac", ".ogg"]

# ============================================================================
# DMX512 SETTINGS
# ============================================================================

# USB DMX Adapter
DMX_USB_DEVICE = "/dev/ttyUSB0"  # Serial port for DMX adapter
DMX_BAUD_RATE = 250000  # DMX512 standard baud rate
DMX_NUM_CHANNELS = 512  # Standard DMX512 has 512 channels
DMX_FRAME_RATE = 30  # Frames per second

# DMX Fixture Configuration
DMX_FIXTURES = {
    # Format: "fixture_name": {"universe": 0, "channels": [1, 2, 3, 4, ...]}
    "stage_lights": {"universe": 0, "channels": list(range(1, 33))},  # 32 channels
    "wash_lights": {"universe": 0, "channels": list(range(33, 65))},  # 32 channels
}

# ============================================================================
# 7-SEGMENT DISPLAY SETTINGS
# ============================================================================

# LED Strip Configuration
LEDS_PER_SEGMENT = 2  # Number of LEDs per segment (configurable)
DISPLAY_BRIGHTNESS = 100  # 0-255
DISPLAY_UPDATE_RATE = 30  # Hz (updates per second)

# 7-segment layout per digit: g→b→a→f→e→d→c
# This order matches the physical LED strip routing through the segments
SEGMENT_ORDER = ['g', 'b', 'a', 'f', 'e', 'd', 'c']  # 7 segments per digit

# Auto-generated: LED indices for each character and segment
# 4 characters × 7 segments × 8 LEDs per segment = 224 total LEDs
def generate_led_map():
    """Generate LED index mapping for 4-character display"""
    led_map = {}
    led_index = 0
    for char_num in range(4):
        led_map[f'char_{char_num}'] = {}
        for segment in SEGMENT_ORDER:
            led_map[f'char_{char_num}'][segment] = list(range(led_index, led_index + LEDS_PER_SEGMENT))
            led_index += LEDS_PER_SEGMENT
    return led_map

DISPLAY_LED_MAP = generate_led_map()

# 7-segment patterns for digits 0-9
# Each pattern is [a, b, c, d, e, f, g] (7 segments, in alphabetical order)
# Segment layout:
#  _a_
# |f| |b|
#  -g-
# |e| |c|
#  _d_
SEGMENT_PATTERNS = {
    '0': [1, 1, 1, 1, 1, 1, 0],  # a,b,c,d,e,f (no g)
    '1': [0, 1, 1, 0, 0, 0, 0],  # b,c
    '2': [1, 1, 0, 1, 1, 0, 1],  # a,b,d,e,g
    '3': [1, 1, 1, 1, 0, 0, 1],  # a,b,c,d,g
    '4': [0, 1, 1, 0, 0, 1, 1],  # b,c,f,g
    '5': [1, 0, 1, 1, 0, 1, 1],  # a,c,d,f,g
    '6': [1, 0, 1, 1, 1, 1, 1],  # a,c,d,e,f,g
    '7': [1, 1, 1, 0, 0, 0, 0],  # a,b,c
    '8': [1, 1, 1, 1, 1, 1, 1],  # all segments
    '9': [1, 1, 1, 1, 0, 1, 1],  # a,b,c,d,f,g
}

# ============================================================================
# COLOR SCHEME
# ============================================================================

# RGB Colors for LED display (tuple format: (R, G, B))
COLOR_WHITE = (255, 255, 255)
COLOR_RED = (255, 0, 0)
COLOR_GREEN = (0, 255, 0)
COLOR_BLUE = (0, 0, 255)
COLOR_CYAN = (0, 255, 255)
COLOR_MAGENTA = (255, 0, 255)
COLOR_YELLOW = (255, 255, 0)
COLOR_ORANGE = (255, 165, 0)
COLOR_OFF = (0, 0, 0)

DISPLAY_COLOR_DEFAULT = COLOR_WHITE
DISPLAY_COLOR_ERROR = COLOR_RED
DISPLAY_COLOR_SUCCESS = COLOR_GREEN

# ============================================================================
# SYSTEM PATHS
# ============================================================================

BASE_PATH = Path("/home/MicroRave")
CONFIG_PATH = BASE_PATH / "config"
AUDIO_PATH = BASE_PATH / "audio"
LOG_PATH = BASE_PATH / "logs"
DATA_PATH = BASE_PATH / "data"

# Create directories if they don't exist
for path in [CONFIG_PATH, AUDIO_PATH, LOG_PATH, DATA_PATH]:
    path.mkdir(parents=True, exist_ok=True)

# ============================================================================
# AUDIO CONFIGURATION
# ============================================================================

# Use relative paths based on script location (more portable)
BASE_DIR = Path(__file__).parent
SOUNDS_DIR = BASE_DIR / "sounds"
MUSIC_DIR = BASE_DIR / "music"
DEFAULT_VOLUME = 50  # 0-100, in 10% increments
BEEP_SOUND = "beep.mp3"
DING_SOUND = "ding.mp3"

# Easter egg times and their corresponding sound files
EASTER_EGG_TIMES = {
    '00:07': '007.mp3',
    '00:42': '042.mp3',
    '00:67': '067.mp3',
    '00:69': '069.mp3',
    '04:20': '420.mp3',
    '06:66': '666.mp3'
}

# ============================================================================
# TESTING & DEBUGGING
# ============================================================================

# Use mock GPIO for testing on non-Pi systems
USE_MOCK_GPIO = False  # Set to True to test on Windows/Linux without Pi

# Pin test configuration
PIN_TEST_SEQUENCE = [
    "Button_1", "Button_2", "Button_3", "Switch", "PowerOutput", "Display"
]

print(f"✓ Config loaded: {PROJECT_NAME} v{PROJECT_VERSION}")
