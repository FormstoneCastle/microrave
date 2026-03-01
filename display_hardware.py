#!/usr/bin/env python3
"""
MicroRave 7-Segment Display Hardware Control (v0.0.1)

Controls WS2812 RGB LED strip via GPIO using rpi_ws281x library.
Includes mock GPIO support for testing on Windows.

Usage:
    from display_7seg import Display7Segment
    from display_hardware import DisplayHardware
    
    display = Display7Segment()
    hardware = DisplayHardware(use_mock=True)  # True for Windows, False for Pi
    
    result = display.display_text("1234")
    hardware.set_leds(result['led_indices'], color=(255, 255, 255))
    hardware.show()
"""

MODULE_VERSION = "0.0.1"

import sys
import time
from typing import List, Tuple, Optional


# ============================================================================
# MOCK GPIO FOR TESTING (Windows/Linux without Pi)
# ============================================================================

class MockNeoPixel:
    """Mock WS2812 LED strip for testing without hardware."""
    
    def __init__(self, pin, num_leds, brightness=255, auto_write=True, pixel_order="RGB"):
        """
        Initialize mock LED strip.
        
        Args:
            pin: GPIO pin (unused in mock)
            num_leds: Number of LEDs
            brightness: Brightness 0-255
            auto_write: Whether to auto-update (unused in mock)
            pixel_order: Pixel color order (unused in mock)
        """
        self.num_leds = num_leds
        self.brightness = brightness
        self.pixel_order = pixel_order
        self.leds = [(0, 0, 0) for _ in range(num_leds)]
        self.show_count = 0
    
    def __setitem__(self, index, color):
        """Set LED color at index."""
        if isinstance(index, int):
            self.leds[index] = color
        elif isinstance(index, slice):
            # Handle slice assignment
            start, stop, step = index.indices(self.num_leds)
            for i in range(start, stop, step or 1):
                self.leds[i] = color
    
    def __getitem__(self, index):
        """Get LED color at index."""
        return self.leds[index]
    
    def show(self):
        """Mock show - just increment counter."""
        self.show_count += 1
    
    def fill(self, color):
        """Fill all LEDs with color."""
        self.leds = [color for _ in range(self.num_leds)]


# ============================================================================
# HARDWARE CONTROL
# ============================================================================

class DisplayHardware:
    """
    Control WS2812 RGB LED strip for 7-segment display.
    
    Supports both real hardware (Pi) and mock GPIO (Windows/testing).
    """
    
    def __init__(self, config=None, use_mock=False):
        """
        Initialize hardware controller.
        
        Args:
            config: Config module (contains WS2812_PIN, WS2812_NUM_LEDS, etc.)
                   If None, imports config automatically.
            use_mock: If True, use mock GPIO. If False, use real rpi_ws281x.
        """
        if config is None:
            try:
                import config
            except ImportError:
                print("ERROR: config.py not found.")
                sys.exit(1)
        
        self.config = config
        self.use_mock = use_mock
        
        # Get config values
        self.pin = config.WS2812_PIN
        self.num_leds = config.WS2812_NUM_LEDS
        self.brightness = config.WS2812_BRIGHTNESS
        self.color_order = config.WS2812_COLOR_ORDER
        
        # Initialize LED strip
        if use_mock:
            self._init_mock()
        else:
            self._init_hardware()
        
        # Current state
        self.current_color = (255, 255, 255)  # Default: white
        self.is_initialized = True
    
    
    def _init_mock(self):
        """Initialize mock LED strip."""
        print(f"[MOCK GPIO] Initializing WS2812 mock strip")
        print(f"  GPIO Pin: {self.pin}")
        print(f"  Num LEDs: {self.num_leds}")
        print(f"  Brightness: {self.brightness}/255")
        print(f"  Color Order: {self.color_order}")
        
        self.strip = MockNeoPixel(
            self.pin,
            self.num_leds,
            brightness=self.brightness,
            pixel_order=self.color_order
        )
    
    
    def _init_hardware(self):
        """Initialize real WS2812 hardware via rpi_ws281x."""
        try:
            # Try to import rpi_ws281x
            from neopixel import NeoPixel
            
            print(f"[HARDWARE] Initializing WS2812 LED strip")
            print(f"  GPIO Pin: {self.pin}")
            print(f"  Num LEDs: {self.num_leds}")
            print(f"  Brightness: {self.brightness}/255")
            print(f"  Color Order: {self.color_order}")
            
            # Initialize the strip
            self.strip = NeoPixel(
                self.pin,
                self.num_leds,
                brightness=self.brightness / 255.0,  # NeoPixel expects 0-1
                auto_write=False,  # Manual show() calls
                pixel_order=self.color_order
            )
            
            # Clear all LEDs on startup
            self.clear()
            self.show()
            
        except ImportError:
            print("ERROR: rpi_ws281x library not found.")
            print("Install with: pip install rpi-ws281x")
            print("Falling back to mock GPIO...")
            self.use_mock = True
            self._init_mock()
        except Exception as e:
            print(f"ERROR initializing WS2812: {e}")
            print("Falling back to mock GPIO...")
            self.use_mock = True
            self._init_mock()
    
    
    def set_leds(self, indices: List[int], color: Tuple[int, int, int] = (255, 255, 255)) -> bool:
        """
        Turn on specific LEDs with a color.
        
        Args:
            indices (List[int]): LED indices to turn ON
            color (Tuple): RGB color tuple (R, G, B), each 0-255
        
        Returns:
            bool: True if successful
        
        Example:
            hardware.set_leds([0, 1, 2, 3], color=(255, 255, 255))  # White
            hardware.set_leds([0, 1, 2, 3], color=(255, 0, 0))      # Red
        """
        try:
            # Validate color values
            if not all(0 <= c <= 255 for c in color):
                print(f"WARNING: Color values out of range: {color}")
                color = tuple(max(0, min(255, c)) for c in color)
            
            # First, clear all LEDs
            self.clear()
            
            # Set specified LEDs to color
            for idx in indices:
                if 0 <= idx < self.num_leds:
                    self.strip[idx] = color
                else:
                    print(f"WARNING: LED index {idx} out of range (0-{self.num_leds-1})")
            
            self.current_color = color
            return True
        
        except Exception as e:
            print(f"ERROR in set_leds: {e}")
            return False
    
    
    def set_brightness(self, value: int) -> bool:
        """
        Set global brightness level.
        
        Args:
            value (int): Brightness 0-255
        
        Returns:
            bool: True if successful
        
        Note: On real hardware, this requires re-initializing the strip.
              On mock, it's instant.
        """
        try:
            if not 0 <= value <= 255:
                print(f"WARNING: Brightness out of range: {value}")
                value = max(0, min(255, value))
            
            self.brightness = value
            
            if not self.use_mock:
                # For real hardware, update the brightness
                if hasattr(self.strip, 'brightness'):
                    self.strip.brightness = value / 255.0
            
            return True
        
        except Exception as e:
            print(f"ERROR in set_brightness: {e}")
            return False
    
    
    def clear(self) -> bool:
        """
        Turn off all LEDs.
        
        Returns:
            bool: True if successful
        """
        try:
            for i in range(self.num_leds):
                self.strip[i] = (0, 0, 0)
            return True
        
        except Exception as e:
            print(f"ERROR in clear: {e}")
            return False
    
    
    def show(self) -> bool:
        """
        Push changes to physical LED strip.
        
        Must be called after set_leds() to update the display.
        
        Returns:
            bool: True if successful
        """
        try:
            self.strip.show()
            return True
        
        except Exception as e:
            print(f"ERROR in show: {e}")
            return False
    
    
    def set_all(self, color: Tuple[int, int, int] = (255, 255, 255)) -> bool:
        """
        Turn on all LEDs with a color.
        
        Args:
            color (Tuple): RGB color tuple
        
        Returns:
            bool: True if successful
        """
        try:
            for i in range(self.num_leds):
                self.strip[i] = color
            self.current_color = color
            return True
        
        except Exception as e:
            print(f"ERROR in set_all: {e}")
            return False
    
    
    def display_text(self, text: str, color: Tuple[int, int, int] = (255, 255, 255)) -> bool:
        """
        Convenience method: Display text on the LED strip.
        
        This method ties together display_7seg and display_hardware:
        1. Convert text to LED indices (display_7seg)
        2. Set those LEDs to color (display_hardware)
        3. Show the result (push to strip)
        
        Args:
            text (str): Text to display (e.g., "1234", "12:34")
            color (Tuple): RGB color
        
        Returns:
            bool: True if successful
        """
        try:
            from display_7seg import Display7Segment
            
            # Convert text to LED indices
            display = Display7Segment(self.config)
            result = display.display_text(text)
            
            # Set the LEDs
            self.set_leds(result['led_indices'], color=color)
            
            # Push to hardware
            self.show()
            
            return True
        
        except Exception as e:
            print(f"ERROR in display_text: {e}")
            return False
    
    
    def test_single_led(self, index: int, duration: float = 0.5, color: Tuple[int, int, int] = (255, 255, 255)) -> bool:
        """
        Test a single LED by turning it on temporarily.
        
        Args:
            index (int): LED index to test
            duration (float): How long to keep it on (seconds)
            color (Tuple): RGB color
        
        Returns:
            bool: True if successful
        """
        try:
            if not 0 <= index < self.num_leds:
                print(f"ERROR: LED index {index} out of range")
                return False
            
            print(f"Testing LED {index}...")
            self.clear()
            self.strip[index] = color
            self.show()
            
            time.sleep(duration)
            
            self.clear()
            self.show()
            
            return True
        
        except Exception as e:
            print(f"ERROR in test_single_led: {e}")
            return False
    
    
    def get_status(self) -> dict:
        """
        Get current hardware status.
        
        Returns:
            dict: Status information
        """
        status = {
            'initialized': self.is_initialized,
            'use_mock': self.use_mock,
            'gpio_pin': self.pin,
            'num_leds': self.num_leds,
            'brightness': self.brightness,
            'current_color': self.current_color,
            'color_order': self.color_order,
        }
        
        if self.use_mock:
            status['strip_type'] = 'Mock (testing)'
            status['show_count'] = self.strip.show_count
        else:
            status['strip_type'] = 'Real Hardware (rpi_ws281x)'
        
        return status
    
    
    def print_status(self):
        """Pretty-print hardware status."""
        status = self.get_status()
        
        print(f"\n{'='*60}")
        print(f"DISPLAY HARDWARE STATUS (v{MODULE_VERSION})")
        print(f"{'='*60}")
        print(f"Initialized:   {status['initialized']}")
        print(f"Strip Type:    {status['strip_type']}")
        print(f"GPIO Pin:      {status['gpio_pin']}")
        print(f"Num LEDs:      {status['num_leds']}")
        print(f"Brightness:    {status['brightness']}/255")
        print(f"Current Color: RGB{status['current_color']}")
        print(f"Color Order:   {status['color_order']}")
        
        if status['use_mock']:
            print(f"Show Calls:    {status['show_count']}")
        
        print(f"{'='*60}\n")


# Module-level test function
def test_module():
    """Quick self-test of the module."""
    try:
        import config
    except ImportError:
        print("ERROR: config.py not found. Cannot run tests.")
        return False
    
    print(f"\n{'='*60}")
    print(f"Testing display_hardware.py v{MODULE_VERSION}")
    print(f"{'='*60}\n")
    
    # Test with mock GPIO
    print("1. Testing with MOCK GPIO...")
    hardware = DisplayHardware(config, use_mock=True)
    hardware.print_status()
    
    # Test set_leds
    print("2. Testing set_leds()...")
    result = hardware.set_leds([0, 1, 2, 3], color=(255, 0, 0))
    print(f"   set_leds([0,1,2,3], RED): {result}")
    
    # Test show
    print("3. Testing show()...")
    result = hardware.show()
    print(f"   show(): {result}")
    
    # Test clear
    print("4. Testing clear()...")
    result = hardware.clear()
    print(f"   clear(): {result}")
    result = hardware.show()
    print(f"   show(): {result}")
    
    # Test set_all
    print("5. Testing set_all()...")
    result = hardware.set_all(color=(0, 255, 0))
    print(f"   set_all(GREEN): {result}")
    result = hardware.show()
    print(f"   show(): {result}")
    
    # Test set_brightness
    print("6. Testing set_brightness()...")
    result = hardware.set_brightness(128)
    print(f"   set_brightness(128): {result}")
    
    # Test display_text
    print("7. Testing display_text()...")
    result = hardware.display_text("1234", color=(255, 255, 255))
    print(f"   display_text('1234', WHITE): {result}")
    
    print(f"\n{'='*60}")
    print("✓ All tests completed")
    print(f"{'='*60}\n")
    
    return True


if __name__ == "__main__":
    test_module()
