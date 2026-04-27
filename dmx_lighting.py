"""
MicroRave DMX Lighting Module v0.0.1

Controls Enttec Open DMX USB interface to manage Par 50 RGB lights.
Features:
- Automatic Enttec device detection on any USB port
- Color cycling patterns synchronized to music playback
- Non-blocking pattern updates
- Graceful shutdown with lights off
"""

import logging
import threading
import time
from pathlib import Path

# Try to import pyftdi for Enttec Open DMX USB control
try:
    from pyftdi.ftdi import Ftdi
    FTDI_AVAILABLE = True
except ImportError:
    FTDI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Module version
DMX_MODULE_VERSION = "0.0.1"


class DMXLighting:
    """
    DMX512 lighting controller for Enttec Open DMX USB
    
    Controls up to 8 Par 50 lights with RGB color cycling patterns.
    """

    def __init__(self, dmx_config=None, use_mock=False):
        """
        Initialize DMX lighting controller
        
        Args:
            dmx_config: DMX configuration module (loads LIGHTS_TO_CONTROL, patterns, etc.)
            use_mock: Use mock mode for testing without hardware
        """
        self.use_mock = use_mock
        self.config = dmx_config
        
        # DMX state
        self.dmx_channels = [0] * 512  # All 512 DMX channels
        self.ftdi = None
        self.device_found = False
        
        # Pattern control
        self.current_pattern = None
        self.pattern_index = 0
        self.pattern_start_time = 0
        self.is_lights_on = False
        
        # Threading
        self.update_thread = None
        self.thread_running = False
        self.pattern_lock = threading.Lock()
        
        logger.info(f"🔆 DMX Module v{DMX_MODULE_VERSION} Initializing...")
        
        # Initialize FTDI device
        if not use_mock:
            self._init_ftdi_device()
        else:
            logger.info("  (MOCK MODE - No hardware)")
        
        # Start update thread
        self.thread_running = True
        self.update_thread = threading.Thread(
            target=self._update_loop,
            daemon=True
        )
        self.update_thread.start()
        
        logger.info("🔆 DMX system ready")

    def _init_ftdi_device(self):
        """Initialize Enttec Open DMX USB device via FTDI"""
        if not FTDI_AVAILABLE:
            logger.warning("  ⚠ pyftdi not available - using mock mode")
            self.use_mock = True
            return
        
        try:
            self.ftdi = Ftdi()
            
            # Try to find Enttec Open DMX USB device
            # Enttec uses FTDI chip with specific VID/PID
            # Open DMX USB: VID=0x0403 (FTDI), PID=0x6001
            devices = Ftdi.list_devices()
            
            if not devices:
                logger.warning("  ✗ No FTDI devices found")
                self.use_mock = True
                return
            
            logger.info(f"  ✓ Found {len(devices)} FTDI device(s)")
            
            # Open first FTDI device (usually the Open DMX USB)
            device_info = devices[0]
            self.ftdi.open(
                vendor=device_info[0],
                product=device_info[1],
                serial=device_info[2]
            )
            
            # Configure for DMX512 output
            # 250kbaud, 8 bits, 2 stop bits, no parity
            self.ftdi.setbitmode(0xff, Ftdi.BitMode.RESET)
            self.ftdi.setbaudrate(250000)
            self.ftdi.set_line_property(8, 2, 'N')
            
            self.device_found = True
            logger.info(f"  ✓ Connected to Enttec Open DMX USB")
            logger.info(f"    Device: {device_info}")
            
        except Exception as e:
            logger.warning(f"  ✗ Error initializing FTDI: {e}")
            logger.warning("    Using mock mode")
            self.use_mock = True
            self.ftdi = None

    def _startup_test(self):
        """Flash all lights with startup test pattern"""
        logger.info("🔆 Running startup test...")
        
        if self.config is None:
            return
        
        try:
            # Get startup test pattern
            test_pattern = self.config.STARTUP_TEST_PATTERN
            
            for step in test_pattern:
                color = step.get("color", (255, 255, 255))
                duration = step.get("duration", 1.0)
                
                # Set all lights to this color
                for light_num in self.config.LIGHTS_TO_CONTROL:
                    self._set_light_color(light_num, color)
                
                # Send DMX frame
                self._send_dmx_frame()
                logger.info(f"  ✓ Test: RGB{color}")
                
                # Wait for duration
                time.sleep(duration)
            
            # Turn off after test
            self.turn_off()
            logger.info("🔆 Startup test complete")
            
        except Exception as e:
            logger.error(f"Error in startup test: {e}")

    def _set_light_color(self, light_num, color):
        """
        Set a light to RGB color
        
        Args:
            light_num: Light number (1-8)
            color: Tuple (R, G, B) with values 0-255
        """
        if not self.config:
            return
        
        r, g, b = color
        
        # Get channel assignments for this light
        attr_base = f"LIGHT_{light_num}"
        
        try:
            red_ch = getattr(self.config, f"{attr_base}_RED_CHANNEL") - 1  # 0-indexed
            green_ch = getattr(self.config, f"{attr_base}_GREEN_CHANNEL") - 1
            blue_ch = getattr(self.config, f"{attr_base}_BLUE_CHANNEL") - 1
            strobe_ch = getattr(self.config, f"{attr_base}_STROBE_CHANNEL") - 1
            dimmer_ch = getattr(self.config, f"{attr_base}_DIMMER_CHANNEL") - 1
            func_ch = getattr(self.config, f"{attr_base}_FUNCTION_CHANNEL") - 1
            
            # Set RGB values
            self.dmx_channels[red_ch] = r
            self.dmx_channels[green_ch] = g
            self.dmx_channels[blue_ch] = b
            
            # Keep other channels at 0 as requested
            self.dmx_channels[strobe_ch] = 0
            self.dmx_channels[dimmer_ch] = 255  # Full brightness
            self.dmx_channels[func_ch] = 0
            
        except AttributeError as e:
            logger.debug(f"Channel config error for Light {light_num}: {e}")

    def _send_dmx_frame(self):
        """Send current DMX frame to hardware"""
        if self.use_mock:
            return
        
        if not self.device_found or self.ftdi is None:
            return
        
        try:
            # Build DMX frame: Start Code (0x00) + 512 channels
            frame = bytes([0x00] + self.dmx_channels)
            
            # Send to device
            self.ftdi.write_data(frame)
            
        except Exception as e:
            logger.debug(f"Error sending DMX frame: {e}")

    def _update_loop(self):
        """Background thread - updates DMX pattern"""
        while self.thread_running:
            try:
                with self.pattern_lock:
                    # If lights are on and have a pattern, update it
                    if self.is_lights_on and self.current_pattern:
                        self._update_pattern()
                
                # Send DMX frame
                self._send_dmx_frame()
                
                # Update rate (30 Hz default)
                update_rate = 0.033  # ~30 Hz
                if self.config:
                    update_rate = 1.0 / self.config.DMX_UPDATE_RATE
                
                time.sleep(update_rate)
                
            except Exception as e:
                logger.debug(f"Error in DMX update loop: {e}")
                time.sleep(0.1)

    def _update_pattern(self):
        """Update current color based on pattern timing"""
        if not self.current_pattern or not self.current_pattern:
            return
        
        current_time = time.time()
        elapsed = current_time - self.pattern_start_time
        
        # Find which step of pattern we're on
        total_duration = 0
        for i, step in enumerate(self.current_pattern):
            step_duration = step.get("duration", 1.0)
            total_duration += step_duration
            
            if elapsed < total_duration:
                # This is our current step
                color = step.get("color", (0, 0, 0))
                
                # Set all lights to this color
                for light_num in self.config.LIGHTS_TO_CONTROL:
                    self._set_light_color(light_num, color)
                
                return
        
        # Pattern completed - loop it
        self.pattern_start_time = time.time()

    def start_music_pattern(self):
        """Start color cycling pattern for music playback"""
        if not self.config:
            logger.warning("No config - cannot start pattern")
            return
        
        with self.pattern_lock:
            self.is_lights_on = True
            self.current_pattern = self.config.MUSIC_PATTERN
            self.pattern_start_time = time.time()
            self.pattern_index = 0
        
        logger.info("💡 Music pattern started")

    def turn_off(self):
        """Turn off all lights"""
        with self.pattern_lock:
            self.is_lights_on = False
            self.current_pattern = None
            
            # Set all channels to OFF
            if self.config:
                off_color = self.config.OFF_COLOR
                for light_num in self.config.LIGHTS_TO_CONTROL:
                    self._set_light_color(light_num, off_color)
        
        # Send one final frame to turn everything off
        self._send_dmx_frame()
        logger.info("💡 Lights off")

    def cleanup(self):
        """Shutdown and cleanup"""
        logger.info("🔇 DMX cleanup...")
        
        # Stop update thread
        self.thread_running = False
        if self.update_thread and self.update_thread.is_alive():
            self.update_thread.join(timeout=1.0)
        
        # Turn off all lights
        self.turn_off()
        
        # Close FTDI device
        if self.ftdi is not None:
            try:
                self.ftdi.close()
                logger.info("  ✓ FTDI device closed")
            except Exception as e:
                logger.debug(f"Error closing FTDI: {e}")
        
        logger.info("✓ DMX cleanup complete")
