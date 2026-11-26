"""
Zoey's voice (Step 4): ears and a mouth.

    mic -> Ears.wait_for_wake_word()   "Hey Zoey" (Porcupine) or press Enter
        -> Ears.listen()               record until you stop talking (WebRTC VAD)
        -> Ears.transcribe()           speech -> text (Whisper, runs locally)
        -> ...Zoey thinks, same agent loop as text mode...
        -> Mouth.say_streaming()       text -> her voice (ElevenLabs), streamed

The latency trick: Zoey's reply streams in word by word. As soon as a full
sentence arrives, it goes off to ElevenLabs while she keeps writing the next
one, and audio plays the moment it comes back. She starts talking about a
second after you stop, instead of waiting for the whole answer.

Setup (see README, Step 4):
    pip install -r requirements-voice.txt
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID            her voice
    PICOVOICE_ACCESS_KEY, PORCUPINE_KEYWORD_PATH       "Hey Zoey" (optional)
"""

import json
import os
import queue
import re
import threading
import urllib.error
import urllib.request

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
MIC_RATE = 16000            # Whisper, Porcupine and the VAD all want 16 kHz mono
SPEAKER_RATE = 24000        # ElevenLabs pcm_24000 output

# Whisper model size: tiny.en, base.en, small.en, medium.en, large-v3-turbo.
# Bigger = more accurate but slower. With an NVIDIA GPU, use large-v3-turbo.
WHISPER_MODEL = os.environ.get("ZOEY_WHISPER_MODEL", "small.en")

# ElevenLabs model. eleven_flash_v2_5 is fastest (best for conversation).
# eleven_multilingual_v2 sounds a bit richer but responds slower.
TTS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")

SILENCE_TO_STOP = 0.9       # seconds of quiet that means "I'm done talking"
MAX_RECORDING = 30          # seconds, hard cap on one utterance
VAD_AGGRESSIVENESS = 2      # 0-3; higher ignores more background noise


# ---------------------------------------------------------------------------
# EARS
# ---------------------------------------------------------------------------
class Ears:
    def __init__(self):
        import webrtcvad
        from faster_whisper import WhisperModel

        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.wake = self._load_wake_word()

        print(f"[loading speech recognition ({WHISPER_MODEL})... first run downloads it]")
        self.whisper = WhisperModel(WHISPER_MODEL, device="auto", compute_type="default")

        # The mic runs the whole time and drops 16-bit samples into a queue.
        self._mic_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._buffer = np.zeros(0, dtype=np.int16)
        self._stream = sd.InputStream(
            samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=480,
            callback=lambda data, *_: self._mic_queue.put(data[:, 0].copy()),
        )
        self._stream.start()

    # -- wake word ----------------------------------------------------------
    @staticmethod
    def _load_wake_word():
        access_key = os.environ.get("PICOVOICE_ACCESS_KEY")
        keyword_path = os.environ.get("PORCUPINE_KEYWORD_PATH")
        if not (access_key and keyword_path):
            return None
        import pvporcupine
        return pvporcupine.create(access_key=access_key, keyword_paths=[keyword_path])

    @property
    def hands_free(self) -> bool:
        return self.wake is not None

    def wait_for_wake_word(self) -> None:
        """Block until "Hey Zoey" (or Enter, in push-to-talk mode)."""
        if self.wake is None:
            input("[press Enter and talk] ")
        else:
            print('[sleeping... say "Hey Zoey"]')
            self.flush()
            for frame in self._frames(self.wake.frame_length):
                if self.wake.process(frame) >= 0:
                    break
        self.flush()

    # -- recording ----------------------------------------------------------
    def flush(self) -> None:
        """Throw away audio heard so far (e.g. Zoey's own voice)."""
        while not self._mic_queue.empty():
            self._mic_queue.get_nowait()
        self._buffer = np.zeros(0, dtype=np.int16)

    def _frames(self, size: int):
        """Yield fixed-size chunks of mic audio, however the mic delivers it."""
        while True:
            while len(self._buffer) < size:
                self._buffer = np.concatenate([self._buffer, self._mic_queue.get()])
            frame, self._buffer = self._buffer[:size], self._buffer[size:]
            yield frame

    def listen(self, wait_for_speech: float = 8.0) -> np.ndarray | None:
        """
        Record one utterance. Waits up to `wait_for_speech` seconds for you to
        start talking, then records until you've been quiet for SILENCE_TO_STOP.
        Returns None if you never said anything.
        """
        frame_ms = 30
        size = MIC_RATE * frame_ms // 1000          # 480 samples
        pre_roll = []                                # audio just before speech started
        voiced, silent_ms, waited_ms = [], 0, 0

        print("[listening...]")
        for frame in self._frames(size):
            is_speech = self.vad.is_speech(frame.tobytes(), MIC_RATE)
            if not voiced:
                pre_roll = (pre_roll + [frame])[-10:]   # keep ~0.3 s
                waited_ms += frame_ms
                if is_speech:
                    voiced = pre_roll[:]
                elif waited_ms > wait_for_speech * 1000:
                    return None
                continue
            voiced.append(frame)
            silent_ms = 0 if is_speech else silent_ms + frame_ms
            if silent_ms >= SILENCE_TO_STOP * 1000 or len(voiced) * frame_ms > MAX_RECORDING * 1000:
                break
        return np.concatenate(voiced)

    def transcribe(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(samples, language="en", beam_size=5)
        return "".join(s.text for s in segments).strip()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
        if self.wake is not None:
            self.wake.delete()


# ---------------------------------------------------------------------------
# MOUTH
# ---------------------------------------------------------------------------
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n+")


def clean_for_speech(text: str) -> str:
    """Strip things that sound awful read aloud: markdown, URLs, citations."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)     # [label](link) -> label
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


class Mouth:
    """
    Two background workers form a pipeline:
      sentences -> [synth thread: ElevenLabs] -> audio chunks -> [player thread] -> speaker
    so sentence 2 is being generated while sentence 1 is playing.
    """

    def __init__(self):
        self.api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
        if not (self.api_key and self.voice_id):
            raise RuntimeError("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID (see README, Step 4).")

        self._text_queue: queue.Queue[str] = queue.Queue()
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._pending = ""
        self._previous_sentence = ""
        self._stop = threading.Event()

        self._speaker = sd.RawOutputStream(samplerate=SPEAKER_RATE, channels=1, dtype="int16")
        self._speaker.start()
        threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._player_worker, daemon=True).start()

    # -- feeding text in ----------------------------------------------------
    def feed(self, text_piece: str) -> None:
        """Called with each streamed piece of her reply. Ships whole sentences."""
        self._pending += text_piece
        while True:
            match = _SENTENCE_END.search(self._pending)
            if not match:
                return
            sentence, self._pending = self._pending[:match.end()], self._pending[match.end():]
            self._queue_sentence(sentence)

    def break_sentence(self) -> None:
        """A new block of text is starting (e.g. after a tool call)."""
        if self._pending.rstrip().endswith((".", "!", "?", "…")):
            self._queue_sentence(self._pending)
            self._pending = ""
        elif self._pending and not self._pending.endswith(" "):
            self._pending += " "

    def finish(self) -> None:
        """Her reply is complete: speak whatever's left and wait until done."""
        self._queue_sentence(self._pending)
        self._pending = ""
        self._text_queue.join()
        self._audio_queue.join()

    def say(self, text: str) -> None:
        self.feed(text)
        self.finish()

    def stop(self) -> None:
        """Cut her off mid-sentence."""
        self._stop.set()
        self._pending = ""
        self._text_queue.join()
        self._audio_queue.join()
        self._stop.clear()

    def _queue_sentence(self, sentence: str) -> None:
        sentence = clean_for_speech(sentence)
        if sentence:
            self._text_queue.put(sentence)

    # -- workers ------------------------------------------------------------
    def _synth_worker(self) -> None:
        while True:
            sentence = self._text_queue.get()
            try:
                if not self._stop.is_set():
                    for chunk in self._synthesize(sentence):
                        if self._stop.is_set():
                            break
                        self._audio_queue.put(chunk)
                    self._previous_sentence = sentence
            except Exception as e:
                print(f"\n[!] Voice failed: {e}")
            finally:
                self._text_queue.task_done()

    def _player_worker(self) -> None:
        while True:
            chunk = self._audio_queue.get()
            try:
                if not self._stop.is_set():
                    self._speaker.write(chunk)
            finally:
                self._audio_queue.task_done()

    def _synthesize(self, sentence: str):
        """Stream raw 16-bit audio for one sentence from ElevenLabs."""
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
            f"?output_format=pcm_{SPEAKER_RATE}",
            data=json.dumps({
                "text": sentence,
                "model_id": TTS_MODEL,
                # Tells ElevenLabs what came before, so her tone flows
                # naturally from one sentence to the next.
                "previous_text": self._previous_sentence,
            }).encode(),
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"ElevenLabs error {e.code}: {detail}") from None

        leftover = b""
        with response:
            while chunk := response.read(4096):
                chunk = leftover + chunk
                cut = len(chunk) - (len(chunk) % 2)    # keep whole 16-bit samples
                chunk, leftover = chunk[:cut], chunk[cut:]
                if chunk:
                    yield chunk

    def close(self) -> None:
        self._speaker.stop()
        self._speaker.close()
