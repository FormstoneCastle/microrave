"""
MicroRave DMX512 Output Module
Controls stage lighting via USB DMX adapter.

Hardware:
- USB DMX adapter (Enttec or clone)
- Connected to one of the 4 USB ports
- Supports 512 DMX channels

Features:
- Individual channel control
- Fixture-based control
- Smooth fade/transition effects
- Mock mode for testing without hardware
"""

import time
import logging
from typing import Dict, Optional, List
import os

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("⚠ PySerial not available - using mock DMX512")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DMX512:
    """
    Controls DMX512 lighting via USB adapter.
    
    DMX512 Protocol:
    - 512 channels per universe
    - 250 kbaud serial communication
    - Each channel: 0-255 (0% to 100%)
    """
    
    # DMX protocol constants
    DMX_BAUD = 250000
    DMX_CHANNELS = 512
    DMX_START_CODE = 0x00
    DMX_BREAK_TIME = 0.000092  # 92 microseconds
    DMX_MAB_TIME = 0.000012    # 12 microseconds
    
    def __init__(self, serial_port: str = "/dev/ttyUSB0", use_mock: bool = False):
        """
        Initialize DMX512 controller.
        
        Args:
            serial_port: Serial port for USB DMX adapter
            use_mock: Use mock mode (for testing)
        """
        self.serial_port = serial_port
        self.use_mock = use_mock or not SERIAL_AVAILABLE
        
        # Channel state (0-255 for each of 512 channels)
        self.channels = [0] * self.DMX_CHANNELS
        
        # Serial connection
        self.ser = None
        
        # Transition state
        self.is_transitioning = False
        self.transition_frames = 0
        self.transition_targets = [0] * self.DMX_CHANNELS
        
        self._init_hardware()
        logger.info(f"✓ DMX512 initialized ({self.DMX_CHANNELS} channels)")
    
    def _init_hardware(self):
        """Initialize serial connection to DMX adapter"""
        if self.use_mock:
            logger.info("🔧 Using mock DMX512 mode")
            return
        
        logger.info(f"🔌 Initializing DMX512 on {self.serial_port}")
        
        try:
            # Check if port exists
            if not os.path.exists(self.serial_port):
                logger.warning(f"⚠ Serial port {self.serial_port} not found")
                logger.info("  Falling back to mock mode")
                self.use_mock = True
                return
            
            # Open serial connection
            self.ser = serial.Serial(
                port=self.serial_port,
                baudrate=self.DMX_BAUD,
                bytesize=serial.EIGHTBITS,
                stopbits=serial.STOPBITS_TWO,
                parity=serial.PARITY_NONE,
                timeout=0
            )
            logger.info(f"✓ DMX512 serial connection established")
            
        except Exception as e:
            logger.error(f"✗ Failed to open serial port: {e}")
            logger.info("  Falling back to mock mode")
            self.use_mock = True
    
    def set_channel(self, channel: int, value: int):
        """
        Set individual channel value.
        
        Args:
            channel: Channel number (0-511)
            value: Channel value (0-255)
        """
        if channel < 0 or channel >= self.DMX_CHANNELS:
            logger.warning(f"✗ Invalid channel: {channel}")
            return
        
        value = max(0, min(255, value))
        self.channels[channel] = value
        self._send_dmx()
    
    def set_channels(self, channels_dict: Dict[int, int]):
        """
        Set multiple channels at once.
        
        Args:
            channels_dict: Dictionary {channel: value, ...}
        """
        for channel, value in channels_dict.items():
            if 0 <= channel < self.DMX_CHANNELS:
                self.channels[channel] = max(0, min(255, value))
        
        self._send_dmx()
    
    def set_fixture(self, fixture_name: str, channel_values: Dict[int, int]):
        """
        Control a fixture using relative channel offsets.
        
        Args:
            fixture_name: Name of fixture (for logging)
            channel_values: Dict of {offset: value, ...}
        
        Example:
            # Fixture at channel 1 with RGB channels
            dmx.set_fixture("stage_light_1", {0: 255, 1: 128, 2: 64})
        """
        for offset, value in channel_values.items():
            channel = offset
            if 0 <= channel < self.DMX_CHANNELS:
                self.channels[channel] = max(0, min(255, value))
        
        logger.debug(f"Fixture '{fixture_name}' updated")
        self._send_dmx()
    
    def fade_channel(self, channel: int, target_value: int, duration_sec: float = 1.0):
        """
        Smoothly fade a channel to target value.
        
        Args:
            channel: Channel number (0-511)
            target_value: Target value (0-255)
            duration_sec: Duration of fade in seconds
        """
        if channel < 0 or channel >= self.DMX_CHANNELS:
            logger.warning(f"✗ Invalid channel: {channel}")
            return
        
        target_value = max(0, min(255, target_value))
        current_value = self.channels[channel]
        
        if current_value == target_value:
            return  # Already at target
        
        steps = max(1, int(duration_sec * 30))  # 30 FPS
        step_size = (target_value - current_value) / steps
        
        for step in range(steps):
            new_value = int(current_value + step_size * step)
            self.set_channel(channel, new_value)
            time.sleep(duration_sec / steps)
        
        # Ensure we end exactly at target
        self.set_channel(channel, target_value)
    
    def fade_all(self, target_values: Dict[int, int], duration_sec: float = 1.0):
        """
        Smoothly fade multiple channels.
        
        Args:
            target_values: Dict {channel: target_value, ...}
            duration_sec: Duration of fade in seconds
        """
        steps = max(1, int(duration_sec * 30))  # 30 FPS
        
        # Calculate step sizes for each channel
        step_sizes = {}
        for channel, target in target_values.items():
            if 0 <= channel < self.DMX_CHANNELS:
                current = self.channels[channel]
                step_sizes[channel] = (target - current) / steps
        
        # Execute fade
        for step in range(steps):
            new_values = {}
            for channel, step_size in step_sizes.items():
                current = self.channels[channel]
                new_value = int(current + step_size)
                new_values[channel] = new_value
            
            self.set_channels(new_values)
            time.sleep(duration_sec / steps)
        
        # Ensure we end exactly at targets
        self.set_channels(target_values)
    
    def all_off(self):
        """Turn off all channels"""
        self.channels = [0] * self.DMX_CHANNELS
        self._send_dmx()
        logger.info("All DMX channels OFF")
    
    def all_on(self, brightness: int = 255):
        """Turn on all channels to full brightness"""
        self.channels = [brightness] * self.DMX_CHANNELS
        self._send_dmx()
        logger.info(f"All DMX channels ON (brightness: {brightness})")
    
    def get_channel(self, channel: int) -> int:
        """Get current channel value"""
        if 0 <= channel < self.DMX_CHANNELS:
            return self.channels[channel]
        return 0
    
    def get_all_channels(self) -> List[int]:
        """Get all channel values"""
        return self.channels.copy()
    
    def _send_dmx(self):
        """Send DMX data via serial port"""
        if self.use_mock:
            # Mock mode: just log channel updates
            active_channels = [(i, v) for i, v in enumerate(self.channels) if v > 0]
            if active_channels:
                logger.debug(f"DMX Send: {len(active_channels)} active channels")
            return
        
        if not self.ser or not self.ser.is_open:
            logger.warning("✗ Serial port not open")
            return
        
        try:
            # Build DMX packet
            # Format: Break signal + Start Code + 512 channel bytes
            
            # Send BREAK (low for 92+ microseconds)
            # Note: This is handled at serial level in real DMX adapters
            
            # Send Data (start code + channels)
            dmx_packet = bytes([self.DMX_START_CODE] + self.channels)
            self.ser.write(dmx_packet)
            
        except Exception as e:
            logger.error(f"✗ DMX send error: {e}")
    
    def cleanup(self):
        """Clean up serial connection"""
        self.all_off()
        
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                logger.info("✓ DMX512 cleanup complete")
            except Exception as e:
                logger.error(f"✗ Error during cleanup: {e}")


# ============================================================================
# TESTING & STANDALONE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Standalone test script for DMX512 output.
    
    Usage:
        python3 dmx512_output.py
    """
    
    print("\n" + "="*60)
    print("MICRORAVE - DMX512 OUTPUT TEST")
    print("="*60 + "\n")
    
    # Create DMX controller with mock mode
    dmx = DMX512(use_mock=True)
    
    print("🧪 Testing DMX512 functionality...\n")
    
    # Test 1: Set individual channels
    print("Test 1: Set individual channels")
    dmx.set_channel(0, 255)  # Channel 1 full brightness
    print(f"  Channel 0: {dmx.get_channel(0)}")
    dmx.set_channel(1, 128)  # Channel 2 half brightness
    print(f"  Channel 1: {dmx.get_channel(1)}")
    dmx.set_channel(2, 64)   # Channel 3 quarter brightness
    print(f"  Channel 2: {dmx.get_channel(2)}")
    
    # Test 2: Set multiple channels
    print("\nTest 2: Set multiple channels")
    dmx.set_channels({0: 200, 1: 150, 2: 100, 3: 50})
    print(f"  Channels 0-3: {dmx.get_all_channels()[:4]}")
    
    # Test 3: Control a fixture (RGB light)
    print("\nTest 3: Control RGB fixture")
    dmx.set_fixture("stage_light_1", {0: 255, 1: 0, 2: 0})  # Red
    print(f"  Set to RED: R={dmx.get_channel(0)}, G={dmx.get_channel(1)}, B={dmx.get_channel(2)}")
    
    dmx.set_fixture("stage_light_1", {0: 0, 1: 255, 2: 0})  # Green
    print(f"  Set to GREEN: R={dmx.get_channel(0)}, G={dmx.get_channel(1)}, B={dmx.get_channel(2)}")
    
    dmx.set_fixture("stage_light_1", {0: 0, 1: 0, 2: 255})  # Blue
    print(f"  Set to BLUE: R={dmx.get_channel(0)}, G={dmx.get_channel(1)}, B={dmx.get_channel(2)}")
    
    # Test 4: Fade channel
    print("\nTest 4: Fade channel")
    print("  Fading channel 0 from 0 to 255 over 1 second...")
    dmx.set_channel(0, 0)
    dmx.fade_channel(0, 255, duration_sec=1.0)
    print(f"  Final value: {dmx.get_channel(0)}")
    
    # Test 5: All off
    print("\nTest 5: All channels OFF")
    dmx.all_off()
    print(f"  Channel 0: {dmx.get_channel(0)}")
    print(f"  Channel 1: {dmx.get_channel(1)}")
    
    # Test 6: All on
    print("\nTest 6: All channels ON (full brightness)")
    dmx.all_on(200)
    print(f"  Sample channels: {dmx.get_all_channels()[:5]}")
    
    print("\n✓ DMX512 output test complete!")
    dmx.cleanup()
