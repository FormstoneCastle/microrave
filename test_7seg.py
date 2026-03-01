#!/usr/bin/env python3
"""
Test Suite for 7seg.py v0.0.1

Tests the pure data transformation logic:
- Text normalization
- Segment pattern lookup
- LED index mapping
- Full text-to-LED conversion
"""

import sys
from pathlib import Path

# Import config and display module
try:
    import config
    from display_7seg import Display7Segment
except ImportError as e:
    print(f"ERROR: {e}")
    sys.exit(1)


class TestDisplay7Segment:
    """Test suite for Display7Segment class."""
    
    def __init__(self):
        self.display = Display7Segment(config)
        self.passed = 0
        self.failed = 0
        self.tests = []
    
    
    def assert_equal(self, actual, expected, test_name: str):
        """Check if actual equals expected."""
        if actual == expected:
            self.passed += 1
            print(f"  ✓ {test_name}")
            return True
        else:
            self.failed += 1
            print(f"  ✗ {test_name}")
            print(f"    Expected: {expected}")
            print(f"    Got:      {actual}")
            return False
    
    
    def assert_true(self, condition, test_name: str):
        """Check if condition is True."""
        if condition:
            self.passed += 1
            print(f"  ✓ {test_name}")
            return True
        else:
            self.failed += 1
            print(f"  ✗ {test_name}")
            return False
    
    
    def test_normalize_text(self):
        """Test text normalization."""
        print("\n" + "="*60)
        print("TEST: normalize_text()")
        print("="*60)
        
        self.assert_equal(self.display.normalize_text("1"), "0001", "Pad '1' to '0001'")
        self.assert_equal(self.display.normalize_text("12"), "0012", "Pad '12' to '0012'")
        self.assert_equal(self.display.normalize_text("123"), "0123", "Pad '123' to '0123'")
        self.assert_equal(self.display.normalize_text("1234"), "1234", "Keep '1234' as '1234'")
        self.assert_equal(self.display.normalize_text("12345"), "1234", "Truncate '12345' to '1234'")
        self.assert_equal(self.display.normalize_text("12:34"), "1234", "Remove colon from '12:34'")
        self.assert_equal(self.display.normalize_text("00:00"), "0000", "Remove colon from '00:00'")
    
    
    def test_validate_text(self):
        """Test text validation."""
        print("\n" + "="*60)
        print("TEST: validate_text()")
        print("="*60)
        
        self.assert_true(self.display.validate_text("1234"), "Valid: '1234'")
        self.assert_true(self.display.validate_text("00:00"), "Valid: '00:00'")
        self.assert_true(self.display.validate_text("12:34"), "Valid: '12:34'")
        self.assert_true(self.display.validate_text("0"), "Valid: '0'")
        self.assert_true(not self.display.validate_text(""), "Invalid: ''")
        self.assert_true(not self.display.validate_text("ABCD"), "Invalid: 'ABCD'")
    
    
    def test_get_segment_pattern(self):
        """Test segment pattern lookup."""
        print("\n" + "="*60)
        print("TEST: get_segment_pattern()")
        print("="*60)
        
        # Pattern format: [a, b, c, d, e, f, g]
        # Digit '0' = [1, 1, 1, 1, 1, 1, 0] (all except g)
        self.assert_equal(
            self.display.get_segment_pattern('0'),
            [1, 1, 1, 1, 1, 1, 0],
            "Digit '0' pattern is [1,1,1,1,1,1,0]"
        )
        
        # Digit '1' = [0, 1, 1, 0, 0, 0, 0] (only b,c)
        self.assert_equal(
            self.display.get_segment_pattern('1'),
            [0, 1, 1, 0, 0, 0, 0],
            "Digit '1' pattern is [0,1,1,0,0,0,0]"
        )
        
        # Digit '8' = [1, 1, 1, 1, 1, 1, 1] (all segments)
        self.assert_equal(
            self.display.get_segment_pattern('8'),
            [1, 1, 1, 1, 1, 1, 1],
            "Digit '8' pattern is [1,1,1,1,1,1,1]"
        )
    
    
    def test_text_to_segments(self):
        """Test text-to-segment conversion."""
        print("\n" + "="*60)
        print("TEST: text_to_segments()")
        print("="*60)
        
        # Test digit '1' (segments b and c only)
        result = self.display.text_to_segments("1000")
        char_0_segments = result['char_0']
        
        # Check that char_0 is digit '1'
        self.assert_equal(char_0_segments['b'], 1, "Digit '1': segment 'b' is ON")
        self.assert_equal(char_0_segments['c'], 1, "Digit '1': segment 'c' is ON")
        self.assert_equal(char_0_segments['g'], 0, "Digit '1': segment 'g' is OFF")
        self.assert_equal(char_0_segments['a'], 0, "Digit '1': segment 'a' is OFF")
        
        # Test digit '0' (all segments except g)
        result = self.display.text_to_segments("0000")
        char_0_segments = result['char_0']
        
        self.assert_equal(char_0_segments['a'], 1, "Digit '0': segment 'a' is ON")
        self.assert_equal(char_0_segments['b'], 1, "Digit '0': segment 'b' is ON")
        self.assert_equal(char_0_segments['c'], 1, "Digit '0': segment 'c' is ON")
        self.assert_equal(char_0_segments['d'], 1, "Digit '0': segment 'd' is ON")
        self.assert_equal(char_0_segments['e'], 1, "Digit '0': segment 'e' is ON")
        self.assert_equal(char_0_segments['f'], 1, "Digit '0': segment 'f' is ON")
        self.assert_equal(char_0_segments['g'], 0, "Digit '0': segment 'g' is OFF")
        
        # Test digit '8' (all segments)
        result = self.display.text_to_segments("8000")
        char_0_segments = result['char_0']
        
        all_on = all(char_0_segments[s] == 1 for s in ['a', 'b', 'c', 'd', 'e', 'f', 'g'])
        self.assert_true(all_on, "Digit '8': all segments are ON")
    
    
    def test_segments_to_led_indices(self):
        """Test segment-to-LED conversion."""
        print("\n" + "="*60)
        print("TEST: segments_to_led_indices()")
        print("="*60)
        
        # Get segments for "0000"
        segments = self.display.text_to_segments("0000")
        leds = self.display.segments_to_led_indices(segments)
        
        # Digit '0' should have 6 segments ON (a,b,c,d,e,f) × 2 LEDs = 12 LEDs per char
        # 4 chars × 12 = 48 total
        self.assert_equal(len(leds), 48, "Digit '0000': 48 LEDs should be ON (4 × 12)")
        
        # Get segments for "8888"
        segments = self.display.text_to_segments("8888")
        leds = self.display.segments_to_led_indices(segments)
        
        # Digit '8' should have all 7 segments ON × 2 LEDs = 14 LEDs per char
        # 4 chars × 14 = 56 total
        self.assert_equal(len(leds), 56, "Digit '8888': all 56 LEDs should be ON")
        
        # Get segments for "1111" (only b and c segments)
        segments = self.display.text_to_segments("1111")
        leds = self.display.segments_to_led_indices(segments)
        
        # Digit '1' should have 2 segments ON (b,c) × 2 LEDs = 4 LEDs per char
        # 4 chars × 4 = 16 total
        self.assert_equal(len(leds), 16, "Digit '1111': 16 LEDs should be ON (4 × 4)")
    
    
    def test_display_text(self):
        """Test full display_text() method."""
        print("\n" + "="*60)
        print("TEST: display_text()")
        print("="*60)
        
        # Test "0000"
        result = self.display.display_text("0000")
        self.assert_equal(result['text'], "0000", "Input '0000' normalized to '0000'")
        self.assert_equal(result['led_count'], 48, "Display '0000': 48 LEDs ON")
        
        # Test "12:34" (with colon)
        result = self.display.display_text("12:34")
        self.assert_equal(result['text'], "1234", "Input '12:34' normalized to '1234'")
        self.assert_equal(result['led_count'], 32, "Display '1234': 32 LEDs ON")
        
        # Test "8888" (all segments)
        result = self.display.display_text("8888")
        self.assert_equal(result['text'], "8888", "Input '8888' normalized to '8888'")
        self.assert_equal(result['led_count'], 56, "Display '8888': all 56 LEDs ON")
        
        # Test "1" (short input, should pad to "0001")
        result = self.display.display_text("1")
        self.assert_equal(result['text'], "0001", "Input '1' padded to '0001'")
        
        # Test "12345" (long input, should truncate to "1234")
        result = self.display.display_text("12345")
        self.assert_equal(result['text'], "1234", "Input '12345' truncated to '1234'")
        
        # Test LED indices are returned as flat list
        result = self.display.display_text("1234")
        self.assert_true(
            isinstance(result['led_indices'], list),
            "LED indices returned as list"
        )
        self.assert_true(
            all(isinstance(x, int) for x in result['led_indices']),
            "All LED indices are integers"
        )
        self.assert_true(
            result['led_indices'] == sorted(result['led_indices']),
            "LED indices are sorted"
        )
    
    
    def test_led_index_ranges(self):
        """Test that LED indices are within valid ranges."""
        print("\n" + "="*60)
        print("TEST: LED index ranges")
        print("="*60)
        
        for input_text in ["0000", "1234", "5678", "8888", "9999"]:
            result = self.display.display_text(input_text)
            leds = result['led_indices']
            
            if leds:
                min_led = min(leds)
                max_led = max(leds)
                
                self.assert_true(
                    min_led >= 0,
                    f"Input '{input_text}': min LED index ({min_led}) >= 0"
                )
                self.assert_true(
                    max_led < 224,
                    f"Input '{input_text}': max LED index ({max_led}) < 224"
                )
                self.assert_true(
                    len(leds) == len(set(leds)),
                    f"Input '{input_text}': no duplicate LED indices"
                )
    
    
    def run_all_tests(self):
        """Run all test suites."""
        self.test_normalize_text()
        self.test_validate_text()
        self.test_get_segment_pattern()
        self.test_text_to_segments()
        self.test_segments_to_led_indices()
        self.test_display_text()
        self.test_led_index_ranges()
        
        # Print summary
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        total = self.passed + self.failed
        print(f"Passed: {self.passed}/{total}")
        print(f"Failed: {self.failed}/{total}")
        
        if self.failed == 0:
            print("\n✓ ALL TESTS PASSED")
        else:
            print(f"\n✗ {self.failed} TEST(S) FAILED")
        
        print("="*60 + "\n")
        
        return self.failed == 0


if __name__ == "__main__":
    tester = TestDisplay7Segment()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
