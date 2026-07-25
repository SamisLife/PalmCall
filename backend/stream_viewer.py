"""Reliable SnapCall MJPEG viewer.

OpenCV handles JPEG decoding and display. Requests handles the network stream so
we can recover cleanly when a phone hotspot changes state.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np
import requests


JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
MAX_BUFFER_BYTES = 2 * 1024 * 1024
WINDOW_NAME = "SnapCall camera - Q or Esc to quit"


class StreamDecodeError(RuntimeError):
    """The server responded, but its body was not a usable MJPEG stream."""


def normalize_stream_url(value: str) -> str:
    """Accept an IP, hostname, root URL, or full /stream URL."""
    value = value.strip()
    if not value:
        raise ValueError("The camera address cannot be empty.")
    if "://" not in value:
        value = f"http://{value}"

    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError(f"Invalid camera address: {value!r}")

    path = parsed.path.rstrip("/")
    if not path:
        path = "/stream"

    return urlunparse(
        (parsed.scheme, parsed.netloc, path, "", parsed.query, "")
    )


def iter_mjpeg_jpegs(
    session: requests.Session,
    url: str,
    connect_timeout: float,
    read_timeout: float,
) -> Iterator[bytes]:
    """Yield complete JPEG images from a multipart MJPEG response."""
    headers = {
        "Accept": "multipart/x-mixed-replace",
        "User-Agent": "SnapCall-Laptop/1.0",
    }

    with session.get(
        url,
        headers=headers,
        stream=True,
        timeout=(connect_timeout, read_timeout),
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "multipart/" not in content_type:
            raise StreamDecodeError(
                f"Expected an MJPEG response, received "
                f"{content_type or 'no Content-Type header'}. "
                "Check that the address ends in /stream."
            )

        buffer = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            buffer.extend(chunk)

            while True:
                start = buffer.find(JPEG_START)
                if start < 0:
                    # Preserve one trailing byte in case it is the first byte of
                    # the JPEG start marker split across network packets.
                    if len(buffer) > MAX_BUFFER_BYTES:
                        del buffer[:-1]
                        raise StreamDecodeError(
                            "No JPEG marker found in 2 MiB of stream data."
                        )
                    break

                end = buffer.find(JPEG_END, start + len(JPEG_START))
                if end < 0:
                    if start:
                        del buffer[:start]
                    if len(buffer) > MAX_BUFFER_BYTES:
                        buffer.clear()
                        raise StreamDecodeError(
                            "A camera frame exceeded the 2 MiB safety limit."
                        )
                    break

                end += len(JPEG_END)
                jpeg = bytes(buffer[start:end])
                del buffer[:end]
                yield jpeg


class FpsMeter:
    def __init__(self) -> None:
        self._previous_time: float | None = None
        self.value = 0.0

    def update(self) -> float:
        now = time.monotonic()
        if self._previous_time is not None:
            elapsed = now - self._previous_time
            if elapsed > 0:
                instantaneous = 1.0 / elapsed
                self.value = (
                    instantaneous
                    if self.value == 0.0
                    else (0.90 * self.value) + (0.10 * instantaneous)
                )
        self._previous_time = now
        return self.value


def put_label(
    image: np.ndarray,
    text: str,
    position: tuple[int, int],
    color: tuple[int, int, int] = (80, 255, 120),
) -> None:
    """Draw readable text with a dark outline."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(image, text, position, font, 0.62, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, position, font, 0.62, color, 1, cv2.LINE_AA)


def show_reconnect_screen(
    last_frame: np.ndarray | None,
    error: str,
    delay_seconds: float,
    window_name: str = WINDOW_NAME,
) -> bool:
    """Keep the window responsive during reconnect backoff."""
    if last_frame is None:
        canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    else:
        canvas = last_frame.copy()
        canvas = (canvas.astype(np.float32) * 0.35).astype(np.uint8)

    put_label(canvas, "STREAM DISCONNECTED", (20, 38), (80, 80, 255))
    put_label(
        canvas,
        f"Retrying in {delay_seconds:.1f}s - Q or Esc to quit",
        (20, 70),
        (80, 220, 255),
    )
    short_error = error.replace("\n", " ")[:85]
    put_label(canvas, short_error, (20, 102), (200, 200, 200))

    deadline = time.monotonic() + delay_seconds
    while time.monotonic() < deadline:
        cv2.imshow(window_name, canvas)
        key = cv2.waitKey(100) & 0xFF
        if key in (ord("q"), 27):
            return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Display the SnapCall camera stream and reconnect automatically. "
            "The address may be a device IP, hostname, or full /stream URL."
        )
    )
    parser.add_argument(
        "address",
        help=(
            "camera numeric IP or full stream URL; use the IP printed over "
            "Serial because Windows .local resolution is unreliable"
        ),
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=3.0,
        help="connection timeout in seconds (default: 3)",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=15.0,
        help="timeout with no stream data in seconds (default: 15)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        url = normalize_stream_url(args.address)
    except ValueError as error:
        print(f"Address error: {error}")
        return 2

    if args.connect_timeout <= 0 or args.read_timeout <= 0:
        print("Timeout values must be greater than zero.")
        return 2

    print(f"SnapCall stream: {url}")
    print("Press Q or Esc in the video window to quit.")

    session = requests.Session()
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    retry_delay = 0.5
    last_frame: np.ndarray | None = None
    fps = FpsMeter()
    frames_received = 0
    running = True

    try:
        while running:
            print(f"Connecting to {url} ...")
            stream = iter_mjpeg_jpegs(
                session,
                url,
                connect_timeout=args.connect_timeout,
                read_timeout=args.read_timeout,
            )

            try:
                for jpeg in stream:
                    frame = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if frame is None:
                        print("Skipped one invalid JPEG frame.")
                        continue

                    if frames_received == 0:
                        height, width = frame.shape[:2]
                        print(f"Receiving {width}x{height} video.")

                    frames_received += 1
                    retry_delay = 0.5
                    current_fps = fps.update()
                    last_frame = frame

                    display = frame.copy()
                    height, width = display.shape[:2]
                    put_label(
                        display,
                        f"SnapCall  {width}x{height}  {current_fps:4.1f} FPS",
                        (14, 28),
                    )
                    put_label(
                        display,
                        f"Frames: {frames_received}",
                        (14, height - 14),
                        (230, 230, 230),
                    )
                    cv2.imshow(WINDOW_NAME, display)

                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        running = False
                        break

            except (
                requests.RequestException,
                StreamDecodeError,
                OSError,
            ) as error:
                message = f"{type(error).__name__}: {error}"
                print(f"Stream lost: {message}")
                running = show_reconnect_screen(
                    last_frame, message, retry_delay
                )
                retry_delay = min(retry_delay * 2.0, 5.0)
            finally:
                stream.close()

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        session.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
