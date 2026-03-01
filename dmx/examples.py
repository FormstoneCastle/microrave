#!/usr/bin/env python3
"""
Advanced DMX Control Examples
"""

import time
import sys
from dmx_controller import DMXController


def example_strobe(controller: DMXController, channel: int = 1, speed: float = 0.1):
    """Strobe effect"""
    print(f"Strobe on channel {channel}")
    for _ in range(10):
        controller.set_channel(channel, 255)
        controller.send()
        time.sleep(speed)
        
        controller.set_channel(channel, 0)
        controller.send()
        time.sleep(speed)


def example_color_mixing(controller: DMXController):
    """RGB color mixing demo (channels 1-3)"""
    print("Color mixing demo")
    
    colors = {
        "Red": (255, 0, 0),
        "Green": (0, 255, 0),
        "Blue": (0, 0, 255),
        "Cyan": (0, 255, 255),
        "Magenta": (255, 0, 255),
        "Yellow": (255, 255, 0),
        "White": (255, 255, 255),
    }
    
    for name, (r, g, b) in colors.items():
        print(f"  {name}: R={r}, G={g}, B={b}")
        controller.set_channels([r, g, b])
        controller.send()
        time.sleep(1)
    
    controller.clear()
    controller.send()


def example_intensity_sweep(controller: DMXController, channel: int = 1, duration: float = 5):
    """Smooth intensity sweep on a channel"""
    print(f"Intensity sweep on channel {channel}")
    
    steps = 50
    step_time = duration / steps
    
    # Fade up
    for i in range(steps):
        value = int(255 * i / steps)
        controller.set_channel(channel, value)
        controller.send()
        time.sleep(step_time)
    
    # Fade down
    for i in range(steps, -1, -1):
        value = int(255 * i / steps)
        controller.set_channel(channel, value)
        controller.send()
        time.sleep(step_time)


def example_multi_channel_pattern(controller: DMXController):
    """Complex multi-channel pattern"""
    print("Multi-channel wave pattern")
    
    for cycle in range(3):
        print(f"Cycle {cycle + 1}")
        
        # Wave effect across channels 1-3
        for phase in range(3):
            for intensity in range(0, 256, 20):
                controller.clear()
                
                ch = (phase % 3) + 1
                controller.set_channel(ch, intensity)
                controller.send()
                time.sleep(0.05)
    
    controller.clear()
    controller.send()


def example_fixture_sequence(controller: DMXController):
    """Sequence through 3 fixtures on channels 1, 2, 3"""
    print("3-fixture sequence")
    
    fixtures = [
        {"name": "Fixture 1", "channel": 1, "color": (255, 0, 0)},
        {"name": "Fixture 2", "channel": 2, "color": (0, 255, 0)},
        {"name": "Fixture 3", "channel": 3, "color": (0, 0, 255)},
    ]
    
    for _ in range(2):
        for fixture in fixtures:
            print(f"  {fixture['name']}")
            controller.clear()
            controller.set_channel(fixture["channel"], 200)
            controller.send()
            time.sleep(0.5)
    
    controller.clear()
    controller.send()


def example_gradient(controller: DMXController):
    """Create gradient across 3 channels"""
    print("Gradient effect")
    
    for i in range(50):
        # Create gradient from red to blue across channels
        val1 = int(255 * (1 - i / 50))  # Red channel fading
        val2 = int(128 * (i / 50))       # Green channel ramping
        val3 = int(255 * (i / 50))       # Blue channel ramping
        
        controller.set_channels([val1, val2, val3])
        controller.send()
        time.sleep(0.05)
    
    controller.clear()
    controller.send()


def main():
    """Run all examples"""
    try:
        print("DMX Advanced Examples")
        print("=" * 50)
        
        with DMXController() as controller:
            # Pause between examples
            pause = 2
            
            print("\n1. Strobe Effect")
            example_strobe(controller, channel=1, speed=0.15)
            time.sleep(pause)
            
            print("\n2. Color Mixing")
            example_color_mixing(controller)
            time.sleep(pause)
            
            print("\n3. Intensity Sweep")
            example_intensity_sweep(controller, channel=1, duration=3)
            time.sleep(pause)
            
            print("\n4. Multi-Channel Pattern")
            example_multi_channel_pattern(controller)
            time.sleep(pause)
            
            print("\n5. Fixture Sequence")
            example_fixture_sequence(controller)
            time.sleep(pause)
            
            print("\n6. Gradient Effect")
            example_gradient(controller)
            time.sleep(pause)
            
            print("\nAll examples completed!")
            
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
