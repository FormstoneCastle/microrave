#!/usr/bin/env python3
"""
Enttec Open DMX USB Controller for Raspberry Pi
Controls DMX512 lighting fixtures via USB interface
"""

import serial
import time
import sys
from typing import List, Optional
import struct


class DMXController:
    """Control DMX512 fixtures via Enttec Open DMX USB adapter"""
    
    # DMX Protocol constants
    BAUD_RATE = 250000
    START_CODE = 0x00
    MAX_CHANNELS = 512
    FRAME_BREAK_US = 110  # microseconds
    MARK_AFTER_BREAK_US = 16  # microseconds
    
    def __init__(self, port: Optional[str] = None):
        """
        Initialize DMX controller
        
        Args:
            port: Serial port (e.g., '/dev/ttyUSB0'). 
                  If None, attempts to find device automatically.
        """
        self.serial = None
        self.dmx_data = bytearray(513)  # 512 channels + start code
        self.dmx_data[0] = self.START_CODE
        
        if port is None:
            port = self._find_enttec_device()
        
        if port is None:
            raise RuntimeError(
                "Enttec Open DMX USB not found. "
                "Check connection and USB drivers."
            )
        
        self._connect(port)
    
    def _find_enttec_device(self) -> Optional[str]:
        """Auto-detect Enttec device on common ports"""
        import glob
        
        # Try common USB serial ports on Raspberry Pi
        possible_ports = [
            '/dev/ttyUSB0',
            '/dev/ttyUSB1',
            '/dev/ttyACM0',
            '/dev/ttyACM1',
        ]
        
        # Also check any ttyUSB* devices
        possible_ports.extend(glob.glob('/dev/ttyUSB*'))
        possible_ports.extend(glob.glob('/dev/ttyACM*'))
        
        for port in possible_ports:
            try:
                # Test connection at DMX baud rate
                test_serial = serial.Serial(port, self.BAUD_RATE, timeout=1)
                test_serial.close()
                print(f"Found device at {port}")
                return port
            except (serial.SerialException, OSError):
                continue
        
        return None
    
    def _connect(self, port: str) -> None:
        """Connect to the Enttec device"""
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=self.BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                stopbits=serial.STOPBITS_TWO,
                parity=serial.PARITY_NONE,
                timeout=1
            )
            print(f"Connected to {port}")
            time.sleep(0.5)  # Allow device to initialize
        except serial.SerialException as e:
            raise RuntimeError(f"Failed to connect to {port}: {e}")
    
    def set_channel(self, channel: int, value: int) -> None:
        """
        Set a single DMX channel value
        
        Args:
            channel: Channel number (1-512)
            value: Channel value (0-255)
        """
        if not 1 <= channel <= self.MAX_CHANNELS:
            raise ValueError(f"Channel must be 1-{self.MAX_CHANNELS}")
        
        if not 0 <= value <= 255:
            raise ValueError("Value must be 0-255")
        
        # DMX data array is 0-indexed, but channels are 1-indexed
        self.dmx_data[channel] = value
    
    def set_channels(self, values: List[int], start_channel: int = 1) -> None:
        """
        Set multiple consecutive DMX channels
        
        Args:
            values: List of values (0-255)
            start_channel: First channel to set (default 1)
        """
        for i, value in enumerate(values):
            self.set_channel(start_channel + i, value)
    
    def get_channel(self, channel: int) -> int:
        """Get current value of a DMX channel"""
        if not 1 <= channel <= self.MAX_CHANNELS:
            raise ValueError(f"Channel must be 1-{self.MAX_CHANNELS}")
        
        return self.dmx_data[channel]
    
    def clear(self) -> None:
        """Set all channels to 0"""
        for i in range(1, self.MAX_CHANNELS + 1):
            self.dmx_data[i] = 0
    
    def send(self) -> None:
        """Send current DMX frame to fixtures"""
        if self.serial is None or not self.serial.is_open:
            raise RuntimeError("Not connected to device")
        
        try:
            self.serial.write(self.dmx_data)
            self.serial.flush()
        except serial.SerialException as e:
            raise RuntimeError(f"Failed to send DMX data: {e}")
    
    def set_and_send(self, channel: int, value: int) -> None:
        """Set a channel and immediately send the frame"""
        self.set_channel(channel, value)
        self.send()
    
    def close(self) -> None:
        """Close the serial connection"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            print("Disconnected from DMX device")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def demo_rainbow(controller: DMXController, num_runs: int = 3):
    """Fade RGB light through rainbow colors"""
    print("\n=== Rainbow Demo ===")
    print("Fading through colors on channels 1-3 (R-G-B)")
    
    for run in range(num_runs):
        print(f"Run {run + 1}/{num_runs}")
        
        # Red to Green
        for i in range(256):
            controller.set_channels([255 - i, i, 0])
            controller.send()
            time.sleep(0.01)
        
        # Green to Blue
        for i in range(256):
            controller.set_channels([0, 255 - i, i])
            controller.send()
            time.sleep(0.01)
        
        # Blue to Red
        for i in range(256):
            controller.set_channels([i, 0, 255 - i])
            controller.send()
            time.sleep(0.01)
    
    controller.clear()
    controller.send()


def demo_chase(controller: DMXController, num_runs: int = 2):
    """Demonstrate channel chase effect"""
    print("\n=== Chase Demo ===")
    print("Chasing through 3 channels")
    
    channels = [1, 2, 3]
    
    for run in range(num_runs):
        print(f"Run {run + 1}/{num_runs}")
        
        for _ in range(3):
            for ch in channels:
                controller.clear()
                controller.set_channel(ch, 255)
                controller.send()
                time.sleep(0.3)
    
    controller.clear()
    controller.send()


def demo_dimmer(controller: DMXController, num_runs: int = 2):
    """Demonstrate dimming effect"""
    print("\n=== Dimmer Demo ===")
    print("Dimming channels 1-3")
    
    for run in range(num_runs):
        print(f"Run {run + 1}/{num_runs}")
        
        # Fade up
        for level in range(0, 256, 5):
            controller.set_channels([level, level, level])
            controller.send()
            time.sleep(0.02)
        
        # Fade down
        for level in range(255, -1, -5):
            controller.set_channels([level, level, level])
            controller.send()
            time.sleep(0.02)
    
    controller.clear()
    controller.send()


def interactive_mode(controller: DMXController):
    """Interactive channel control"""
    print("\n=== Interactive Mode ===")
    print("Commands:")
    print("  c [channel] [value] - Set channel (1-512) to value (0-255)")
    print("  s [ch1] [ch2] [ch3] - Set first 3 channels")
    print("  r - Rainbow demo")
    print("  d - Dimmer demo")
    print("  x - Chase demo")
    print("  c - Clear all")
    print("  q - Quit")
    
    try:
        while True:
            try:
                cmd = input("\n> ").strip().split()
                
                if not cmd:
                    continue
                
                if cmd[0].lower() == 'q':
                    break
                
                elif cmd[0].lower() == 'c' and len(cmd) == 3:
                    ch = int(cmd[1])
                    val = int(cmd[2])
                    controller.set_and_send(ch, val)
                    print(f"Set channel {ch} to {val}")
                
                elif cmd[0].lower() == 's' and len(cmd) == 4:
                    vals = [int(cmd[1]), int(cmd[2]), int(cmd[3])]
                    controller.set_channels(vals, start_channel=1)
                    controller.send()
                    print(f"Set channels 1-3 to {vals}")
                
                elif cmd[0].lower() == 'r':
                    demo_rainbow(controller, num_runs=1)
                
                elif cmd[0].lower() == 'd':
                    demo_dimmer(controller, num_runs=1)
                
                elif cmd[0].lower() == 'x':
                    demo_chase(controller, num_runs=1)
                
                elif cmd[0].lower() == 'c' and len(cmd) == 1:
                    controller.clear()
                    controller.send()
                    print("All channels cleared")
                
                else:
                    print("Invalid command")
            
            except (ValueError, IndexError) as e:
                print(f"Error: {e}")
    
    except KeyboardInterrupt:
        print("\nExiting...")


def main():
    """Main entry point"""
    print("DMX512 Controller for Enttec Open DMX USB")
    print("=" * 50)
    
    try:
        # Auto-detect device
        print("Connecting to Enttec Open DMX USB...")
        controller = DMXController()
        
        print("\nStarting demonstrations...")
        print("Press Ctrl+C to stop\n")
        
        with controller:
            # Run demos
            try:
                demo_dimmer(controller, num_runs=1)
                time.sleep(1)
                
                demo_chase(controller, num_runs=1)
                time.sleep(1)
                
                demo_rainbow(controller, num_runs=1)
                time.sleep(1)
                
                # Enter interactive mode
                interactive_mode(controller)
            
            except KeyboardInterrupt:
                print("\nStopping demos...")
                controller.clear()
                controller.send()
    
    except RuntimeError as e:
        print(f"Error: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure Enttec Open DMX USB is connected via USB")
        print("2. Install FTDI drivers if not already installed")
        print("3. Check device permissions: ls -la /dev/ttyUSB*")
        print("4. Try: sudo chmod 666 /dev/ttyUSB0")
        sys.exit(1)


if __name__ == "__main__":
    main()
