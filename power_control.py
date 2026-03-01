"""
MicroRave Power Control Module
Controls external power relay via GPIO output.

Hardware:
- GPIO pin connected to relay control circuit
- 3.3V output controlling power supply

Features:
- Simple ON/OFF control
- State tracking
- Safe shutdown
"""

import time
import logging
from typing import Callable, Optional

try:
    import lgpio
    HARDWARE_GPIO_AVAILABLE = True
except ImportError:
    HARDWARE_GPIO_AVAILABLE = False
    print("⚠ lgpio not available - using mock GPIO")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PowerControl:
    """
    Controls external power relay via GPIO output.
    """
    
    def __init__(self, gpio_pin: int = 27, active_high: bool = True, use_mock: bool = False):
        """
        Initialize power control.
        
        Args:
            gpio_pin: GPIO pin number for relay control
            active_high: True if HIGH=ON, False if LOW=ON
            use_mock: Use mock GPIO (for testing)
        """
        self.gpio_pin = gpio_pin
        self.active_high = active_high
        self.use_mock = use_mock or not HARDWARE_GPIO_AVAILABLE
        
        # State tracking
        self.is_on = False
        
        # Hardware
        self.h = None  # lgpio handle
        
        # Callbacks
        self.on_power_change: Optional[Callable[[bool], None]] = None
        
        self._init_hardware()
        logger.info(f"✓ PowerControl initialized (GPIO {gpio_pin})")
    
    def _init_hardware(self):
        """Initialize GPIO pin"""
        if self.use_mock:
            logger.info("🔧 Using mock power control")
            return
        
        logger.info(f"🔌 Initializing power relay on GPIO {self.gpio_pin}")
        
        try:
            # Open GPIO device
            self.h = lgpio.gpiochip_open(0)
            
            # Configure pin as output
            lgpio.gpio_claim_output(self.h, self.gpio_pin, 0)
            
            # Ensure power starts OFF
            self.is_on = False
            self._set_output(False)
            
            logger.info(f"✓ Power relay GPIO initialized")
            
        except Exception as e:
            logger.error(f"✗ Failed to initialize GPIO: {e}")
            logger.info("  Falling back to mock mode")
            self.use_mock = True
    
    def _set_output(self, state: bool):
        """Set GPIO output state"""
        if not self.use_mock and self.h is not None:
            try:
                # Convert state to GPIO value based on active_high setting
                gpio_value = 1 if (state and self.active_high or not state and not self.active_high) else 0
                lgpio.gpio_write(self.h, self.gpio_pin, gpio_value)
            except Exception as e:
                logger.error(f"✗ Failed to set GPIO: {e}")
    
    def power_on(self):
        """Turn power ON"""
        if not self.is_on:
            self.is_on = True
            self._set_output(True)
            logger.info("🔌 POWER ON")
            
            if self.on_power_change:
                try:
                    self.on_power_change(True)
                except Exception as e:
                    logger.error(f"✗ Power callback error: {e}")
    
    def power_off(self):
        """Turn power OFF"""
        if self.is_on:
            self.is_on = False
            self._set_output(False)
            logger.info("⚫ POWER OFF")
            
            if self.on_power_change:
                try:
                    self.on_power_change(False)
                except Exception as e:
                    logger.error(f"✗ Power callback error: {e}")
    
    def toggle(self):
        """Toggle power state"""
        if self.is_on:
            self.power_off()
        else:
            self.power_on()
    
    def get_state(self) -> bool:
        """Get current power state"""
        return self.is_on
    
    def register_power_change_callback(self, callback: Callable[[bool], None]):
        """
        Register callback for power state changes.
        
        Args:
            callback: Function to call with boolean (True=ON, False=OFF)
        """
        self.on_power_change = callback
        logger.debug("✓ Registered power change callback")
    
    def cleanup(self):
        """Clean up GPIO and turn off power"""
        self.power_off()
        
        if self.h is not None:
            try:
                lgpio.gpiochip_close(self.h)
                logger.info("✓ Power control cleanup complete")
            except Exception as e:
                logger.error(f"✗ Error during cleanup: {e}")


# ============================================================================
# TESTING & STANDALONE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Standalone test script for power control.
    
    Usage:
        python3 power_control.py
    """
    
    print("\n" + "="*60)
    print("MICRORAVE - POWER CONTROL TEST")
    print("="*60 + "\n")
    
    # Create power controller with mock GPIO
    power = PowerControl(gpio_pin=27, active_high=True, use_mock=True)
    
    # Define event handler
    def on_power_change(state: bool):
        status = "✓ ON" if state else "⚫ OFF"
        print(f"  Power changed: {status}")
    
    power.register_power_change_callback(on_power_change)
    
    print("🧪 Testing power control functionality...\n")
    
    # Test 1: Turn on
    print("Test 1: Power ON")
    print(f"  Current state: {power.get_state()}")
    power.power_on()
    print(f"  Current state: {power.get_state()}")
    
    # Test 2: Turn off
    print("\nTest 2: Power OFF")
    print(f"  Current state: {power.get_state()}")
    power.power_off()
    print(f"  Current state: {power.get_state()}")
    
    # Test 3: Toggle
    print("\nTest 3: Toggle")
    print(f"  Initial state: {power.get_state()}")
    power.toggle()
    print(f"  After toggle: {power.get_state()}")
    power.toggle()
    print(f"  After toggle: {power.get_state()}")
    
    # Test 4: Rapid toggle (simulating power cycling)
    print("\nTest 4: Rapid power cycling")
    for i in range(3):
        print(f"  Cycle {i+1}:")
        power.power_on()
        time.sleep(0.2)
        power.power_off()
        time.sleep(0.2)
    
    print("\n✓ Power control test complete!")
    power.cleanup()
