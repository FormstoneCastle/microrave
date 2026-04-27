"""
MicroRave Button Input Module
Handles 21 buttons + 1 switch with debouncing and state management.

Features:
- Debounce handling to prevent false triggers
- State tracking (pressed/released)
- Event callbacks on state changes
- Works on Raspberry Pi 5 with lgpio
"""

import time
import logging
from typing import Callable, Dict, Optional, List
from dataclasses import dataclass
from enum import Enum

try:
    import lgpio
    HARDWARE_GPIO_AVAILABLE = True
except ImportError:
    HARDWARE_GPIO_AVAILABLE = False
    print("⚠ lgpio not available - using mock GPIO for testing")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ButtonState(Enum):
    """Button state enumeration"""
    RELEASED = 0
    PRESSED = 1


@dataclass
class ButtonEvent:
    """Data class for button events"""
    button_id: int
    button_name: str
    state: ButtonState
    timestamp: float
    
    def __repr__(self):
        return f"ButtonEvent(id={self.button_id}, name={self.button_name}, state={self.state.name}, time={self.timestamp:.3f})"


class ButtonInput:
    """
    Manages 21 buttons + 1 switch on GPIO pins.
    
    Features:
    - Debouncing with configurable delay
    - State change detection
    - Event-based callbacks
    - Mock mode for testing without hardware
    """
    
    def __init__(self, button_pins: Dict[int, str], switch_pin: int, 
                 switch_name: str = "Switch",
                 debounce_ms: int = 20,
                 use_mock: bool = False):
        """
        Initialize button input handler.
        
        Args:
            button_pins: Dictionary {gpio_pin: button_name}
            switch_pin: GPIO pin for switch
            switch_name: Name of switch
            debounce_ms: Debounce time in milliseconds
            use_mock: Use mock GPIO (for testing without Pi)
        """
        self.button_pins = button_pins
        self.switch_pin = switch_pin
        self.switch_name = switch_name
        self.debounce_ms = debounce_ms / 1000.0  # Convert to seconds
        self.use_mock = use_mock or not HARDWARE_GPIO_AVAILABLE
        
        # State tracking
        self.button_states: Dict[int, ButtonState] = {}  # pin -> state
        self.button_names: Dict[int, str] = {}  # pin -> name
        self.last_change_time: Dict[int, float] = {}  # pin -> timestamp
        
        # Event callbacks
        self.on_press_callbacks: Dict[int, List[Callable]] = {}  # pin -> [callbacks]
        self.on_release_callbacks: Dict[int, List[Callable]] = {}  # pin -> [callbacks]
        
        # Hardware
        self.h = None  # lgpio handle
        self.all_pins = list(button_pins.keys()) + [switch_pin]
        
        self._init_hardware()
        logger.info(f"✓ ButtonInput initialized ({len(self.button_pins)} buttons + 1 switch)")
        
    def _init_hardware(self):
        """Initialize GPIO pins for input"""
        if self.use_mock:
            logger.info("🔧 Using mock GPIO mode")
            self._init_mock()
        else:
            logger.info("🔌 Using hardware GPIO (lgpio)")
            self._init_hardware_gpio()
    
    def _init_mock(self):
        """Initialize mock GPIO for testing"""
        # Initialize all button/switch states as released (HIGH = not pressed)
        for pin in self.button_pins.keys():
            self.button_states[pin] = ButtonState.RELEASED
            self.button_names[pin] = self.button_pins[pin]
            self.last_change_time[pin] = time.time()
        
        self.button_states[self.switch_pin] = ButtonState.RELEASED
        self.button_names[self.switch_pin] = self.switch_name
        self.last_change_time[self.switch_pin] = time.time()
        
        logger.info(f"✓ Mock GPIO initialized: {len(self.button_pins)} buttons + 1 switch")
    
    def _init_hardware_gpio(self):
        """Initialize real GPIO pins using lgpio"""
        try:
            # Open GPIO device
            self.h = lgpio.gpiochip_open(0)
            
            # Configure all pins as inputs
            for pin in self.all_pins:
                lgpio.gpio_claim_input(self.h, pin)
                self.button_states[pin] = ButtonState.RELEASED
                self.button_names[pin] = self.button_pins.get(pin, self.switch_name)
                self.last_change_time[pin] = time.time()
            
            logger.info(f"✓ Hardware GPIO initialized: {len(self.all_pins)} pins configured")
            
        except Exception as e:
            logger.error(f"✗ Failed to initialize GPIO: {e}")
            logger.info("  Falling back to mock GPIO")
            self.use_mock = True
            self._init_mock()
    
    def register_press_callback(self, pin: int, callback: Callable[[ButtonEvent], None]):
        """
        Register callback for button press event.
        
        Args:
            pin: GPIO pin number
            callback: Function to call on press (receives ButtonEvent)
        """
        if pin not in self.on_press_callbacks:
            self.on_press_callbacks[pin] = []
        self.on_press_callbacks[pin].append(callback)
        logger.debug(f"✓ Registered press callback for pin {pin}")
    
    def register_release_callback(self, pin: int, callback: Callable[[ButtonEvent], None]):
        """
        Register callback for button release event.
        
        Args:
            pin: GPIO pin number
            callback: Function to call on release (receives ButtonEvent)
        """
        if pin not in self.on_release_callbacks:
            self.on_release_callbacks[pin] = []
        self.on_release_callbacks[pin].append(callback)
        logger.debug(f"✓ Registered release callback for pin {pin}")
    
    def _trigger_callbacks(self, pin: int, state: ButtonState):
        """Trigger registered callbacks for button state change"""
        button_name = self.button_names.get(pin, f"GPIO_{pin}")
        event = ButtonEvent(
            button_id=pin,
            button_name=button_name,
            state=state,
            timestamp=time.time()
        )
        
        if state == ButtonState.PRESSED:
            callbacks = self.on_press_callbacks.get(pin, [])
            logger.debug(f"→ PRESSED: {event}")
        else:
            callbacks = self.on_release_callbacks.get(pin, [])
            logger.debug(f"← RELEASED: {event}")
        
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"✗ Callback error: {e}")
    
    def update(self):
        """
        Poll all buttons and check for state changes.
        Call this regularly (e.g., every 10-20ms) in your main loop.
        """
        current_time = time.time()
        
        for pin in self.all_pins:
            # Read pin state
            if self.use_mock:
                current_state = self.button_states[pin]  # In mock, state doesn't change unless set
            else:
                try:
                    pin_value = lgpio.gpio_read(self.h, pin)
                    # pin_value: HIGH (1) = released, LOW (0) = pressed
                    current_state = ButtonState.PRESSED if pin_value == 0 else ButtonState.RELEASED
                except Exception as e:
                    logger.warning(f"✗ Failed to read GPIO {pin}: {e}")
                    continue
            
            # Check if state changed and debounce time has passed
            time_since_change = current_time - self.last_change_time.get(pin, 0)
            
            if current_state != self.button_states.get(pin):
                if time_since_change >= self.debounce_ms:
                    # Valid state change (debounce passed)
                    old_state = self.button_states.get(pin)
                    self.button_states[pin] = current_state
                    self.last_change_time[pin] = current_time
                    
                    # Trigger callbacks
                    self._trigger_callbacks(pin, current_state)
    
    def get_button_state(self, pin: int) -> Optional[ButtonState]:
        """Get current state of a button"""
        return self.button_states.get(pin)
    
    def get_all_states(self) -> Dict[int, ButtonState]:
        """Get state of all buttons and switch"""
        return self.button_states.copy()
    
    def get_state_dict(self) -> Dict[str, bool]:
        """Get all button states as a dictionary {name: is_pressed}"""
        return {
            name: self.button_states.get(pin, ButtonState.RELEASED) == ButtonState.PRESSED
            for pin, name in self.button_names.items()
        }
    
    def simulate_press(self, pin: int):
        """
        Simulate a button press (for testing).
        Useful for testing without physical buttons.
        """
        if self.use_mock:
            old_state = self.button_states.get(pin, ButtonState.RELEASED)
            self.button_states[pin] = ButtonState.PRESSED
            self.last_change_time[pin] = time.time()
            self._trigger_callbacks(pin, ButtonState.PRESSED)
            logger.info(f"📝 Simulated press on pin {pin}")
    
    def simulate_release(self, pin: int):
        """
        Simulate a button release (for testing).
        """
        if self.use_mock:
            old_state = self.button_states.get(pin, ButtonState.RELEASED)
            self.button_states[pin] = ButtonState.RELEASED
            self.last_change_time[pin] = time.time()
            self._trigger_callbacks(pin, ButtonState.RELEASED)
            logger.info(f"📝 Simulated release on pin {pin}")
    
    def cleanup(self):
        """Clean up GPIO resources"""
        if self.h is not None:
            try:
                lgpio.gpiochip_close(self.h)
                logger.info("✓ GPIO cleanup complete")
            except Exception as e:
                logger.error(f"✗ Error during GPIO cleanup: {e}")


# ============================================================================
# TESTING & STANDALONE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Standalone test script for button input.
    
    Usage:
        python3 button_input.py
    """
    
    # Test configuration
    from config import BUTTON_PINS, SWITCH_PIN, SWITCH_NAME
    
    print("\n" + "="*60)
    print("MICRORAVE - BUTTON INPUT TEST")
    print("="*60 + "\n")
    
    # Create button input handler (with mock GPIO for testing)
    button_handler = ButtonInput(
        button_pins=BUTTON_PINS,
        switch_pin=SWITCH_PIN,
        switch_name=SWITCH_NAME,
        debounce_ms=20,
        use_mock=True  # Force mock mode for testing
    )
    
    # Define event handlers
    def on_button_pressed(event: ButtonEvent):
        print(f"✓ PRESSED:  {event.button_name} (GPIO {event.button_id})")
    
    def on_button_released(event: ButtonEvent):
        print(f"✓ RELEASED: {event.button_name} (GPIO {event.button_id})")
    
    # Register callbacks for all buttons
    for pin, name in BUTTON_PINS.items():
        button_handler.register_press_callback(pin, on_button_pressed)
        button_handler.register_release_callback(pin, on_button_released)
    
    # Register callbacks for switch
    button_handler.register_press_callback(SWITCH_PIN, lambda e: print(f"🔒 CLOSED: {e.button_name}"))
    button_handler.register_release_callback(SWITCH_PIN, lambda e: print(f"🔓 OPENED: {e.button_name}"))
    
    print("\n📋 Available buttons:")
    for pin, name in BUTTON_PINS.items():
        print(f"   {pin:2d} → {name}")
    print(f"   {SWITCH_PIN:2d} → {SWITCH_NAME}")
    
    print("\n🧪 Testing button simulation...")
    print("   (Simulating button presses and releases)\n")
    
    # Test some buttons
    test_sequence = [0, 5, 10, 15, 20, 22]  # Button IDs to test
    
    for pin in test_sequence:
        print(f"\n→ Testing pin {pin}...")
        button_handler.simulate_press(pin)
        button_handler.update()
        time.sleep(0.1)
        button_handler.simulate_release(pin)
        button_handler.update()
        time.sleep(0.1)
    
    print("\n✓ Button input test complete!")
    button_handler.cleanup()
