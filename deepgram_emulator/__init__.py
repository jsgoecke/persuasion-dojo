"""
Local Deepgram API emulator for CI and no-network testing.

Replays pre-recorded fixture responses matching the real Deepgram API
contract. Supports both WebSocket streaming and REST file processing.
"""

from deepgram_emulator.server import DeepgramEmulator

__all__ = ["DeepgramEmulator"]
