#!/usr/bin/env python3
"""
Hardware Test Suite for 7-Segment Display (v0.0.1)

Menu-driven test script to verify WS2812 LED strip control.
Tests LED control, segment mapping, digit display, brightness, colors.

Usage:
    python3 test_hardware.py

The script will:
1. Detect Pi or Windows environment
2. Auto-select mock GPIO if on Windows
3. Present menu of tests
4. Run selected tests
5. Show results and diagnostics
"""

MODULE_VERSION = "0.0.1"

import sys
import time
import platform
from typing import List, Tuple

# Import our modules
try:
    import config
    from display_7seg import Display7Segment
    from display_hardware import DisplayHardware
except ImportError as e:
    print(f"ERROR: {e}")
    print("Make sure config.py, display_7seg.py, and display_hardware.py are in the same directory.")
    sys.exit(1)


class HardwareTester:
    """Comprehensive hardware test suite."""
    
    def __init__(self):
        """Initialize tester."""
        self.display = Display7Segment(config)
        
        # Auto-detect if running on Pi or Windows
        system = platform.system()
        self.use_mock = system != "Linux" or not self._is_raspberry_pi()
        
        print(f"\n{'='*60}")
        print(f"7-Segment Display Hardware Tester v{MODULE_VERSION}")
        print(f"{'='*60}")
        print(f"System: {system}")
        print(f"GPIO Mode: {'MOCK (Windows/Testing)' if self.use_mock else 'REAL (Raspberry Pi)'}")
        print(f"{'='*60}\n")
        
        # Initialize hardware
        self.hardware = DisplayHardware(config, use_mock=self.use_mock)
        self.hardware.print_status()
    
    
    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            with open('/etc/os-release', 'r') as f:
                return 'Raspberry' in f.read()
        except:
            return False
    
    
    def clear_screen(self):
        """Clear terminal screen."""
        import os
        os.system('clear' if platform.system() != 'Windows' else 'cls')
    
    
    def show_menu(self):
        """Show main test menu."""
        print(f"\n{'='*60}")
        print("HARDWARE TEST MENU")
        print(f"{'='*60}")
        print("1. Test Single LED (turn on one LED at a time)")
        print("2. Test Segments (light up each segment)")
        print("3. Test Digits (display 0-9)")
        print("4. Test Full Display (display various 4-digit numbers)")
        print("5. Test Brightness (vary brightness 0-255)")
        print("6. Test Colors (RGB color combinations)")
        print("7. Performance Test (measure update speed)")
        print("8. LED Mapping Diagnostic (verify physical routing)")
        print("9. Print Hardware Status")
        print("0. Exit")
        print(f"{'='*60}\n")
    
    
    def test_single_led(self):
        """Test turning on individual LEDs."""
        print(f"\n{'='*60}")
        print("TEST 1: Single LED Test")
        print(f"{'='*60}\n")
        
        try:
            num_test = min(10, self.hardware.num_leds)
            print(f"Testing first {num_test} LEDs (0.5 seconds each)...")
            print("Watch the physical LED strip - each LED should light up in sequence.\n")
            
            for i in range(num_test):
                print(f"  Testing LED {i}...", end='', flush=True)
                result = self.hardware.test_single_led(i, duration=0.5, color=(255, 255, 255))
                print(f" {'✓' if result else '✗'}")
                time.sleep(0.2)
            
            print("\n✓ Single LED test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_segments(self):
        """Test individual segments."""
        print(f"\n{'='*60}")
        print("TEST 2: Segment Test")
        print(f"{'='*60}\n")
        
        try:
            segment_order = config.SEGMENT_ORDER
            char_num = 0  # Test first character
            
            print(f"Testing each segment of character 0 (first digit)")
            print(f"Segment order: {segment_order}\n")
            
            for segment in segment_order:
                led_indices = config.DISPLAY_LED_MAP[f'char_{char_num}'][segment]
                
                print(f"  Segment '{segment}' (LEDs {min(led_indices)}-{max(led_indices)})...", end='', flush=True)
                
                self.hardware.set_leds(led_indices, color=(255, 255, 255))
                self.hardware.show()
                
                time.sleep(0.8)
                print(" ✓")
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Segment test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_digits(self):
        """Test displaying digits 0-9."""
        print(f"\n{'='*60}")
        print("TEST 3: Digit Test (0-9)")
        print(f"{'='*60}\n")
        
        try:
            print("Displaying each digit in first position (1 second each)...\n")
            
            for digit in range(10):
                text = f"{digit}000"
                print(f"  Displaying '{digit}'...", end='', flush=True)
                
                self.hardware.display_text(text, color=(255, 255, 255))
                time.sleep(1.0)
                print(" ✓")
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Digit test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_full_display(self):
        """Test full 4-digit display."""
        print(f"\n{'='*60}")
        print("TEST 4: Full Display Test")
        print(f"{'='*60}\n")
        
        try:
            test_cases = [
                "0000",
                "1111",
                "1234",
                "5678",
                "9999",
                "8888",
                "00:00",
                "12:34",
            ]
            
            print("Testing full 4-digit display (1 second each)...\n")
            
            for text in test_cases:
                print(f"  Displaying '{text}'...", end='', flush=True)
                
                result = self.display.display_text(text)
                self.hardware.set_leds(result['led_indices'], color=(255, 255, 255))
                self.hardware.show()
                
                print(f" (LEDs ON: {result['led_count']})")
                time.sleep(1.0)
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Full display test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_brightness(self):
        """Test brightness control."""
        print(f"\n{'='*60}")
        print("TEST 5: Brightness Test")
        print(f"{'='*60}\n")
        
        try:
            print("Displaying '8888' at various brightness levels (0.5 seconds each)...\n")
            
            brightness_levels = [255, 200, 150, 100, 50, 25, 0]
            
            for brightness in brightness_levels:
                print(f"  Brightness {brightness}/255...", end='', flush=True)
                
                self.hardware.set_brightness(brightness)
                self.hardware.display_text("8888", color=(255, 255, 255))
                
                time.sleep(0.5)
                print(" ✓")
            
            # Restore brightness
            self.hardware.set_brightness(config.WS2812_BRIGHTNESS)
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Brightness test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_colors(self):
        """Test RGB color combinations."""
        print(f"\n{'='*60}")
        print("TEST 6: Color Test")
        print(f"{'='*60}\n")
        
        try:
            colors = [
                ("White", (255, 255, 255)),
                ("Red", (255, 0, 0)),
                ("Green", (0, 255, 0)),
                ("Blue", (0, 0, 255)),
                ("Yellow", (255, 255, 0)),
                ("Cyan", (0, 255, 255)),
                ("Magenta", (255, 0, 255)),
                ("Orange", (255, 165, 0)),
            ]
            
            print("Testing RGB colors on display '8888' (0.5 seconds each)...\n")
            
            for color_name, color_rgb in colors:
                print(f"  {color_name} {color_rgb}...", end='', flush=True)
                
                self.hardware.display_text("8888", color=color_rgb)
                time.sleep(0.5)
                print(" ✓")
            
            # Return to white
            self.hardware.display_text("8888", color=(255, 255, 255))
            time.sleep(0.5)
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Color test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_performance(self):
        """Performance test - measure update speed."""
        print(f"\n{'='*60}")
        print("TEST 7: Performance Test")
        print(f"{'='*60}\n")
        
        try:
            num_iterations = 100
            test_text = "1234"
            
            print(f"Updating display '{test_text}' {num_iterations} times...")
            print("Measuring time per update...\n")
            
            result = self.display.display_text(test_text)
            
            start_time = time.time()
            
            for i in range(num_iterations):
                self.hardware.set_leds(result['led_indices'], color=(255, 255, 255))
                self.hardware.show()
            
            end_time = time.time()
            total_time = end_time - start_time
            time_per_update = total_time / num_iterations * 1000  # Convert to ms
            updates_per_second = num_iterations / total_time
            
            print(f"Total time: {total_time:.3f} seconds")
            print(f"Time per update: {time_per_update:.3f} ms")
            print(f"Updates per second: {updates_per_second:.1f} Hz")
            
            # Config expects 30 Hz update rate
            target_hz = 30
            print(f"\nTarget update rate: {target_hz} Hz")
            print(f"Actual update rate: {updates_per_second:.1f} Hz")
            
            if updates_per_second >= target_hz:
                print("✓ Performance is sufficient")
            else:
                print(f"⚠ Performance is below target (need optimization)")
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ Performance test complete")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def test_led_mapping(self):
        """Diagnostic test - verify LED mapping."""
        print(f"\n{'='*60}")
        print("TEST 8: LED Mapping Diagnostic")
        print(f"{'='*60}\n")
        
        try:
            print("Verifying LED mapping by lighting up each character's segments...\n")
            
            segment_order = config.SEGMENT_ORDER
            
            for char_num in range(4):
                char_key = f'char_{char_num}'
                print(f"\nCharacter {char_num}:")
                
                for segment in segment_order:
                    led_indices = config.DISPLAY_LED_MAP[char_key][segment]
                    
                    print(f"  Segment '{segment}': LEDs {min(led_indices):3d}-{max(led_indices):3d}", end='')
                    
                    # Light up this segment
                    self.hardware.clear()
                    self.hardware.set_leds(led_indices, color=(0, 255, 0))
                    self.hardware.show()
                    
                    print(" [PRESS ENTER TO CONTINUE]", end='', flush=True)
                    input()
            
            self.hardware.clear()
            self.hardware.show()
            
            print("\n✓ LED mapping diagnostic complete")
            print("\nExpected physical behavior:")
            print("  Each segment should light up in order (g, b, a, f, e, d, c)")
            print("  Verify this matches the physical 7-segment routing")
        
        except KeyboardInterrupt:
            print("\n✗ Test interrupted by user")
    
    
    def run(self):
        """Run interactive test menu."""
        while True:
            self.show_menu()
            
            choice = input("Select test (0-9): ").strip()
            
            if choice == "1":
                self.test_single_led()
            elif choice == "2":
                self.test_segments()
            elif choice == "3":
                self.test_digits()
            elif choice == "4":
                self.test_full_display()
            elif choice == "5":
                self.test_brightness()
            elif choice == "6":
                self.test_colors()
            elif choice == "7":
                self.test_performance()
            elif choice == "8":
                self.test_led_mapping()
            elif choice == "9":
                self.hardware.print_status()
            elif choice == "0":
                print("\nExiting hardware tester...")
                break
            else:
                print("Invalid choice. Please select 0-9.")
            
            input("\nPress ENTER to continue...")


if __name__ == "__main__":
    try:
        tester = HardwareTester()
        tester.run()
    except KeyboardInterrupt:
        print("\n\nHardware tester interrupted by user")
        sys.exit(0)
