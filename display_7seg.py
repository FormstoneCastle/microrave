#!/usr/bin/env python3
"""
MicroRave 7-Segment Display Module (v0.0.1)

Pure data transformation: Convert 4-character text to LED indices.
No hardware control, no simulator communication.

Usage:
    from config import *
    from 7seg import Display7Segment
    
    display = Display7Segment()
    result = display.display_text("1234")
    # result['led_indices'] = [0, 1, 2, 3, ...]  # All LEDs to turn ON
    # result['text'] = "1234"
"""

MODULE_VERSION = "0.0.1"

import sys
from typing import Dict, List, Optional


class Display7Segment:
    """
    Convert 4-character text to LED indices for WS2812 strip.
    MVP: Logic only - no hardware or network calls.
    """
    
    def __init__(self, config=None):
        """
        Initialize Display7Segment.
        
        Args:
            config: Config module (imports DISPLAY_LED_MAP, SEGMENT_PATTERNS, SEGMENT_ORDER)
                   If None, imports config automatically.
        """
        if config is None:
            try:
                import config
            except ImportError:
                print("ERROR: config.py not found. Make sure it's in the same directory.")
                sys.exit(1)
        
        self.config = config
        self.led_map = config.DISPLAY_LED_MAP
        self.patterns = config.SEGMENT_PATTERNS
        self.segment_order = config.SEGMENT_ORDER
        self.current_display = "0000"
        
        # Map segment names to indices in the pattern array [a,b,c,d,e,f,g]
        self.pattern_indices = {
            'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4, 'f': 5, 'g': 6
        }
    
    
    def validate_text(self, text: str) -> bool:
        """
        Check if text contains only supported characters (0-9, colon).
        
        Args:
            text (str): Input text to validate
        
        Returns:
            bool: True if valid, False otherwise
        """
        if not text:
            return False
        
        for char in text:
            if char not in "0123456789:":
                return False
        
        return True
    
    
    def normalize_text(self, text: str) -> str:
        """
        Ensure exactly 4 digits (no colon, with padding if needed).
        
        Args:
            text (str): Input text (e.g., "1", "12:34", "123456")
        
        Returns:
            str: Exactly 4 digits (e.g., "0001", "1234", "1234")
        
        Examples:
            "1" -> "0001"
            "12:34" -> "1234"  (colon removed)
            "123456" -> "1234"  (truncated)
        """
        # Remove colons
        text = text.replace(":", "")
        
        # Pad with zeros on the left if less than 4 characters
        if len(text) < 4:
            text = text.rjust(4, "0")
        
        # Truncate to 4 characters if more than 4
        if len(text) > 4:
            text = text[:4]
        
        return text
    
    
    def get_segment_pattern(self, char: str) -> List[int]:
        """
        Get the 7-segment pattern for a character.
        
        Args:
            char (str): A single digit character (0-9)
        
        Returns:
            List[int]: [a, b, c, d, e, f, g] where 1=ON, 0=OFF
        
        Raises:
            KeyError: If character not found in SEGMENT_PATTERNS
        """
        if char not in self.patterns:
            raise KeyError(f"Character '{char}' not supported. Supported: {list(self.patterns.keys())}")
        
        return self.patterns[char]
    
    
    def text_to_segments(self, text: str) -> Dict[str, Dict[str, int]]:
        """
        Convert 4-character text to segment states for each character.
        
        Args:
            text (str): Input text (e.g., "1234")
        
        Returns:
            Dict: {
                'char_0': {'g': 1, 'b': 0, 'a': 1, 'f': 1, 'e': 0, 'd': 1, 'c': 0},
                'char_1': {...},
                'char_2': {...},
                'char_3': {...}
            }
        
        Raises:
            ValueError: If text contains unsupported characters after normalization
        """
        # Normalize to 4 digits
        text = self.normalize_text(text)
        
        segments = {}
        
        for char_num, digit in enumerate(text):
            if digit not in self.patterns:
                # Silently replace unsupported with '0'
                digit = '0'
            
            pattern = self.get_segment_pattern(digit)
            
            # Map pattern [a,b,c,d,e,f,g] to segment names
            segments[f'char_{char_num}'] = {
                'a': pattern[0],
                'b': pattern[1],
                'c': pattern[2],
                'd': pattern[3],
                'e': pattern[4],
                'f': pattern[5],
                'g': pattern[6],
            }
        
        return segments
    
    
    def segments_to_led_indices(self, segments: Dict[str, Dict[str, int]]) -> List[int]:
        """
        Convert segment states to LED indices that should be ON (flat list).
        
        Args:
            segments (dict): Output from text_to_segments()
        
        Returns:
            List[int]: All LED indices that should be ON, in order
                      Example: [0, 1, 2, 3, ..., 8, 9, ...]
        
        Note: Returns a flat list of all LED indices where segment is ON (1)
        """
        led_indices = []
        
        # Process each character in order
        for char_num in range(4):
            char_key = f'char_{char_num}'
            
            if char_key not in segments:
                continue
            
            char_segments = segments[char_key]
            
            # Process segments in the order they appear on the LED strip
            for segment_name in self.segment_order:
                # Check if this segment should be ON
                if segment_name in char_segments and char_segments[segment_name] == 1:
                    # Get the LED indices for this segment
                    led_indices_for_segment = self.led_map[char_key][segment_name]
                    led_indices.extend(led_indices_for_segment)
        
        # Return sorted, unique indices
        return sorted(list(set(led_indices)))
    
    
    def display_text(self, text: str) -> Dict:
        """
        Main public method: Convert text to LED indices.
        
        Args:
            text (str): Input text (e.g., "1234" or "12:34")
        
        Returns:
            Dict: {
                'text': "1234",           # Normalized/cleaned text
                'led_indices': [0, 1, 2, 3, ...],  # All LEDs to turn ON
                'segment_states': {...}  # Per-character segment details
            }
        
        Example:
            >>> display = Display7Segment()
            >>> result = display.display_text("12:34")
            >>> print(result['text'])
            '1234'
            >>> print(len(result['led_indices']))
            48  # Example: some LEDs ON
        """
        try:
            # Validate input (before normalization to catch colons)
            if not self.validate_text(text):
                # Silently replace with '0000'
                text = "0000"
            
            # Convert text to segment states
            segments = self.text_to_segments(text)
            
            # Convert segments to LED indices (flat list)
            led_indices = self.segments_to_led_indices(segments)
            
            # Get normalized text for reference
            normalized_text = self.normalize_text(text)
            self.current_display = normalized_text
            
            return {
                'text': normalized_text,
                'led_indices': led_indices,
                'segment_states': segments,
                'led_count': len(led_indices),
            }
        
        except Exception as e:
            print(f"ERROR in display_text('{text}'): {e}")
            # Return safe default: all zeros
            return {
                'text': "0000",
                'led_indices': [],
                'segment_states': {},
                'led_count': 0,
            }
    
    
    def get_current_display(self) -> str:
        """Get the currently displayed text."""
        return self.current_display
    
    
    def debug_print(self, result: Dict) -> None:
        """
        Pretty-print display result for debugging.
        
        Args:
            result (dict): Output from display_text()
        """
        print(f"\n{'='*60}")
        print(f"Display Text: {result['text']}")
        print(f"LEDs ON: {result['led_count']} / 224")
        print(f"LED Indices: {result['led_indices'][:20]}{'...' if len(result['led_indices']) > 20 else ''}")
        
        # Print per-character segment details
        print(f"\nSegment States:")
        for char_key in ['char_0', 'char_1', 'char_2', 'char_3']:
            if char_key in result['segment_states']:
                states = result['segment_states'][char_key]
                on_segments = [s for s, v in states.items() if v == 1]
                print(f"  {char_key}: {on_segments}")
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
    print(f"Testing 7seg.py v{MODULE_VERSION}")
    print(f"{'='*60}")
    
    display = Display7Segment(config)
    
    # Test cases
    test_cases = [
        "0000",
        "1234",
        "12:34",  # With colon
        "8888",
        "00:00",
        "1",      # Short input (should pad)
        "12345",  # Long input (should truncate)
    ]
    
    print("\nRunning tests...")
    for test_input in test_cases:
        result = display.display_text(test_input)
        print(f"\nInput: '{test_input}'")
        print(f"  → Text: '{result['text']}'")
        print(f"  → LEDs ON: {result['led_count']} / 224")
        print(f"  → First 20 indices: {result['led_indices'][:20]}")
    
    print(f"\n{'='*60}")
    print("✓ All tests completed")
    print(f"{'='*60}\n")
    
    return True


if __name__ == "__main__":
    # Run self-test
    test_module()
