"""Hermes MQTT server for Rhasspy wakeword with snowboy"""
import io
import json
import logging
import subprocess
import typing
import wave
from pathlib import Path

import attr
from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.wake import (
    HotwordDetected,
    HotwordError,
    HotwordToggleOff,
    HotwordToggleOn,
)

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------


@attr.s(auto_attribs=True, slots=True)
class SnowboyModel:
    """Settings for a single snowboy model"""

    model_path: Path
    sensitivity: str = "0.5"
    audio_gain: float = 1.0
    apply_frontend: bool = False


# -----------------------------------------------------------------------------


class WakeHermesMqtt:
    """Hermes MQTT server for Rhasspy wakeword with snowboy."""

    def __init__(
        self,
        client,
        models: typing.List[SnowboyModel],
        wakeword_ids: typing.List[str],
        siteIds: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        chunk_size: int = 960,
    ):
        self.client = client
        self.models = models
        self.wakeword_ids = wakeword_ids
        self.siteIds = siteIds or []
        self.enabled = enabled

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.chunk_size = chunk_size

        # Topics to listen for WAV chunks on
        self.audioframe_topics: typing.List[str] = []
        for siteId in self.siteIds:
            self.audioframe_topics.append(AudioFrame.topic(siteId=siteId))

        self.first_audio: bool = True

        self.audio_buffer = bytes()

        # Load detector
        self.detectors: typing.List[typing.Any] = []
        self.model_ids: typing.List[str] = []

    # -------------------------------------------------------------------------

    def load_detectors(self):
        """Load snowboy detectors from models"""
        from snowboy import snowboydecoder, snowboydetect

        self.model_ids = []
        self.detectors = []

        for model in self.models:
            assert model.model_path.is_file(), f"Missing {model.model_path}"
            _LOGGER.debug("Loading snowboy model: %s", model)

            detector = snowboydetect.SnowboyDetect(
                snowboydecoder.RESOURCE_FILE.encode(), str(model.model_path).encode()
            )

            detector.SetSensitivity(model.sensitivity.encode())
            detector.SetAudioGain(model.audio_gain)
            detector.ApplyFrontend(model.apply_frontend)

            self.detectors.append(detector)
            self.model_ids = model.model_path.stem

    # -------------------------------------------------------------------------

    def handle_audio_frame(
        self, wav_bytes: bytes, siteId: str = "default"
    ) -> typing.Iterable[
        typing.Tuple[str, typing.Union[HotwordDetected, HotwordError]]
    ]:
        """Process a single audio frame"""
        if not self.detectors:
            self.load_detectors()

        # Extract/convert audio data
        audio_data = self.maybe_convert_wav(wav_bytes)

        # Add to persistent buffer
        self.audio_buffer += audio_data

        # Process in chunks.
        # Any remaining audio data will be kept in buffer.
        while len(self.audio_buffer) >= self.chunk_size:
            chunk = self.audio_buffer[: self.chunk_size]
            self.audio_buffer = self.audio_buffer[self.chunk_size :]

            for detector_index, detector in enumerate(self.detectors):
                # Return is:
                # -2 silence
                # -1 error
                #  0 voice
                #  n index n-1
                result_index = detector.RunDetection(chunk)

                if result_index > 0:
                    # Detection
                    if detector_index < len(self.wakeword_ids):
                        wakewordId = self.wakeword_ids[detector_index]
                    else:
                        wakewordId = "default"

                    yield (
                        wakewordId,
                        self.handle_detection(detector_index, siteId=siteId),
                    )
                    break

    def handle_detection(
        self, model_index, siteId="default"
    ) -> typing.Union[HotwordDetected, HotwordError]:
        """Handle a successful hotword detection"""
        try:
            assert len(self.model_ids) > model_index, f"Missing {model_index} in models"

            return HotwordDetected(
                siteId=siteId,
                modelId=self.model_ids[model_index],
                currentSensitivity=self.models[model_index].sensitivity,
                modelVersion="",
                modelType="personal",
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            return HotwordError(error=str(e), context=str(model_index), siteId=siteId)

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
            topics = [HotwordToggleOn.topic(), HotwordToggleOff.topic()]

            if self.audioframe_topics:
                # Specific siteIds
                topics.extend(self.audioframe_topics)
            else:
                # All siteIds
                topics.append(AudioFrame.topic(siteId="+"))

            for topic in topics:
                self.client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)
        except Exception:
            _LOGGER.exception("on_connect")

    def on_message(self, client, userdata, msg):
        """Received message from MQTT broker."""
        try:
            if not msg.topic.endswith("/audioFrame"):
                _LOGGER.debug("Received %s byte(s) on %s", len(msg.payload), msg.topic)

            # Check enable/disable messages
            if msg.topic == HotwordToggleOn.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = True
                    self.first_audio = True
                    _LOGGER.debug("Enabled")
            elif msg.topic == HotwordToggleOff.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = False
                    _LOGGER.debug("Disabled")

            if not self.enabled:
                # Disabled
                return

            # Handle audio frames
            if AudioFrame.is_topic(msg.topic):
                if (not self.audioframe_topics) or (
                    msg.topic in self.audioframe_topics
                ):
                    if self.first_audio:
                        _LOGGER.debug("Receiving audio")
                        self.first_audio = False

                    siteId = AudioFrame.get_siteId(msg.topic)
                    for wakewordId, result in self.handle_audio_frame(
                        msg.payload, siteId=siteId
                    ):
                        if isinstance(result, HotwordDetected):
                            # Topic contains wake word id
                            self.publish(result, wakewordId=wakewordId)
                        else:
                            self.publish(result)
        except Exception:
            _LOGGER.exception("on_message")

    def publish(self, message: Message, **topic_args):
        """Publish a Hermes message to MQTT."""
        try:
            _LOGGER.debug("-> %s", message)
            topic = message.topic(**topic_args)
            payload = json.dumps(attr.asdict(message))
            _LOGGER.debug("Publishing %s char(s) to %s", len(payload), topic)
            self.client.publish(topic, payload)
        except Exception:
            _LOGGER.exception("on_message")

    # -------------------------------------------------------------------------

    def _check_siteId(self, json_payload: typing.Dict[str, typing.Any]) -> bool:
        if self.siteIds:
            return json_payload.get("siteId", "default") in self.siteIds

        # All sites
        return True

    # -------------------------------------------------------------------------

    def _convert_wav(self, wav_data: bytes) -> bytes:
        """Converts WAV data to required format with sox. Return raw audio."""
        return subprocess.run(
            [
                "sox",
                "-t",
                "wav",
                "-",
                "-r",
                str(self.sample_rate),
                "-e",
                "signed-integer",
                "-b",
                str(self.sample_width * 8),
                "-c",
                str(self.channels),
                "-t",
                "raw",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            input=wav_data,
        ).stdout

    def maybe_convert_wav(self, wav_bytes: bytes) -> bytes:
        """Converts WAV data to required format if necessary. Returns raw audio."""
        with io.BytesIO(wav_bytes) as wav_io:
            with wave.open(wav_io, "rb") as wav_file:
                if (
                    (wav_file.getframerate() != self.sample_rate)
                    or (wav_file.getsampwidth() != self.sample_width)
                    or (wav_file.getnchannels() != self.channels)
                ):
                    # Return converted wav
                    return self._convert_wav(wav_bytes)

                # Return original audio
                return wav_file.readframes(wav_file.getnframes())
