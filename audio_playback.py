#!/usr/bin/env python3
"""
MicroRave Audio Playback Module
Handles HDMI audio output, sound effects, and music playlist playback.

v0.0.6 - Force lowercase filenames
- All music and sound file references converted to lowercase
- Handles mixed-case filenames transparently
- More reliable file matching across platforms

v0.0.5 - Remove mock mode completely
- Removed use_mock parameter entirely
- Simplified class to real audio only
- Cleaner API

v0.0.4 - MP3 support via ffplay
- Added ffplay subprocess backend for reliable MP3 playback
- Pygame.mixer for sound effects (beep/ding)
- Removed mock mode test
- Real audio hardware testing

v0.0.3 - SDL auto-detect audio driver
- Changed mixer to use SDL auto-detect instead of forcing specific driver
- More reliable on Pi 5 (driver auto-detection works better than forcing)

v0.0.2 - Case sensitivity fix
- Fixed folder name case (dj1-dj6 lowercase)

v0.0.1 - Initial implementation
- pygame.mixer backend for HDMI audio
- Background thread for playlist looping
- DJ1-DJ6 playlist support

Usage:
    audio = AudioPlayer(
        sounds_dir=Path("./sounds"),
        music_dir=Path("./music"),
        default_volume=50
    )
    audio.play_playlist_random(dj_num=1, volume=70)
    audio.stop_music()
    audio.cleanup()
"""

import logging
import threading
import time
import random
import subprocess
from pathlib import Path
from typing import Optional, List

# Audio backend
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("⚠ pygame not available - sound effects disabled")

# Check for ffplay
try:
    subprocess.run(["ffplay", "-version"], capture_output=True, timeout=2)
    FFPLAY_AVAILABLE = True
except (FileNotFoundError, subprocess.TimeoutExpired):
    FFPLAY_AVAILABLE = False
    print("⚠ ffplay not available - music playback disabled")

# Version constant
AUDIO_MODULE_VERSION = "0.0.6"

# Configure logging
logger = logging.getLogger(__name__)


class AudioPlayer:
    """
    Manages audio playback for MicroRave.
    
    Features:
    - HDMI audio output to Raspberry Pi
    - MP3 music playback via ffplay (reliable MP3 support)
    - Sound effects via pygame.mixer (beep, ding, etc.)
    - Music playlist playback with looping (DJ1-DJ6)
    - Background thread for non-blocking playback
    - Case-insensitive file handling (forces lowercase)
    """

    def __init__(self, sounds_dir: Path, music_dir: Path, default_volume: int = 50):
        """
        Initialize AudioPlayer.
        
        Args:
            sounds_dir: Path to sounds directory (beep, ding, etc.)
            music_dir: Path to music directory with dj1-dj6 subfolders
            default_volume: Default volume level (0-100)
        """
        self.sounds_dir = Path(sounds_dir)
        self.music_dir = Path(music_dir)
        self.current_volume = default_volume
        self.mixer_initialized = False
        
        # Playback state
        self.current_track = None
        self.is_playing = False
        self.current_playlist = []
        self.playlist_index = 0
        
        # ffplay subprocess for music
        self.music_process = None
        
        # Threading
        self.playback_thread = None
        self.running = True
        
        logger.info(f"🎵 Audio Module v{AUDIO_MODULE_VERSION} initializing...")
        
        # Initialize mixer for sound effects
        self._initialize_mixer()
        
        # Start background playback thread
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()
        logger.info("✓ Audio module ready")

    def _initialize_mixer(self):
        """Initialize pygame.mixer with SDL auto-detecting audio driver"""
        if not PYGAME_AVAILABLE:
            logger.warning("⚠ pygame not available - sound effects disabled")
            return
        
        try:
            # Import config to get audio settings
            from config import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS
            
            # Let SDL auto-detect the best audio driver (more reliable on Pi 5)
            pygame.mixer.init(
                frequency=AUDIO_SAMPLE_RATE,
                size=-16,  # 16-bit samples
                channels=AUDIO_CHANNELS,
                buffer=4096
            )
            self.mixer_initialized = True
            logger.info(f"   ✓ pygame.mixer initialized (SDL auto-detect)")
            logger.info(f"   ✓ Sample rate: {AUDIO_SAMPLE_RATE} Hz")
            logger.info(f"   ✓ Channels: {AUDIO_CHANNELS}")
            
        except Exception as e:
            logger.warning(f"⚠ Failed to initialize mixer: {e}")
            self.mixer_initialized = False

    def _playback_loop(self):
        """Background thread loop for playlist management"""
        logger.debug("🔄 Playback thread started")
        
        while self.running:
            try:
                # Check if current music process has finished
                if self.is_playing and self.music_process:
                    if self.music_process.poll() is not None:
                        # Process finished, play next track
                        self._play_next_track()
                
                time.sleep(0.5)  # Check every 500ms
                
            except Exception as e:
                logger.error(f"❌ Playback thread error: {e}")
                time.sleep(1)
        
        logger.debug("🔄 Playback thread stopped")

    def _play_next_track(self):
        """Play the next track in current playlist using ffplay"""
        if not self.current_playlist:
            self.is_playing = False
            return
        
        # Get next track (loop if at end)
        if self.playlist_index >= len(self.current_playlist):
            self.playlist_index = 0
            # Shuffle for variety on repeat
            random.shuffle(self.current_playlist)
        
        track = self.current_playlist[self.playlist_index]
        self.playlist_index += 1
        
        try:
            if not FFPLAY_AVAILABLE:
                logger.warning("⚠ ffplay not available - cannot play music")
                return
            
            # Stop previous process if still running
            if self.music_process:
                try:
                    self.music_process.terminate()
                    self.music_process.wait(timeout=1)
                except:
                    pass
            
            # Start ffplay process with volume control
            self.music_process = subprocess.Popen(
                [
                    "ffplay",
                    "-nodisp",           # No display window
                    "-autoexit",         # Exit when done
                    "-volume", str(self.current_volume),  # Volume 0-100
                    str(track)
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            self.current_track = track.name
            self.is_playing = True
            logger.info(f"▶ Now playing: {track.name}")
            
        except Exception as e:
            logger.warning(f"⚠ Failed to play {track.name}: {e}")
            # Try next track
            self._play_next_track()

    def play_playlist_random(self, dj_num: int, volume: int):
        """
        Play random music from dj{N} folder on loop.
        
        Args:
            dj_num: DJ number (1-6)
            volume: Volume level (0-100)
        """
        self.set_volume(volume)
        
        # Find DJ folder (lowercase: dj1, dj2, etc.)
        dj_folder = self.music_dir / f"dj{dj_num}"
        
        if not dj_folder.exists():
            logger.warning(f"⚠ dj{dj_num} folder not found: {dj_folder}")
            return
        
        # Scan for .mp3 files (all lowercase)
        mp3_files = list(dj_folder.glob("*.mp3"))
        
        # Shuffle playlist
        self.current_playlist = mp3_files.copy()
        random.shuffle(self.current_playlist)
        self.playlist_index = 0
        
        logger.info(f"🎛️ DJ{dj_num} playlist loaded ({len(mp3_files)} tracks)")
        
        # Play first track
        self._play_next_track()

    def play_sound(self, filename: str, volume: int):
        """
        Play a sound effect (beep, ding, etc.).
        Non-blocking - plays immediately in background.
        Filename is converted to lowercase for consistency.
        
        Args:
            filename: Sound file name (e.g., "beep.mp3")
            volume: Volume level (0-100)
        """
        # Force lowercase for file consistency
        filename_lower = filename.lower()
        sound_file = self.sounds_dir / filename_lower
        
        if not sound_file.exists():
            logger.warning(f"⚠ Sound file not found: {sound_file}")
            return
        
        try:
            if self.mixer_initialized:
                # Load and play on a separate channel (don't interrupt music)
                sound = pygame.mixer.Sound(str(sound_file))
                sound.set_volume(volume / 100.0)
                sound.play()
                logger.debug(f"🔊 Playing sound: {filename_lower}")
            else:
                logger.warning("⚠ Mixer not initialized - cannot play sound")
        
        except Exception as e:
            logger.warning(f"⚠ Failed to play sound {filename}: {e}")

    def stop_music(self):
        """Stop current music playback"""
        if self.music_process:
            try:
                self.music_process.terminate()
                self.music_process.wait(timeout=2)
            except:
                try:
                    self.music_process.kill()
                except:
                    pass
            self.music_process = None
        
        self.is_playing = False
        self.current_track = None
        self.current_playlist = []
        logger.info("⏹ Music stopped")

    def set_volume(self, level: int):
        """
        Set volume level (0-100).
        Affects newly started tracks.
        
        Args:
            level: Volume level (0-100)
        """
        self.current_volume = max(0, min(100, level))
        logger.debug(f"🔊 Volume set to {self.current_volume}%")

    def get_current_volume(self) -> int:
        """Return current volume level (0-100)"""
        return self.current_volume

    def cleanup(self):
        """Clean shutdown of audio module"""
        logger.info("🎵 Cleaning up audio module...")
        
        # Stop music playback
        self.stop_music()
        
        # Shut down mixer
        if self.mixer_initialized:
            try:
                pygame.mixer.quit()
                logger.info("   ✓ Mixer shut down")
            except Exception as e:
                logger.warning(f"  ⚠ Error shutting down mixer: {e}")
        
        # Stop playback thread
        self.running = False
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2)
            logger.info("   ✓ Playback thread stopped")


if __name__ == "__main__":
    """Real audio test - plays actual music files"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    
    print("\n" + "="*60)
    print("AUDIO PLAYBACK MODULE TEST (REAL AUDIO)")
    print("="*60 + "\n")
    
    audio = AudioPlayer(
        sounds_dir=Path("./sounds"),
        music_dir=Path("./music"),
        default_volume=50
    )
    
    print("\n[TEST] Starting DJ1 playlist...")
    audio.play_playlist_random(dj_num=1, volume=70)
    time.sleep(3)
    
    print("\n[TEST] Setting volume to 100...")
    audio.set_volume(100)
    time.sleep(2)
    
    print("\n[TEST] Playing sound effect...")
    audio.play_sound("beep.mp3", 80)
    time.sleep(2)
    
    print("\n[TEST] Stopping music...")
    audio.stop_music()
    time.sleep(1)
    
    print("\n[TEST] Cleaning up...")
    audio.cleanup()
    
    print("\n✓ Test complete\n")
