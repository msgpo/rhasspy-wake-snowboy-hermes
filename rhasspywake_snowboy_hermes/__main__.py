"""Hermes MQTT service for Rhasspy wakeword with snowboy"""
import argparse
import itertools
import json
import logging
import os
import sys
import typing
from pathlib import Path

import attr
import paho.mqtt.client as mqtt

from . import SnowboyModel, WakeHermesMqtt

_LOGGER = logging.getLogger(__name__)


def main():
    """Main method."""
    parser = argparse.ArgumentParser(prog="rhasspywake_snowboy_hermes")
    parser.add_argument(
        "--model",
        required=True,
        action="append",
        nargs="+",
        help="Snowboy model settings (model, sensitivity, audio_gain, apply_frontend)",
    )
    parser.add_argument(
        "--wakewordId",
        action="append",
        help="Wakeword IDs of each keyword (default: default)",
    )
    parser.add_argument(
        "--stdin-audio", action="store_true", help="Read WAV audio from stdin"
    )
    parser.add_argument(
        "--host", default="localhost", help="MQTT host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=1883, help="MQTT port (default: 1883)"
    )
    parser.add_argument(
        "--siteId",
        action="append",
        help="Hermes siteId(s) to listen for (default: all)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to the console"
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    _LOGGER.debug(args)

    try:
        # Load model settings
        models: typing.List[SnowboyModel] = []

        for model_settings in args.model:
            model = SnowboyModel(model_path=Path(model_settings[0]))

            if len(model_settings) > 1:
                model.sensitivity = model_settings[1]

            if len(model_settings) > 2:
                model.audio_gain = float(model_settings[2])

            if len(model_settings) > 3:
                model.apply_frontend = model_settings[3].strip().lower() == "true"

            models.append(model)

        wakeword_ids = [
            kn[1]
            for kn in itertools.zip_longest(
                args.model, args.wakewordId or [], fillvalue="default"
            )
        ]

        if args.stdin_audio:
            # Read WAV from stdin, detect, and exit
            client = None
            hermes = WakeHermesMqtt(client, models, wakeword_ids)

            hermes.load_detectors()

            if os.isatty(sys.stdin.fileno()):
                print("Reading WAV data from stdin...", file=sys.stderr)

            wav_bytes = sys.stdin.buffer.read()

            # Print results as JSON
            for result in hermes.handle_audio_frame(wav_bytes):
                result_dict = attr.asdict(result)
                json.dump(result_dict, sys.stdout)

            return

        # Listen for messages
        client = mqtt.Client()
        hermes = WakeHermesMqtt(client, models, wakeword_ids, siteIds=args.siteId)

        hermes.load_detectors()

        def on_disconnect(client, userdata, flags, rc):
            try:
                # Automatically reconnect
                _LOGGER.info("Disconnected. Trying to reconnect...")
                client.reconnect()
            except Exception:
                logging.exception("on_disconnect")

        # Connect
        client.on_connect = hermes.on_connect
        client.on_disconnect = on_disconnect
        client.on_message = hermes.on_message

        _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
        client.connect(args.host, args.port)

        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _LOGGER.debug("Shutting down")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
