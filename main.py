#!/usr/bin/env python3
"""
MicroRave Main Program
Coordinates all modules (buttons, display, DMX, power) and communicates with the simulator GUI.

This is the central hub that:
1. Initializes all hardware modules
2. Runs the main event loop
3. Handles button input events
4. Updates the display
5. Communicates with the Windows simulator via WebSocket
6. Manages DMX lighting output
7. Controls power relay

Usage:
    python3 main.py
"""

import json
import time
import logging
import asyncio
import threading
from datetime import datetime
from typing import Dict, Optional

# MicroRave modules
from config import BUTTON_PINS, SWITCH_PIN, SWITCH_NAME, SIMULATOR_HOST, SIMULATOR_PORT
from config import SOUNDS_DIR, MUSIC_DIR, DEFAULT_VOLUME, BEEP_SOUND, DING_SOUND, EASTER_EGG_TIMES
from button_input import ButtonInput, ButtonState
from display_7seg import Display7Segment, Color
from dmx512_output import DMX512
from power_control import PowerControl
from audio_playback import AudioPlayer
from dmx_lighting import DMXLighting
import dmx_config

# Web server for simulator communication
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("⚠ websockets not available - simulator communication disabled")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class MicroRave:
    """
    Main MicroRave controller.
    Coordinates all modules and manages communication.
    """

    def __init__(self):
        """Initialize all modules"""
        logger.info("=" * 60)
        logger.info("MICRORAVE - MAIN PROGRAM v0.2.14")
        logger.info("=" * 60)

        # Module initialization
        logger.info("Initializing modules...")
        self.buttons = ButtonInput(
            button_pins=BUTTON_PINS,
            switch_pin=SWITCH_PIN,
            switch_name=SWITCH_NAME,
            use_mock=False  # Set to True for testing without Pi
        )

        self.display = Display7Segment(use_mock=False)
        self.dmx = DMX512(use_mock=False)
        self.power = PowerControl(use_mock=False)
        
        # Audio player
        self.audio = AudioPlayer(
            sounds_dir=SOUNDS_DIR,
            music_dir=MUSIC_DIR,
            default_volume=DEFAULT_VOLUME
        )
        self.selected_dj = 1  # Default to DJ1
        self.door_is_open = False  # Track door state
        
        # Import version constant to log
        from audio_playback import AUDIO_MODULE_VERSION
        logger.info(f"✅ Audio Module v{AUDIO_MODULE_VERSION} loaded")
        
        # DMX lighting controller
        self.dmx_lights = DMXLighting(dmx_config=dmx_config, use_mock=False)
        self.music_playing = False  # Track if music is currently playing
        
        # Import version constant to log
        from dmx_lighting import DMX_MODULE_VERSION
        logger.info(f"✅ DMX Module v{DMX_MODULE_VERSION} loaded")

        # State
        self.running = True
        self.connected_clients = set()
        self.last_button_states = {}
        self.display_text = "8888"
        self.dmx_active_channels = []

        # Register button callbacks
        for pin in BUTTON_PINS.keys():
            self.buttons.register_press_callback(pin, self.on_button_pressed)
            self.buttons.register_release_callback(pin, self.on_button_released)

        # Register power callback
        self.power.register_power_change_callback(self.on_power_change)

        # WebSocket server
        self.ws_server = None
        self.ws_port = SIMULATOR_PORT
        self.ws_thread = None

        logger.info("✓ All modules initialized")
        logger.info(f"✓ Ready for operation")

        # Initial display
        self.set_display("REDY")
        
        # Run DMX startup test
        self.dmx_lights._startup_test()

    def on_button_pressed(self, event):
        """Handle button press event"""
        logger.debug(f"→ PRESSED: Button {event.button_id} ({event.button_name})")
        
        # Play beep sound for all button presses
        self.audio.play_sound(BEEP_SOUND, self.audio.get_current_volume())
        logger.info(f"🔘 Button '{event.button_name}' - beep triggered")
        
        # Handle specific button functions (case-insensitive)
        button_name = event.button_name.upper()
        
        # DJ Selection (DJ1-DJ6)
        if button_name.startswith('DJ'):
            try:
                dj_num = int(button_name[2])
                if 1 <= dj_num <= 6:
                    self.selected_dj = dj_num
                    logger.info(f"🎛️ DJ{dj_num} selected")
            except (ValueError, IndexError):
                pass
        
        # Volume Control
        elif button_name == 'VOLUP':
            new_volume = min(100, self.audio.get_current_volume() + 10)
            self.audio.set_volume(new_volume)
            logger.info(f"🔊 Volume UP → {new_volume}%")
        
        elif button_name == 'VOLDN':
            new_volume = max(0, self.audio.get_current_volume() - 10)
            self.audio.set_volume(new_volume)
            logger.info(f"🔊 Volume DOWN → {new_volume}%")
        
        # Start Button
        elif button_name == 'START':
            logger.info(f"▶ START pressed - Playing DJ{self.selected_dj} music")
            self.audio.play_playlist_random(self.selected_dj, self.audio.get_current_volume())
        
        # Cancel Button
        elif button_name == 'CANCEL':
            logger.info(f"⏹ CANCEL pressed - Stopping music")
            self.audio.stop_music()
        
        # Broadcast to simulator
        self.broadcast({
            "type": "button_press",
            "button_id": event.button_id,
            "button_name": event.button_name,
            "timestamp": time.time()
        })

    def on_button_released(self, event):
        """Handle button release event"""
        logger.debug(f"← RELEASED: Button {event.button_id} ({event.button_name})")
        self.broadcast({
            "type": "button_release",
            "button_id": event.button_id,
            "button_name": event.button_name,
            "timestamp": time.time()
        })

    def on_power_change(self, state: bool):
        """Handle power state change"""
        status = "ON" if state else "OFF"
        logger.info(f"🔌 Power: {status}")
        self.broadcast({
            "type": "power_change",
            "state": state,
            "timestamp": time.time()
        })

    def on_door_state_change(self, is_open: bool):
        """Handle door open/close event"""
        self.door_is_open = is_open
        status = "OPEN" if is_open else "CLOSED"
        logger.info(f"🚪 Door: {status}")
        
        if is_open:
            # Door opened - stop music and play ding sound
            logger.info(f"🚪 Door opened - stopping music")
            self.audio.stop_music()
            self.audio.play_sound(DING_SOUND, self.audio.get_current_volume())
            # Turn off DMX lights
            self.music_playing = False
            self.dmx_lights.turn_off()
        
        # Broadcast to simulator
        self.broadcast({
            "type": "door_change",
            "state": status,
            "timestamp": time.time()
        })

    def set_display(self, text: str, color: Optional[Color] = None):
        """Update 7-segment display"""
        self.display_text = str(text)[:4].ljust(4, " ").upper()
        if color is None:
            color = Color.white()
        self.display.display(self.display_text, [color] * 4)

        self.broadcast({
            "type": "display_update",
            "text": self.display_text,
            "timestamp": time.time()
        })

    def set_dmx_channel(self, channel: int, value: int):
        """Set DMX channel and broadcast"""
        self.dmx.set_channel(channel, value)
        
        if value > 0 and channel not in self.dmx_active_channels:
            self.dmx_active_channels.append(channel)
        elif value == 0 and channel in self.dmx_active_channels:
            self.dmx_active_channels.remove(channel)

        self.broadcast({
            "type": "dmx_update",
            "channel": channel,
            "value": value,
            "active_channels": len(self.dmx_active_channels),
            "timestamp": time.time()
        })

    def broadcast(self, message: Dict):
        """Send message to all connected simulators"""
        if not WEBSOCKETS_AVAILABLE or not self.connected_clients:
            return

        async def send_to_all():
            disconnected = set()
            for client in self.connected_clients:
                try:
                    await client.send(json.dumps(message))
                except Exception as e:
                    logger.warning(f"Failed to send to client: {e}")
                    disconnected.add(client)
            
            for client in disconnected:
                self.connected_clients.discard(client)

        # Schedule the async function
        if self.ws_server:
            asyncio.run_coroutine_threadsafe(send_to_all(), self.ws_server.loop)

    async def handle_simulator_message(self, websocket, message_str: str):
        """Handle incoming message from simulator"""
        try:
            message = json.loads(message_str)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from simulator: {message_str}")
            return

        msg_type = message.get("type")

        if msg_type == "button_press":
            button_name = message.get("button_name", "Unknown")
            logger.info(f"📡 Simulator: Button '{button_name}' pressed")
            
            # Play beep sound for all button presses
            self.audio.play_sound(BEEP_SOUND, self.audio.get_current_volume())
            logger.info(f"🔘 Button '{button_name}' - beep triggered")
            
            # Handle specific button functions (case-insensitive)
            button_name_upper = button_name.upper()
            
            # DJ Selection (DJ1-DJ6)
            if button_name_upper.startswith('DJ'):
                try:
                    dj_num = int(button_name_upper[2])
                    if 1 <= dj_num <= 6:
                        self.selected_dj = dj_num
                        logger.info(f"🎛️ DJ{dj_num} selected")
                except (ValueError, IndexError):
                    pass
            
            # Volume Control
            elif button_name_upper == 'VOLUP':
                new_volume = min(100, self.audio.get_current_volume() + 10)
                self.audio.set_volume(new_volume)
                logger.info(f"🔊 Volume UP → {new_volume}%")
            
            elif button_name_upper == 'VOLDN':
                new_volume = max(0, self.audio.get_current_volume() - 10)
                self.audio.set_volume(new_volume)
                logger.info(f"🔊 Volume DOWN → {new_volume}%")
            
            # Start Button
            elif button_name_upper == 'START':
                logger.info(f"▶ START pressed - Playing DJ{self.selected_dj} music")
                self.audio.play_playlist_random(self.selected_dj, self.audio.get_current_volume())
                # Start DMX light pattern for music (not easter eggs)
                self.music_playing = True
                self.dmx_lights.start_music_pattern()
            
            # Cancel Button
            elif button_name_upper == 'CANCEL':
                logger.info(f"⏹ CANCEL pressed - Stopping music")
                self.audio.stop_music()
                # Turn off DMX lights
                self.music_playing = False
                self.dmx_lights.turn_off()

        elif msg_type == "button_release":
            button_name = message.get("button_name", "Unknown")
            logger.info(f"📡 Simulator: Button '{button_name}' released")

        elif msg_type == "display_set":
            text = message.get("text", "")
            self.set_display(text)

        elif msg_type == "dmx_set":
            channel = message.get("channel")
            value = message.get("value")
            if channel is not None and value is not None:
                self.set_dmx_channel(channel, value)

        elif msg_type == "power_toggle":
            self.power.toggle()

        elif msg_type == "ping":
            await websocket.send(json.dumps({
                "type": "pong",
                "timestamp": time.time()
            }))

        elif msg_type == "exit":
            logger.info("📡 Simulator requested shutdown...")
            try:
                await websocket.send(json.dumps({
                    "type": "shutdown_ack",
                    "message": "Shutting down gracefully",
                    "timestamp": time.time()
                }))
            except Exception as e:
                logger.debug(f"Failed to send shutdown_ack: {e}")
            # Stop the main loop
            self.running = False
        
        elif msg_type == "door_change":
            door_open = message.get("door_open", False)
            self.on_door_state_change(door_open)
        
        elif msg_type == "simulator_info":
            simulator_version = message.get("version", "unknown")
            logger.info(f"📡 Simulator Info: Version {simulator_version}")

    async def websocket_handler(self, websocket):
        """Handle WebSocket connections from simulator"""
        self.connected_clients.add(websocket)
        try:
            client_addr = websocket.remote_address
        except:
            client_addr = "unknown"
        logger.info(f"📡 Simulator connected: {client_addr}")

        # Send welcome message
        try:
            await websocket.send(json.dumps({
                "type": "welcome",
                "message": "Connected to MicroRave",
                "version": "0.2.3",
                "timestamp": time.time()
            }))
        except:
            pass

        try:
            async for message in websocket:
                await self.handle_simulator_message(websocket, message)
        except Exception as e:
            logger.debug(f"Connection error: {e}")
        finally:
            self.connected_clients.discard(websocket)
            logger.info(f"📡 Simulator disconnected: {client_addr}")

    def start_websocket_server(self):
        """Start WebSocket server in background thread"""
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("WebSocket support disabled (install 'websockets' package)")
            return

        async def run_server():
            logger.info(f"🌐 WebSocket server starting on ws://0.0.0.0:{self.ws_port}")
            async with websockets.serve(self.websocket_handler, "0.0.0.0", self.ws_port):
                logger.info(f"✓ WebSocket server ready for simulator connections")
                await asyncio.Future()  # Run forever

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_server())
            except KeyboardInterrupt:
                pass
            finally:
                loop.close()

        self.ws_thread = threading.Thread(target=run_in_thread, daemon=True)
        self.ws_thread.start()

    def run_console_listener(self):
        """Listen for console input commands"""
        while self.running:
            try:
                user_input = input()
                if user_input.lower().strip() == 'exit':
                    logger.info("📝 Exit command received from console")
                    self.running = False
                    break
            except EOFError:
                # End of input stream
                break
            except KeyboardInterrupt:
                # Ctrl+C from console
                self.running = False
                break
            except Exception as e:
                logger.debug(f"Console input error: {e}")
                break

    def update_loop(self):
        """Main event loop"""
        logger.info("🎛️ Starting main event loop...")
        logger.info("Press Ctrl+C or type 'exit' to shutdown")

        # Start console listener in background thread
        console_thread = threading.Thread(target=self.run_console_listener, daemon=True)
        console_thread.start()

        update_rate = 0.01  # 100 Hz
        stats_interval = 5  # Print stats every 5 seconds
        last_stats = time.time()

        try:
            while self.running:
                # Poll buttons
                self.buttons.update()

                # Check for periodic tasks
                current_time = time.time()
                if current_time - last_stats >= stats_interval:
                    button_count = sum(1 for b in self.buttons.get_all_states().values()
                                      if b == ButtonState.PRESSED)
                    if button_count > 0 or len(self.connected_clients) > 0:
                        logger.debug(f"Active: {button_count} button(s), "
                                    f"{len(self.connected_clients)} simulator(s), "
                                    f"{len(self.dmx_active_channels)} DMX channel(s)")
                    last_stats = current_time

                time.sleep(update_rate)

        except KeyboardInterrupt:
            logger.info("\n⚠ Ctrl+C received")
            self.shutdown()
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            self.shutdown()

    def shutdown(self):
        """Clean shutdown with proper resource cleanup"""
        self.running = False
        logger.info("\n⚠ Shutdown initiated...")
        logger.info("Closing connections...")

        # Close WebSocket connections
        if self.ws_server:
            try:
                logger.info("  • Closing WebSocket connections...")
                for client in list(self.connected_clients):
                    try:
                        asyncio.run_coroutine_threadsafe(client.close(), self.ws_server.loop)
                    except Exception as e:
                        logger.debug(f"    Error closing client: {e}")
                self.connected_clients.clear()
            except Exception as e:
                logger.warning(f"  Error during WebSocket cleanup: {e}")

        # Clean up hardware modules
        try:
            logger.info("  • Cleaning up button input...")
            self.buttons.cleanup()
        except Exception as e:
            logger.warning(f"  Error cleaning buttons: {e}")

        try:
            logger.info("  • Shutting down display...")
            self.display.shutdown()
        except Exception as e:
            logger.warning(f"  Error shutting down display: {e}")

        try:
            logger.info("  • Cleaning up DMX512...")
            self.dmx.cleanup()
        except Exception as e:
            logger.warning(f"  Error cleaning DMX: {e}")

        try:
            logger.info("  • Cleaning up power control...")
            self.power.cleanup()
        except Exception as e:
            logger.warning(f"  Error cleaning power: {e}")
        
        try:
            logger.info("  • Cleaning up audio...")
            self.audio.cleanup()
        except Exception as e:
            logger.warning(f"  Error cleaning audio: {e}")
        
        try:
            logger.info("  • Cleaning up DMX lighting...")
            self.dmx_lights.cleanup()
        except Exception as e:
            logger.warning(f"  Error cleaning DMX: {e}")

        logger.info("✓ All resources cleaned up")
        logger.info("=" * 60)
        logger.info("Shutdown complete - goodbye!")
        logger.info("=" * 60)


def cleanup_old_processes():
    """Kill any old MicroRave processes and clear ports"""
    import subprocess
    import time
    import os
    
    logger.info("Checking for old processes...")
    try:
        current_pid = os.getpid()
        # Find all Python processes running main.py except current process
        result = subprocess.run(
            ["pgrep", "-f", "python3.*main.py"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.stdout:
            pids = result.stdout.strip().split('\n')
            killed_count = 0
            for pid in pids:
                try:
                    pid_int = int(pid)
                    if pid_int != current_pid:  # Don't kill ourselves!
                        subprocess.run(["kill", "-9", str(pid_int)], timeout=2)
                        killed_count += 1
                except (ValueError, subprocess.TimeoutExpired):
                    pass
            
            if killed_count > 0:
                logger.info(f"  ✓ Cleaned up {killed_count} old process(es)")
                time.sleep(1)  # Give port time to be released
            else:
                logger.info("  • No old processes found")
        else:
            logger.info("  • No old processes found")
    except Exception as e:
        logger.debug(f"Process cleanup check: {e}")


def main():
    """Main entry point"""
    cleanup_old_processes()
    microrave = MicroRave()

    # Start WebSocket server for simulator
    microrave.start_websocket_server()

    # Run main event loop
    try:
        microrave.update_loop()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        microrave.shutdown()


if __name__ == "__main__":
    main()
