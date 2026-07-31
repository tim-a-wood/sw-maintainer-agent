"""The in-window player for the rendered explain video.

Qt Multimedia ships in the PySide6-Addons wheel, which the explain
extra installs. Without it this module does not import; the result
screen then keeps the plain Open-the-video button and says how to get
the player.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from maintain.ui.strings import text


def _clock(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


class VideoPanel(QWidget):
    """The finished scene, playing right where the render completed."""

    failed = Signal()

    def __init__(self) -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self.surface = QVideoWidget()
        self.surface.setMinimumHeight(280)
        column.addWidget(self.surface)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.toggle = QPushButton(text("video.pause"))
        self.toggle.setObjectName("Secondary")
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(self._toggle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._seek)
        self.clock = QLabel("0:00 / 0:00")
        self.clock.setObjectName("MonoHint")
        row.addWidget(self.toggle)
        row.addWidget(self.slider, 1)
        row.addWidget(self.clock)
        column.addLayout(row)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.surface)
        self.player.positionChanged.connect(self._moved)
        self.player.durationChanged.connect(self._sized)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.errorOccurred.connect(lambda *_args: self.failed.emit())

    def load(self, path) -> None:
        """Start the video from the top, without a click."""
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def stop(self) -> None:
        """Stop and let go of the file, so nothing keeps it locked."""
        self.player.stop()
        self.player.setSource(QUrl())

    def _toggle(self) -> None:
        if (self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState):
            self.player.pause()
            return
        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
        self.player.play()

    def _seek(self, position: int) -> None:
        self.player.setPosition(position)

    def _moved(self, position: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.clock.setText(
            f"{_clock(position)} / {_clock(self.player.duration())}")

    def _sized(self, duration: int) -> None:
        self.slider.setRange(0, duration)
        self.clock.setText(
            f"{_clock(self.player.position())} / {_clock(duration)}")

    def _playback_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.toggle.setText(text("video.pause" if playing else "video.play"))

    def hideEvent(self, event) -> None:  # noqa: N802
        self.player.pause()
        super().hideEvent(event)
