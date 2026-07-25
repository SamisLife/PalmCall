"""SnapCall MVP hand-gesture detector.

MediaPipe supplies hand landmarks and its built-in gesture classifier. A
time-based state machine turns a held gesture into one debounced alert event.
The alert action is local log until we implement the real api.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import requests
from mediapipe.tasks.python import vision

from stream_viewer import (
    FpsMeter,
    StreamDecodeError,
    iter_mjpeg_jpegs,
    normalize_stream_url,
    put_label,
    show_reconnect_screen,
)


WINDOW_NAME = "SnapCall gesture detector - Q or Esc to quit"
MODEL_PATH = Path(__file__).parent / "models" / "gesture_recognizer.task"
EVENT_LOG_PATH = Path(__file__).parent / "events.jsonl"

SUPPORTED_GESTURES = (
    "Open_Palm",
    "Closed_Fist",
    "Pointing_Up",
    "Thumb_Down",
    "Thumb_Up",
    "Victory",
    "ILoveYou",
)

# MediaPipe's 21-landmark hand skeleton.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

# Requiring the palm center inside this generous region prevents a hand at the
# edge of the image from silently triggering.
REGION_LEFT = 0.12
REGION_TOP = 0.08
REGION_RIGHT = 0.88
REGION_BOTTOM = 0.92
MIN_HAND_SPAN = 0.18


class AlertState(Enum):
    IDLE = auto()
    ARMING = auto()
    COUNTDOWN = auto()
    COOLDOWN = auto()


@dataclass
class StateOutput:
    headline: str
    detail: str
    color: tuple[int, int, int]
    progress: float
    triggered: bool = False
    cancelled: bool = False


class AlertStateMachine:
    """Debounces a low-frame-rate gesture using elapsed time, not frame count."""

    def __init__(
        self,
        hold_seconds: float,
        countdown_seconds: float,
        cooldown_seconds: float,
        missing_grace_seconds: float,
    ) -> None:
        self.hold_seconds = hold_seconds
        self.countdown_seconds = countdown_seconds
        self.cooldown_seconds = cooldown_seconds
        self.missing_grace_seconds = missing_grace_seconds
        self.state = AlertState.IDLE
        self.state_started_at = time.monotonic()
        self.last_signal_at = 0.0
        self.release_seen = True
        self.alert_count = 0

    def _enter(self, state: AlertState, now: float) -> None:
        self.state = state
        self.state_started_at = now

    def reset(self, now: float) -> None:
        """Cancel any partial gesture, especially after a stream interruption."""
        self._enter(AlertState.IDLE, now)
        self.last_signal_at = 0.0
        self.release_seen = True

    def _idle_output(self) -> StateOutput:
        return StateOutput(
            "READY",
            "Show an open palm in the center box",
            (80, 255, 120),
            0.0,
        )

    def update(self, signal: bool, now: float) -> StateOutput:
        if self.state is AlertState.IDLE:
            if not signal:
                return self._idle_output()
            self.last_signal_at = now
            if self.hold_seconds <= 0.0:
                self.alert_count += 1
                self.release_seen = False
                self._enter(AlertState.COOLDOWN, now)
                return StateOutput(
                    "ALERT TRIGGERED",
                    "Fake alert logged; remove your hand",
                    (50, 50, 255),
                    1.0,
                    triggered=True,
                )
            self._enter(AlertState.ARMING, now)

        if self.state is AlertState.ARMING:
            if signal:
                self.last_signal_at = now
            elif now - self.last_signal_at > self.missing_grace_seconds:
                self._enter(AlertState.IDLE, now)
                return StateOutput(
                    "RESET",
                    "Palm was lost; try again",
                    (80, 180, 255),
                    0.0,
                )

            elapsed = now - self.state_started_at
            if elapsed >= self.hold_seconds:
                if self.countdown_seconds <= 0.0:
                    self.alert_count += 1
                    self.release_seen = False
                    self._enter(AlertState.COOLDOWN, now)
                    return StateOutput(
                        "ALERT TRIGGERED",
                        "Fake alert logged; remove your hand",
                        (50, 50, 255),
                        1.0,
                        triggered=True,
                    )
                self._enter(AlertState.COUNTDOWN, now)
                return StateOutput(
                    "ARMED",
                    "Keep holding, or remove your hand to cancel",
                    (40, 220, 255),
                    0.0,
                )

            remaining = self.hold_seconds - elapsed
            return StateOutput(
                "HOLD STILL",
                f"Arming in {remaining:.1f}s",
                (40, 220, 255),
                elapsed / self.hold_seconds,
            )

        if self.state is AlertState.COUNTDOWN:
            if signal:
                self.last_signal_at = now
            elif now - self.last_signal_at > self.missing_grace_seconds:
                self._enter(AlertState.IDLE, now)
                return StateOutput(
                    "CANCELLED",
                    "Alert cancelled; system is ready",
                    (80, 180, 255),
                    0.0,
                    cancelled=True,
                )

            elapsed = now - self.state_started_at
            if elapsed >= self.countdown_seconds:
                self.alert_count += 1
                self.release_seen = False
                self._enter(AlertState.COOLDOWN, now)
                return StateOutput(
                    "ALERT TRIGGERED",
                    "Fake alert logged; remove your hand",
                    (50, 50, 255),
                    1.0,
                    triggered=True,
                )

            remaining = self.countdown_seconds - elapsed
            return StateOutput(
                f"ALERT IN {remaining:.1f}s",
                "Remove your hand now to cancel",
                (40, 120, 255),
                elapsed / self.countdown_seconds,
            )

        # COOLDOWN: prevent one continuously held palm from sending repeatedly.
        if signal:
            self.last_signal_at = now
        elif now - self.last_signal_at > self.missing_grace_seconds:
            self.release_seen = True

        elapsed = now - self.state_started_at
        remaining = max(0.0, self.cooldown_seconds - elapsed)
        if remaining <= 0.0 and self.release_seen:
            self._enter(AlertState.IDLE, now)
            return self._idle_output()

        detail = (
            f"Cooldown: {remaining:.1f}s"
            if self.release_seen
            else f"Remove hand to reset; cooldown {remaining:.1f}s"
        )
        return StateOutput(
            "ALERT SENT",
            detail,
            (50, 50, 255),
            1.0 - min(1.0, remaining / self.cooldown_seconds),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recognize a deliberate held hand gesture from the SnapCall stream."
        )
    )
    parser.add_argument(
        "address",
        help=(
            "ESP32 numeric IP or full /stream URL; avoid snapcall.local on "
            "Windows"
        ),
    )
    parser.add_argument(
        "--target-gesture",
        choices=SUPPORTED_GESTURES,
        default="Open_Palm",
        help="gesture that starts an alert (default: Open_Palm)",
    )
    parser.add_argument(
        "--gesture-threshold",
        type=float,
        default=0.50,
        help="minimum MediaPipe gesture score from 0 to 1 (default: 0.50)",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=0.0,
        help="optional continuous gesture time before alert (default: 0)",
    )
    parser.add_argument(
        "--countdown-seconds",
        type=float,
        default=0.0,
        help="optional cancellation countdown duration (default: 0)",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=15.0,
        help="minimum time between alerts (default: 15)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=3.0,
        help="stream connection timeout in seconds (default: 3)",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=15.0,
        help="timeout with no stream data in seconds (default: 15)",
    )
    return parser.parse_args()


def create_recognizer() -> vision.GestureRecognizer:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"MediaPipe model is missing: {MODEL_PATH}"
        )

    options = vision.GestureRecognizerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.50,
        min_hand_presence_confidence=0.50,
        min_tracking_confidence=0.50,
    )
    return vision.GestureRecognizer.create_from_options(options)


def extract_hand(
    result: vision.GestureRecognizerResult,
) -> tuple[list, str, float, str]:
    if not result.hand_landmarks:
        return [], "No hand", 0.0, ""

    landmarks = result.hand_landmarks[0]
    gesture_name = "None"
    gesture_score = 0.0
    if result.gestures and result.gestures[0]:
        category = result.gestures[0][0]
        gesture_name = category.category_name or "None"
        gesture_score = float(category.score or 0.0)

    handedness = ""
    if result.handedness and result.handedness[0]:
        handedness = result.handedness[0][0].category_name or ""

    return landmarks, gesture_name, gesture_score, handedness


def hand_geometry(landmarks: list) -> tuple[tuple[float, float], float]:
    palm_indices = (0, 5, 9, 13, 17)
    center_x = sum(landmarks[i].x for i in palm_indices) / len(palm_indices)
    center_y = sum(landmarks[i].y for i in palm_indices) / len(palm_indices)
    xs = [landmark.x for landmark in landmarks]
    ys = [landmark.y for landmark in landmarks]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    return (center_x, center_y), span


def draw_hand(
    image: np.ndarray,
    landmarks: list,
    active: bool,
) -> None:
    height, width = image.shape[:2]
    points = [
        (
            int(np.clip(landmark.x, 0.0, 1.0) * width),
            int(np.clip(landmark.y, 0.0, 1.0) * height),
        )
        for landmark in landmarks
    ]
    color = (80, 255, 120) if active else (40, 210, 255)
    for start, end in HAND_CONNECTIONS:
        cv2.line(image, points[start], points[end], color, 2, cv2.LINE_AA)
    for point in points:
        cv2.circle(image, point, 4, (245, 245, 245), -1, cv2.LINE_AA)
        cv2.circle(image, point, 5, color, 1, cv2.LINE_AA)


def draw_interface(
    frame: np.ndarray,
    state: StateOutput,
    gesture_name: str,
    gesture_score: float,
    handedness: str,
    fps: float,
    landmarks: list,
    signal: bool,
) -> np.ndarray:
    display = frame.copy()
    height, width = display.shape[:2]

    left = int(REGION_LEFT * width)
    top = int(REGION_TOP * height)
    right = int(REGION_RIGHT * width)
    bottom = int(REGION_BOTTOM * height)
    cv2.rectangle(display, (left, top), (right, bottom), state.color, 2)

    if landmarks:
        draw_hand(display, landmarks, signal)

    overlay = display.copy()
    cv2.rectangle(overlay, (0, 0), (width, 106), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, display, 0.45, 0, display)
    put_label(display, state.headline, (14, 30), state.color)
    put_label(display, state.detail, (14, 58), (235, 235, 235))
    gesture_text = f"{gesture_name} {gesture_score:.2f}"
    if handedness:
        gesture_text += f"  {handedness}"
    put_label(
        display,
        f"{gesture_text}  |  {fps:.1f} FPS",
        (14, 88),
        (190, 220, 255),
    )

    bar_left = 14
    bar_right = width - 14
    bar_top = height - 18
    cv2.rectangle(
        display, (bar_left, bar_top), (bar_right, bar_top + 8), (45, 45, 45), -1
    )
    progress_right = bar_left + int((bar_right - bar_left) * state.progress)
    cv2.rectangle(
        display,
        (bar_left, bar_top),
        (progress_right, bar_top + 8),
        state.color,
        -1,
    )
    return display


def emit_fake_alert(
    stream_url: str,
    gesture_name: str,
    gesture_score: float,
    alert_number: int,
) -> None:
    event = {
        "event": "snapcall_alert",
        "mode": "fake",
        "timestamp": datetime.now(UTC).isoformat(),
        "alert_number": alert_number,
        "gesture": gesture_name,
        "gesture_score": round(gesture_score, 4),
        "source": stream_url,
    }
    with EVENT_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\n")

    print("\n" + "=" * 62)
    print("*** SNAPCALL ALERT TRIGGERED (FAKE CHANNEL) ***")
    print(json.dumps(event, indent=2))
    print("=" * 62 + "\n")


def main() -> int:
    args = parse_args()
    try:
        url = normalize_stream_url(args.address)
    except ValueError as error:
        print(f"Address error: {error}")
        return 2

    positive_options = (
        args.gesture_threshold,
        args.cooldown_seconds,
        args.connect_timeout,
        args.read_timeout,
    )
    if any(value <= 0 for value in positive_options):
        print(
            "Threshold, cooldown, and timeout values must be greater "
            "than zero."
        )
        return 2
    if args.hold_seconds < 0:
        print("--hold-seconds cannot be negative.")
        return 2
    if args.countdown_seconds < 0:
        print("--countdown-seconds cannot be negative.")
        return 2
    if args.gesture_threshold > 1:
        print("--gesture-threshold must be between 0 and 1.")
        return 2

    print(f"SnapCall stream: {url}")
    print(
        f"Signal: {args.target_gesture} >= {args.gesture_threshold:.2f}, "
        f"hold {args.hold_seconds:.1f}s, "
        f"cancel countdown {args.countdown_seconds:.1f}s"
    )
    print("Press Q or Esc in the video window to quit.")

    state_machine = AlertStateMachine(
        hold_seconds=args.hold_seconds,
        countdown_seconds=args.countdown_seconds,
        cooldown_seconds=args.cooldown_seconds,
        missing_grace_seconds=0.85,
    )
    fps = FpsMeter()
    session = requests.Session()
    retry_delay = 0.5
    last_frame: np.ndarray | None = None
    running = True
    timestamp_ms = 0

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    try:
        with create_recognizer() as recognizer:
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
                            continue

                        retry_delay = 0.5
                        last_frame = frame
                        current_fps = fps.update()

                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        media_image = mp.Image(
                            image_format=mp.ImageFormat.SRGB,
                            data=np.ascontiguousarray(rgb),
                        )
                        next_timestamp = int(time.monotonic() * 1000)
                        timestamp_ms = max(timestamp_ms + 1, next_timestamp)
                        result = recognizer.recognize_for_video(
                            media_image, timestamp_ms
                        )
                        (
                            landmarks,
                            gesture_name,
                            gesture_score,
                            handedness,
                        ) = extract_hand(result)

                        centered = False
                        large_enough = False
                        if landmarks:
                            (center_x, center_y), hand_span = hand_geometry(
                                landmarks
                            )
                            centered = (
                                REGION_LEFT <= center_x <= REGION_RIGHT
                                and REGION_TOP <= center_y <= REGION_BOTTOM
                            )
                            large_enough = hand_span >= MIN_HAND_SPAN

                        signal = (
                            gesture_name == args.target_gesture
                            and gesture_score >= args.gesture_threshold
                            and centered
                            and large_enough
                        )
                        now = time.monotonic()
                        state = state_machine.update(signal, now)
                        if state.triggered:
                            emit_fake_alert(
                                url,
                                gesture_name,
                                gesture_score,
                                state_machine.alert_count,
                            )
                        elif state.cancelled:
                            print("Alert cancelled.")

                        display = draw_interface(
                            frame,
                            state,
                            gesture_name,
                            gesture_score,
                            handedness,
                            current_fps,
                            landmarks,
                            signal,
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
                    state_machine.reset(time.monotonic())
                    running = show_reconnect_screen(
                        last_frame,
                        message,
                        retry_delay,
                        window_name=WINDOW_NAME,
                    )
                    retry_delay = min(retry_delay * 2.0, 5.0)
                finally:
                    stream.close()

    except FileNotFoundError as error:
        print(error)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        session.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
