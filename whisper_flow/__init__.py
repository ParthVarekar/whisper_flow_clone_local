"""whisper-flow: a fully local speech-to-text + LLM processing pipeline.

Architecture (verified upstream, see ../ARCHITECTURE.md):
    audio (file/mic) -> whisper.cpp (whisper-cli) -> text + segments
                     -> llama.cpp (llama-server /v1/chat/completions) -> processed text

No cloud calls. No external model services. Runs entirely on-device.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
