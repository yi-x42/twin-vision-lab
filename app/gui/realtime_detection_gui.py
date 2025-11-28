"""使用 PySide6 建立即時人員偵測 GUI（支援多條穿越線、停留區域與速度估計）。

功能重點：
- Ultralytics YOLO + Supervision + ByteTrack 進行偵測與追蹤。
- GUI 可控制框線、模糊、軌跡、熱圖、速度標籤等註解器。
- 支援多條穿越線與多邊形停留區域，提供進出統計與停留時間。
- 可建立速度標尺，計算個別追蹤目標的移動速度並顯示統計資訊。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import queue
import secrets
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtMultimedia import QSoundEffect
from ultralytics import YOLO

import cv2
import numpy as np
import supervision as sv

from app.core.database import SyncSessionLocal
from app.core.logger import detection_logger
from app.models.database import (
    DetectionResult,
    LineCrossingEvent,
    SpeedEvent,
    TaskStatistics,
    ZoneDwellEvent,
)
from app.services.camera_stream_manager import (
    FrameData,
    StreamConsumer,
    camera_stream_manager,
)
from app.services.email_notification_service import send_alert_rule_email
from app.services.notification_settings_service import get_email_settings


def resolve_labels(
    detections: sv.Detections,
    object_types: Iterable[str] | None = None,
    dwell_lookup: dict[int, float] | None = None,
    speed_lookup: dict[int, float] | None = None,
    speed_unit: str = "m/s",
) -> list[str]:
    if len(detections) == 0:
        return []

    object_type_list = (
        list(object_types) if object_types is not None else ["person"] * len(detections)
    )
    if len(object_type_list) < len(detections):
        object_type_list.extend(["person"] * (len(detections) - len(object_type_list)))

    confidences = (
        detections.confidence
        if detections.confidence is not None
        else [None] * len(detections)
    )
    tracker_ids = (
        detections.tracker_id
        if detections.tracker_id is not None
        else [None] * len(detections)
    )

    labels: list[str] = []
    for tracker_id, confidence, object_type in zip(
        tracker_ids, confidences, object_type_list
    ):
        id_part = f"#{int(tracker_id)} " if tracker_id is not None else ""
        dwell_suffix = ""
        speed_suffix = ""
        if tracker_id is not None and dwell_lookup is not None:
            dwell_value = dwell_lookup.get(int(tracker_id))
            if dwell_value is not None and dwell_value > 0:
                dwell_suffix = f" {dwell_value:.1f}s"
        if tracker_id is not None and speed_lookup is not None:
            speed_value = speed_lookup.get(int(tracker_id))
            if speed_value is not None:
                speed_value = float(speed_value)
                if speed_unit == "km/h":
                    speed_value *= 3.6
                speed_suffix = f" {speed_value:.1f}{speed_unit}"
        if confidence is None:
            labels.append(f"{id_part}{object_type}{dwell_suffix}{speed_suffix}")
        else:
            labels.append(
                f"{id_part}{object_type} {float(confidence):.2f}{dwell_suffix}{speed_suffix}"
            )
    return labels


def _to_qimage(frame: np.ndarray) -> QtGui.QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width, channel = rgb.shape
    bytes_per_line = channel * width
    image = QtGui.QImage(
        rgb.data, width, height, bytes_per_line, QtGui.QImage.Format_RGB888
    )
    return image.copy()


class ParentWatcher(threading.Thread):
    """監控父行程狀態，確保後端終止時釋放攝影機。"""

    _STILL_ACTIVE = 259

    def __init__(self, parent_pid: int, window: "MainWindow", interval: float = 5.0) -> None:
        super().__init__(daemon=True)
        self._parent_pid = parent_pid
        self._window = window
        self._interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self._parent_pid <= 0:
            return
        while not self._stop_event.wait(self._interval):
            if not self._is_parent_alive():
                QtCore.QMetaObject.invokeMethod(
                    self._window,
                    "handle_control_shutdown",
                    QtCore.Qt.QueuedConnection,
                )
                break

    def _is_parent_alive(self) -> bool:
        if os.name == "nt":
            access = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            handle = ctypes.windll.kernel32.OpenProcess(access, False, self._parent_pid)
            if handle == 0:
                return False
            exit_code = ctypes.c_ulong()
            success = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(success) and exit_code.value == self._STILL_ACTIVE
        try:
            os.kill(self._parent_pid, 0)
        except OSError:
            return False
        return True


class ControlCommandServer(threading.Thread):
    """接收後端指令以顯示/隱藏/關閉 GUI 視窗。"""

    def __init__(
        self,
        window: "MainWindow",
        host: str,
        port: int,
        token: str | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._window = window
        self._host = host
        self._port = port
        self._token = token
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            with socket.create_connection((self._host, self._port), timeout=1):
                pass
        except Exception:
            pass

    def run(self) -> None:  # noqa: D401
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self._host, self._port))
            server.listen(2)

            while not self._stop_event.is_set():
                try:
                    conn, _ = server.accept()
                except OSError:
                    break
                with conn:
                    try:
                        payload_raw = conn.recv(4096)
                        if not payload_raw:
                            continue
                        payload = json.loads(payload_raw.decode("utf-8"))
                    except Exception:
                        continue

                    if self._token and payload.get("token") != self._token:
                        continue

                    action = payload.get("action")
                    if action == "show":
                        QtCore.QMetaObject.invokeMethod(
                            self._window,
                            "handle_control_show",
                            QtCore.Qt.QueuedConnection,
                        )
                        conn.sendall(b"OK")
                    elif action == "hide":
                        QtCore.QMetaObject.invokeMethod(
                            self._window,
                            "handle_control_hide",
                            QtCore.Qt.QueuedConnection,
                        )
                        conn.sendall(b"OK")
                    elif action == "shutdown":
                        QtCore.QMetaObject.invokeMethod(
                            self._window,
                            "handle_control_shutdown",
                            QtCore.Qt.QueuedConnection,
                        )
                        conn.sendall(b"OK")
                    else:
                        conn.sendall(b"UNKNOWN")


class LineConfigDialog(QtWidgets.QDialog):
    def __init__(self, frame: np.ndarray, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("設定穿越線")
        self.points: list[QtCore.QPointF] = []

        pixmap = QtGui.QPixmap.fromImage(_to_qimage(frame))
        self.view = QtWidgets.QGraphicsView()
        scene = QtWidgets.QGraphicsScene(self.view)
        scene.addPixmap(pixmap)
        self.view.setScene(scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setMinimumSize(640, 360)

        self.instructions = QtWidgets.QLabel(
            "左鍵選擇兩個點形成直線，右鍵刪除最後一點。"
        )
        self.instructions.setWordWrap(True)

        self.undo_button = QtWidgets.QPushButton("撤銷")
        self.clear_button = QtWidgets.QPushButton("清除")
        self.undo_button.clicked.connect(self._undo)
        self.clear_button.clicked.connect(self._clear)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addStretch(1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.instructions)
        layout.addWidget(self.view)
        layout.addLayout(controls)
        layout.addWidget(button_box)
        self.setLayout(layout)

        scene.installEventFilter(self)
        self._graphics_items: list[QtWidgets.QGraphicsItem] = []

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self.view.scene():
            if event.type() == QtCore.QEvent.GraphicsSceneMousePress:
                if event.button() == QtCore.Qt.LeftButton:
                    if len(self.points) < 2:
                        self.points.append(event.scenePos())
                        self._refresh_scene()
                elif event.button() == QtCore.Qt.RightButton and self.points:
                    self.points.pop()
                    self._refresh_scene()
                return True
        return super().eventFilter(obj, event)

    def _undo(self) -> None:
        if self.points:
            self.points.pop()
            self._refresh_scene()

    def _clear(self) -> None:
        if self.points:
            self.points.clear()
            self._refresh_scene()

    def _refresh_scene(self) -> None:
        scene = self.view.scene()
        for item in self._graphics_items:
            scene.removeItem(item)
        self._graphics_items.clear()

        pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
        pen.setWidth(2)
        brush = QtGui.QBrush(QtGui.QColor(0, 255, 0))

        for idx, point in enumerate(self.points):
            ellipse = scene.addEllipse(point.x() - 4, point.y() - 4, 8, 8, pen, brush)
            text_item = scene.addText(f"P{idx + 1}")
            text_item.setDefaultTextColor(QtGui.QColor(0, 255, 0))
            text_item.setPos(point.x() + 6, point.y() - 20)
            self._graphics_items.extend([ellipse, text_item])

        if len(self.points) == 2:
            p1, p2 = self.points
            line_item = scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
            self._graphics_items.append(line_item)

        self._ok_button.setEnabled(len(self.points) == 2)
        self.undo_button.setEnabled(bool(self.points))
        self.clear_button.setEnabled(bool(self.points))

    def result(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if len(self.points) != 2:
            return None
        p1, p2 = self.points
        return ((int(p1.x()), int(p1.y())), (int(p2.x()), int(p2.y())))


class ZoneConfigDialog(QtWidgets.QDialog):
    def __init__(self, frame: np.ndarray, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("設定停留區域")
        self.points: list[QtCore.QPointF] = []

        pixmap = QtGui.QPixmap.fromImage(_to_qimage(frame))
        self.view = QtWidgets.QGraphicsView()
        scene = QtWidgets.QGraphicsScene(self.view)
        scene.addPixmap(pixmap)
        self.view.setScene(scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setMinimumSize(640, 360)

        self.instructions = QtWidgets.QLabel(
            "左鍵新增多邊形頂點（至少三個），右鍵刪除最後一點。"
        )
        self.instructions.setWordWrap(True)

        self.undo_button = QtWidgets.QPushButton("撤銷")
        self.clear_button = QtWidgets.QPushButton("清除")
        self.undo_button.clicked.connect(self._undo)
        self.clear_button.clicked.connect(self._clear)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addStretch(1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.instructions)
        layout.addWidget(self.view)
        layout.addLayout(controls)
        layout.addWidget(button_box)
        self.setLayout(layout)

        scene.installEventFilter(self)
        self._graphics_items: list[QtWidgets.QGraphicsItem] = []

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self.view.scene():
            if event.type() == QtCore.QEvent.GraphicsSceneMousePress:
                if event.button() == QtCore.Qt.LeftButton:
                    self.points.append(event.scenePos())
                    self._refresh_scene()
                elif event.button() == QtCore.Qt.RightButton and self.points:
                    self.points.pop()
                    self._refresh_scene()
                return True
        return super().eventFilter(obj, event)

    def _undo(self) -> None:
        if self.points:
            self.points.pop()
            self._refresh_scene()

    def _clear(self) -> None:
        if self.points:
            self.points.clear()
            self._refresh_scene()

    def _refresh_scene(self) -> None:
        scene = self.view.scene()
        for item in self._graphics_items:
            scene.removeItem(item)
        self._graphics_items.clear()

        pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
        pen.setWidth(2)
        brush = QtGui.QBrush(QtGui.QColor(0, 255, 0, 60))

        if len(self.points) >= 2:
            polygon = QtGui.QPolygonF(self.points + [self.points[0]])
            polygon_item = scene.addPolygon(polygon, pen, brush)
            self._graphics_items.append(polygon_item)

        point_pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
        point_brush = QtGui.QBrush(QtGui.QColor(0, 255, 0))
        for idx, point in enumerate(self.points):
            ellipse = scene.addEllipse(point.x() - 4, point.y() - 4, 8, 8, point_pen, point_brush)
            text_item = scene.addText(f"P{idx + 1}")
            text_item.setDefaultTextColor(QtGui.QColor(0, 255, 0))
            text_item.setPos(point.x() + 6, point.y() - 20)
            self._graphics_items.extend([ellipse, text_item])

        self._ok_button.setEnabled(len(self.points) >= 3)
        self.undo_button.setEnabled(bool(self.points))
        self.clear_button.setEnabled(bool(self.points))

    def result(self) -> list[tuple[int, int]] | None:
        if len(self.points) < 3:
            return None
        return [(int(p.x()), int(p.y())) for p in self.points]


class ScaleConfigDialog(QtWidgets.QDialog):
    def __init__(self, frame: np.ndarray, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("設定速度標尺")
        self.points: list[QtCore.QPointF] = []

        pixmap = QtGui.QPixmap.fromImage(_to_qimage(frame))
        self.view = QtWidgets.QGraphicsView()
        scene = QtWidgets.QGraphicsScene(self.view)
        scene.addPixmap(pixmap)
        self.view.setScene(scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setMinimumSize(640, 360)

        self.instructions = QtWidgets.QLabel(
            "左鍵選擇兩個點作為已知距離，右鍵刪除最後一點，並輸入真實距離。"
        )
        self.instructions.setWordWrap(True)

        self.distance_input = QtWidgets.QDoubleSpinBox()
        self.distance_input.setRange(0.01, 10000.0)
        self.distance_input.setDecimals(2)
        self.distance_input.setSuffix(" 公尺")
        self.distance_input.setValue(1.00)
        self.distance_input.valueChanged.connect(lambda _: self._refresh_scene())

        distance_layout = QtWidgets.QHBoxLayout()
        distance_layout.addWidget(QtWidgets.QLabel("實際距離："))
        distance_layout.addWidget(self.distance_input)

        self.undo_button = QtWidgets.QPushButton("撤銷")
        self.clear_button = QtWidgets.QPushButton("清除")
        self.undo_button.clicked.connect(self._undo)
        self.clear_button.clicked.connect(self._clear)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addStretch(1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.instructions)
        layout.addWidget(self.view)
        layout.addLayout(distance_layout)
        layout.addLayout(controls)
        layout.addWidget(button_box)
        self.setLayout(layout)

        scene.installEventFilter(self)
        self._graphics_items: list[QtWidgets.QGraphicsItem] = []

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self.view.scene():
            if event.type() == QtCore.QEvent.GraphicsSceneMousePress:
                if event.button() == QtCore.Qt.LeftButton:
                    if len(self.points) < 2:
                        self.points.append(event.scenePos())
                        self._refresh_scene()
                elif event.button() == QtCore.Qt.RightButton and self.points:
                    self.points.pop()
                    self._refresh_scene()
                return True
        return super().eventFilter(obj, event)

    def _undo(self) -> None:
        if self.points:
            self.points.pop()
            self._refresh_scene()

    def _clear(self) -> None:
        if self.points:
            self.points.clear()
            self._refresh_scene()

    def _refresh_scene(self) -> None:
        scene = self.view.scene()
        for item in self._graphics_items:
            scene.removeItem(item)
        self._graphics_items.clear()

        pen = QtGui.QPen(QtGui.QColor(255, 165, 0))
        pen.setWidth(2)
        brush = QtGui.QBrush(QtGui.QColor(255, 165, 0))

        for idx, point in enumerate(self.points):
            ellipse = scene.addEllipse(point.x() - 4, point.y() - 4, 8, 8, pen, brush)
            text_item = scene.addText(f"P{idx + 1}")
            text_item.setDefaultTextColor(QtGui.QColor(255, 165, 0))
            text_item.setPos(point.x() + 6, point.y() - 20)
            self._graphics_items.extend([ellipse, text_item])

        if len(self.points) == 2:
            p1, p2 = self.points
            line_item = scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
            self._graphics_items.append(line_item)

        self._ok_button.setEnabled(len(self.points) == 2 and self.distance_input.value() > 0)
        self.undo_button.setEnabled(bool(self.points))
        self.clear_button.setEnabled(bool(self.points))

    def result(self) -> tuple[tuple[int, int], tuple[int, int], float] | None:
        if len(self.points) != 2:
            return None
        distance = float(self.distance_input.value())
        if distance <= 0:
            return None
        p1, p2 = self.points
        return ((int(p1.x()), int(p1.y())), (int(p2.x()), int(p2.y())), distance)


@dataclass
class TrackerDwell:
    total_time: float = 0.0
    is_inside: bool = False
    last_seen: float = 0.0
    entered_at: float | None = None


def _current_dwell_seconds(state: TrackerDwell, now: float) -> float:
    if state.is_inside and state.entered_at is not None:
        return state.total_time + (now - state.entered_at)
    return state.total_time


@dataclass
class LineState:
    label: str
    zone: sv.LineZone
    annotator: sv.LineZoneAnnotator
    color: sv.Color | None = None


@dataclass
class ZoneState:
    label: str
    zone: sv.PolygonZone
    annotator: sv.PolygonZoneAnnotator
    dwell_states: dict[int, TrackerDwell] = field(default_factory=dict)
    color: sv.Color | None = None


@dataclass
class SpeedState:
    last_point: tuple[float, float] | None = None
    last_time: float = 0.0
    speed_mps: float = 0.0


class AlertRuleEvaluator:
    """根據配置的警報規則檢查事件並寄送郵件通知。"""

    RULE_LABELS = {
        "lineCrossing": "越線警報",
        "zoneDwell": "區域滯留警報",
        "speedAnomaly": "速度異常警報",
        "crowdCount": "人數警報",
        "fallDetection": "跌倒警報",
    }

    def __init__(
        self,
        task_id: int,
        camera_name: str | None,
        alert_rules_path: str | None,
        alert_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self._task_id = str(task_id)
        self._camera_name = camera_name or f"Task {task_id}"
        self._config_path = Path(alert_rules_path) if alert_rules_path else None
        self._last_mtime = 0.0
        self._rules: list[dict] = []
        self._line_counts: dict[str, dict[str, int]] = {}
        self._last_trigger_time: dict[str, float] = {}
        self._snapshot_dir = PROJECT_ROOT / "uploads" / "alerts" / "snapshots" / self._task_id
        self._email_disabled_logged = False
        self._alert_callback = alert_callback
        self._zone_alert_active: dict[str, set[tuple[str, str]]] = {}

    def _reload_rules_if_needed(self) -> None:
        if not self._config_path:
            return
        try:
            mtime = self._config_path.stat().st_mtime
        except FileNotFoundError:
            if self._rules:
                detection_logger.info("警報設定檔被移除，停止套用規則")
            self._rules = []
            self._line_counts.clear()
            self._last_mtime = 0.0
            return
        if mtime == self._last_mtime:
            return
        try:
            with self._config_path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except Exception as exc:  # noqa: BLE001
            detection_logger.error(f"讀取警報設定檔失敗: {exc}")
            return
        raw_rules = payload.get("rules") if isinstance(payload, dict) else payload
        if not isinstance(raw_rules, list):
            raw_rules = []
        normalized: list[dict] = []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            normalized.append(self._normalize_rule(item))
        self._rules = normalized
        self._line_counts.clear()
        self._last_mtime = mtime
        detection_logger.info(
            "任務 %s 已載入 %d 筆警報規則", self._task_id, len(self._rules)
        )

    def _normalize_rule(self, payload: dict) -> dict:
        selections_raw = payload.get("selections") or []
        selections: set[str] = set()
        for entry in selections_raw:
            if isinstance(entry, str):
                selections.add(entry)
            elif isinstance(entry, dict):
                label = entry.get("label") or entry.get("id")
                if label is not None:
                    selections.add(str(label))
        actions = payload.get("actions") or {}
        rule_id = (
            payload.get("id")
            or payload.get("rule_id")
            or payload.get("type")
            or payload.get("rule_type")
            or uuid.uuid4().hex
        )
        return {
            "id": str(rule_id),
            "rule_type": str(payload.get("rule_type") or payload.get("type") or "custom"),
            "name": payload.get("name") or payload.get("rule_type") or "未命名規則",
            "severity": payload.get("severity") or "中",
            "trigger": payload.get("trigger_values") or payload.get("trigger") or {},
            "actions": actions,
            "selections": selections,
        }

    def _can_trigger(self, rule_id: str, cooldown: float) -> bool:
        now = time.time()
        last = self._last_trigger_time.get(rule_id)
        if last is None or now - last >= cooldown:
            self._last_trigger_time[rule_id] = now
            return True
        return False

    def _save_snapshot(self, frame: np.ndarray, frame_timestamp: datetime, rule_id: str) -> str | None:
        try:
            if frame is None:
                return None
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp_part = frame_timestamp.strftime("%Y%m%d%H%M%S%f")
            filename = f"{rule_id}_{timestamp_part}.jpg"
            save_path = self._snapshot_dir / filename
            success = cv2.imwrite(str(save_path), frame)
            if not success:
                return None
            return str(save_path)
        except Exception as exc:  # noqa: BLE001
            detection_logger.error(f"儲存警報快照失敗: {exc}")
            return None

    def _emit_alert_notification(
        self,
        rule: dict,
        frame_timestamp: datetime,
        description: str,
        extra_lines: list[str] | None = None,
    ) -> None:
        if not self._alert_callback:
            return
        label = self.RULE_LABELS.get(rule.get("rule_type"), rule.get("rule_type"))
        payload = {
            "rule_id": rule.get("id"),
            "rule_name": rule.get("name", label),
            "rule_type": rule.get("rule_type"),
            "rule_label": label,
            "severity": rule.get("severity", "中"),
            "description": description,
            "extra_lines": list(extra_lines or []),
            "timestamp": frame_timestamp.isoformat(),
            "task_id": self._task_id,
            "camera_name": self._camera_name,
        }
        try:
            self._alert_callback(payload)
        except Exception as exc:  # noqa: BLE001
            detection_logger.error(f"推送通知回呼失敗: {exc}")

    def _send_alert(
        self,
        rule: dict,
        frame: np.ndarray,
        frame_timestamp: datetime,
        description: str,
        extra_lines: list[str] | None = None,
    ) -> None:
        email_settings = get_email_settings() or {}
        cooldown = max(5.0, float(email_settings.get("cooldown_seconds", 30)))
        if not self._can_trigger(rule["id"], cooldown):
            return
        self._emit_alert_notification(rule, frame_timestamp, description, extra_lines)
        if not rule.get("actions", {}).get("email", True):
            return
        if not email_settings.get("enabled"):
            if not self._email_disabled_logged:
                detection_logger.info("郵件通知未啟用，警報僅記錄不寄送")
                self._email_disabled_logged = True
            return
        receiver = email_settings.get("address")
        if not receiver:
            detection_logger.warning("郵件通知未設定收件者，無法寄送警報郵件")
            return
        snapshot_path = self._save_snapshot(frame, frame_timestamp, rule["id"])
        label = self.RULE_LABELS.get(rule["rule_type"], rule["rule_type"])
        body_lines = [
            f"任務 ID：{self._task_id}",
            f"攝影機：{self._camera_name}",
            f"警報類型：{label}",
            f"發生時間：{frame_timestamp.isoformat()}",
        ]
        if extra_lines:
            body_lines.extend(extra_lines)
        send_alert_rule_email(
            rule_name=rule.get("name", label),
            rule_type=label,
            severity=rule.get("severity", "中"),
            receiver_email=receiver,
            description=description,
            body_lines=body_lines,
            frame_path=snapshot_path,
        )

    def evaluate(
        self,
        *,
        frame: np.ndarray,
        frame_timestamp: datetime,
        line_events: list[dict],
        zone_events: list[dict],
        zone_live_events: list[dict],
        speed_events: list[dict],
        person_count: int,
        line_summaries: list[dict],
        zone_summaries: list[dict],
    ) -> None:
        if not self._config_path:
            return
        self._reload_rules_if_needed()
        if not self._rules:
            return
        line_events = line_events or []
        zone_events = zone_events or []
        zone_live_events = zone_live_events or []
        speed_events = speed_events or []
        line_summaries = line_summaries or []
        zone_summaries = zone_summaries or []
        for rule in self._rules:
            rule_type = rule["rule_type"]
            if rule_type == "lineCrossing":
                self._evaluate_line_crossing(rule, frame, frame_timestamp, line_events)
            elif rule_type == "zoneDwell":
                self._evaluate_zone(
                    rule,
                    frame,
                    frame_timestamp,
                    zone_events,
                    zone_live_events,
                    zone_summaries,
                )
            elif rule_type == "speedAnomaly":
                self._evaluate_speed(rule, frame, frame_timestamp, speed_events)
            elif rule_type == "crowdCount":
                self._evaluate_crowd(rule, frame, frame_timestamp, person_count)

    def _evaluate_line_crossing(
        self,
        rule: dict,
        frame: np.ndarray,
        frame_timestamp: datetime,
        line_events: list[dict],
    ) -> None:
        selections = rule["selections"]
        if not selections:
            return
        trigger = rule.get("trigger") or {}
        threshold = int(trigger.get("crossingCount") or 1)
        threshold = max(1, threshold)
        counts = self._line_counts.setdefault(rule["id"], {})
        for event in line_events:
            label = str(event.get("line_id") or "")
            if not label or label not in selections:
                continue
            counts[label] = counts.get(label, 0) + 1
            if counts[label] >= threshold:
                direction = event.get("direction") or "unknown"
                description = f"線段 {label} 偵測 {direction} 越線 (累積 {counts[label]}/{threshold})"
                extra = [f"越線方向：{direction}", f"穿越線：{label}", f"閾值：{threshold}"]
                self._send_alert(rule, frame, frame_timestamp, description, extra)
                counts[label] = 0

    def _evaluate_zone(
        self,
        rule: dict,
        frame: np.ndarray,
        frame_timestamp: datetime,
        zone_events: list[dict],
        zone_live_events: list[dict],
        zone_summaries: list[dict],
    ) -> None:
        selections = rule["selections"]
        if not selections:
            return
        trigger = rule.get("trigger") or {}
        dwell_threshold = float(trigger.get("dwellSeconds") or 0.0)
        people_threshold = int(trigger.get("simultaneousCount") or 0)
        active_keys = self._zone_alert_active.setdefault(rule["id"], set())
        current_live_keys: set[tuple[str, str]] = set()

        if dwell_threshold > 0:
            for entry in zone_live_events:
                zone_id = str(entry.get("zone_id") or "")
                tracker_id = entry.get("tracker_id")
                if not zone_id or zone_id not in selections or tracker_id is None:
                    continue
                tracker_key = str(int(tracker_id))
                key = (zone_id, tracker_key)
                current_live_keys.add(key)
                dwell_seconds = float(entry.get("dwell_seconds") or 0.0)
                if dwell_seconds < dwell_threshold or key in active_keys:
                    continue
                description = f"區域 {zone_id} 停留 {dwell_seconds:.1f}s，超過設定門檻"
                extra = [
                    f"區域：{zone_id}",
                    f"停留秒數：{dwell_seconds:.1f}s",
                    f"門檻：{dwell_threshold:.1f}s",
                ]
                entered_at = entry.get("entered_at")
                if isinstance(entered_at, datetime):
                    extra.append(f"進入時間：{entered_at.isoformat()}")
                self._send_alert(rule, frame, frame_timestamp, description, extra)
                active_keys.add(key)

            for event in zone_events:
                zone_id = str(event.get("zone_id") or "")
                if not zone_id or zone_id not in selections:
                    continue
                dwell_seconds = float(event.get("dwell_seconds") or 0.0)
                if dwell_seconds < dwell_threshold:
                    continue
                tracker_id = event.get("tracker_id")
                key = None
                if tracker_id is not None:
                    key = (zone_id, str(int(tracker_id)))
                if key and key in active_keys:
                    continue
                description = f"區域 {zone_id} 停留 {dwell_seconds:.1f}s，超過設定門檻"
                extra = [
                    f"區域：{zone_id}",
                    f"停留秒數：{dwell_seconds:.1f}s",
                    f"門檻：{dwell_threshold:.1f}s",
                ]
                entered_at = event.get("entered_at")
                if isinstance(entered_at, datetime):
                    extra.append(f"進入時間：{entered_at.isoformat()}")
                self._send_alert(rule, frame, frame_timestamp, description, extra)

            for key in list(active_keys):
                if key not in current_live_keys:
                    active_keys.remove(key)
        else:
            if rule["id"] in self._zone_alert_active:
                self._zone_alert_active[rule["id"]].clear()

        if people_threshold <= 0:
            return
        for summary in zone_summaries:
            label = str(summary.get("label") or summary.get("zone_id") or "")
            if not label or label not in selections:
                continue
            current = int(summary.get("current") or 0)
            if current >= people_threshold:
                description = f"區域 {label} 目前人數 {current} 人，超過門檻 {people_threshold}"
                extra = [
                    f"區域：{label}",
                    f"目前人數：{current}",
                    f"門檻：{people_threshold}",
                ]
                self._send_alert(rule, frame, frame_timestamp, description, extra)

    def _evaluate_speed(
        self,
        rule: dict,
        frame: np.ndarray,
        frame_timestamp: datetime,
        speed_events: list[dict],
    ) -> None:
        trigger = rule.get("trigger") or {}
        avg_threshold = float(trigger.get("avgSpeedThreshold") or 0.0)
        max_threshold = float(trigger.get("maxSpeedThreshold") or 0.0)
        if avg_threshold <= 0 and max_threshold <= 0:
            return
        for event in speed_events:
            avg_speed = float(event.get("speed_avg") or 0.0)
            max_speed = float(event.get("speed_max") or avg_speed)
            threshold_hit = False
            details: list[str] = []
            if avg_threshold > 0 and avg_speed >= avg_threshold:
                details.append(f"平均速度 {avg_speed:.2f} m/s ≥ {avg_threshold}")
                threshold_hit = True
            if max_threshold > 0 and max_speed >= max_threshold:
                details.append(f"最大速度 {max_speed:.2f} m/s ≥ {max_threshold}")
                threshold_hit = True
            if threshold_hit:
                description = "；".join(details)
                extra = [
                    f"平均速度：{avg_speed:.2f} m/s",
                    f"最大速度：{max_speed:.2f} m/s",
                ]
                self._send_alert(rule, frame, frame_timestamp, description, extra)

    def _evaluate_crowd(
        self,
        rule: dict,
        frame: np.ndarray,
        frame_timestamp: datetime,
        person_count: int,
    ) -> None:
        trigger = rule.get("trigger") or {}
        threshold = int(trigger.get("peopleCount") or 0)
        if threshold <= 0:
            return
        if person_count >= threshold:
            description = f"現場偵測到 {person_count} 人，已超過門檻 {threshold}"
            extra = [
                f"當前人數：{person_count}",
                f"設定門檻：{threshold}",
            ]
            self._send_alert(rule, frame, frame_timestamp, description, extra)


class DatabaseWriter:
    """負責將偵測資料寫入資料庫。"""

    def __init__(self, task_id: int) -> None:
        self._task_id = task_id
        self._session = None
        self._lock = threading.Lock()
        self._disabled = False
        self._last_person_count = 0
        self._confidence_sum = 0.0
        self._confidence_count = 0
        self._last_avg_confidence = 0.0
        self._speed_sum = 0.0
        self._speed_count = 0
        self._speed_max = 0.0

    def _get_session(self):
        if self._disabled:
            return None
        if self._session is None:
            try:
                self._session = SyncSessionLocal()
            except Exception as exc:  # noqa: BLE001
                detection_logger.error(f"建立資料庫連線失敗: {exc}")
                self._disabled = True
                return None
        return self._session

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    def persist_frame(
        self,
        *,
        frame_number: int,
        frame_timestamp: datetime,
        detections: list[dict],
        line_events: list[dict],
        zone_events: list[dict],
        speed_events: list[dict],
        stats_payload: dict,
    ) -> None:
        session = self._get_session()
        if session is None:
            return

        try:
            if detections:
                session.add_all(
                    [
                            DetectionResult(
                                task_id=self._task_id,
                                tracker_id=row.get("tracker_id"),
                                object_speed=row.get("object_speed"),
                                zones=row.get("zones"),
                                frame_number=frame_number,
                                frame_timestamp=frame_timestamp,
                                object_type=row.get("object_type"),
                                confidence=row.get("confidence"),
                                bbox_x1=row["bbox"][0],
                                bbox_y1=row["bbox"][1],
                                bbox_x2=row["bbox"][2],
                                bbox_y2=row["bbox"][3],
                                center_x=row["center"][0],
                                center_y=row["center"][1],
                                thumbnail_path=row.get("thumbnail_path"),
                            )
                        for row in detections
                    ]
                )
                frame_confidences = [
                    row.get("confidence")
                    for row in detections
                    if row.get("confidence") is not None
                ]
                if frame_confidences:
                    self._confidence_sum += sum(frame_confidences)
                    self._confidence_count += len(frame_confidences)
                    self._last_avg_confidence = (
                        self._confidence_sum / self._confidence_count
                    )

            if line_events:
                session.add_all(
                    [
                        LineCrossingEvent(
                            task_id=self._task_id,
                            tracker_id=event.get("tracker_id"),
                            line_id=event.get("line_id"),
                            direction=event.get("direction"),
                            frame_number=event.get("frame_number"),
                            frame_timestamp=event.get("frame_timestamp"),
                            extra=event.get("extra"),
                        )
                        for event in line_events
                    ]
                )

            if zone_events:
                session.add_all(
                    [
                        ZoneDwellEvent(
                            task_id=self._task_id,
                            tracker_id=event.get("tracker_id"),
                            zone_id=event.get("zone_id"),
                            entered_at=event.get("entered_at"),
                            exited_at=event.get("exited_at"),
                            dwell_seconds=event.get("dwell_seconds"),
                            frame_number=event.get("frame_number"),
                            event_timestamp=event.get("event_timestamp"),
                            extra=event.get("extra"),
                        )
                        for event in zone_events
                    ]
                )

            if speed_events:
                session.add_all(
                    [
                        SpeedEvent(
                            task_id=self._task_id,
                            tracker_id=event.get("tracker_id"),
                            speed_avg=event.get("speed_avg"),
                            speed_max=event.get("speed_max"),
                            threshold=event.get("threshold"),
                            frame_number=event.get("frame_number"),
                            event_timestamp=event.get("event_timestamp"),
                            extra=event.get("extra"),
                        )
                        for event in speed_events
                    ]
                )
                speed_values = [
                    float(event.get("speed_avg"))
                    for event in speed_events
                    if event.get("speed_avg") is not None
                ]
                if speed_values:
                    self._speed_sum += sum(speed_values)
                    self._speed_count += len(speed_values)
                    self._speed_max = max(self._speed_max, max(speed_values))

            stats = session.get(TaskStatistics, self._task_id)
            if not stats:
                stats = TaskStatistics(task_id=self._task_id)
                session.add(stats)
            stats.updated_at = frame_timestamp
            stats.fps = stats_payload.get("fps")
            current_person_count = int(stats_payload.get("person_count") or 0)
            if current_person_count > 0 or self._last_person_count == 0:
                self._last_person_count = current_person_count
            stats.person_count = (
                self._last_person_count if self._last_person_count else current_person_count
            )
            aggregated_avg_confidence = (
                self._last_avg_confidence
                if self._confidence_count
                else stats_payload.get("avg_confidence")
            )
            stats.avg_confidence = aggregated_avg_confidence or 0.0
            stats.line_stats = stats_payload.get("line_stats")
            stats.zone_stats = stats_payload.get("zone_stats")
            speed_stats_payload = dict(stats_payload.get("speed_stats") or {})
            unit = speed_stats_payload.get("unit") or "m/s"
            configured = bool(speed_stats_payload.get("configured"))
            if configured and self._speed_count:
                factor = 3.6 if unit.lower() == "km/h" else 1.0
                speed_stats_payload["avg_speed"] = (
                    self._speed_sum / self._speed_count
                ) * factor
                speed_stats_payload["max_speed"] = self._speed_max * factor
            stats.speed_stats = speed_stats_payload
            extra_payload = dict(stats_payload.get("extra") or {})
            extra_payload["current_person_count"] = current_person_count
            extra_payload["last_person_count"] = self._last_person_count
            stats.extra = extra_payload

            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            detection_logger.error(f"寫入即時辨識資料失敗: {exc}")
            self._disabled = True

def _draw_line_overlay(scene: np.ndarray, line_state: LineState) -> np.ndarray:
    color = line_state.color or sv.ColorPalette.DEFAULT.by_idx(0)
    bgr = tuple(int(v) for v in color.as_bgr())
    label = f"{line_state.label} IN:{line_state.zone.in_count} OUT:{line_state.zone.out_count}"

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5
    thickness = 1
    text_size, baseline = cv2.getTextSize(label, font, font_scale, thickness)

    height, width = scene.shape[:2]
    center = line_state.zone.vector.center.as_xy_int_tuple()
    text_x = max(6, min(center[0] - text_size[0] // 2, width - text_size[0] - 6))
    text_y = max(text_size[1] + 6, min(center[1] - 10, height - 6))

    bg_top_left = (text_x - 6, text_y - text_size[1] - 6)
    bg_bottom_right = (text_x + text_size[0] + 6, text_y + baseline + 4)

    luminance = 0.299 * bgr[2] + 0.587 * bgr[1] + 0.114 * bgr[0]
    text_color = (0, 0, 0) if luminance > 160 else (255, 255, 255)

    cv2.rectangle(scene, bg_top_left, bg_bottom_right, bgr, -1)
    cv2.putText(
        scene,
        label,
        (text_x, text_y),
        font,
        font_scale,
        text_color,
        thickness,
        cv2.LINE_AA,
    )
    return scene


class DetectionWorker(QtCore.QThread):
    frameReady = QtCore.Signal(QtGui.QImage)
    statsUpdated = QtCore.Signal(dict)
    statusMessage = QtCore.Signal(str)
    errorOccurred = QtCore.Signal(str)
    linesChanged = QtCore.Signal(list)
    zonesChanged = QtCore.Signal(list)
    scaleChanged = QtCore.Signal(dict)
    alertTriggered = QtCore.Signal(dict)

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self._args = args
        if not hasattr(args, "task_id") or args.task_id is None:
            raise ValueError("啟動即時偵測時必須提供 --task-id 參數")
        self._task_id = int(args.task_id)
        self._toggle_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._toggles: dict[str, bool] = {
            "corner": True,
            "blur": False,
            "trace": False,
            "heatmap": False,
            "speed": True,
        }
        self._last_frame: np.ndarray | None = None
        self._lines: list[LineState] = []
        self._zones: list[ZoneState] = []
        self._reset_heatmap_event = threading.Event()
        self._tracker_warning_sent = False
        self._meters_per_pixel: float | None = None
        self._scale_reference_pixels: float | None = None
        self._scale_reference_distance: float | None = None
        self._speed_unit = "m/s"
        self._speed_states: dict[int, SpeedState] = {}
        self._fall_alert_enabled = bool(getattr(args, "enable_fall_alert", False))
        self._fall_event_log: dict[int, float] = {}
        self._fall_aspect_ratio_threshold = 1.25
        self._fall_log_cooldown = 5.0
        self._next_line_id = 1
        self._next_zone_id = 1
        self._frame_index = 0
        self._db_writer = DatabaseWriter(self._task_id)
        self._thumbnails_dir = PROJECT_ROOT / "uploads" / "detections" / str(self._task_id)
        self._emit_lines_changed()
        self._emit_zones_changed()
        self._emit_scale_changed()
        raw_source = getattr(args, "source", 0)
        if isinstance(raw_source, str) and raw_source.isdigit():
            raw_source = int(raw_source)
        self._source_value = raw_source
        self._shared_enabled = isinstance(raw_source, int)
        self._shared_camera_id: str | None = (
            f"camera_{raw_source}" if self._shared_enabled else None
        )
        self._shared_device_index: int | None = raw_source if self._shared_enabled else None
        self._shared_queue: queue.Queue[FrameData] | None = (
            queue.Queue(maxsize=1) if self._shared_enabled else None
        )
        self._shared_consumer: StreamConsumer | None = None
        self._shared_running = threading.Event()
        self._capture: cv2.VideoCapture | None = None
        alert_rules_path = getattr(args, "alert_rules", None)
        self._alert_evaluator: AlertRuleEvaluator | None = None
        if alert_rules_path:
            self._alert_evaluator = AlertRuleEvaluator(
                task_id=self._task_id,
                camera_name=getattr(args, "window_name", None),
                alert_rules_path=alert_rules_path,
                alert_callback=self._notify_alert,
            )

    def _emit_lines_changed(self) -> None:
        with self._config_lock:
            labels = [line.label for line in self._lines]
        self.linesChanged.emit(labels)

    def _emit_zones_changed(self) -> None:
        with self._config_lock:
            labels = [zone.label for zone in self._zones]
        self.zonesChanged.emit(labels)

    def _emit_scale_changed(self) -> None:
        with self._config_lock:
            payload = {
                "configured": self._meters_per_pixel is not None,
                "meters_per_pixel": self._meters_per_pixel,
                "reference_distance": self._scale_reference_distance,
                "reference_pixels": self._scale_reference_pixels,
                "unit": self._speed_unit,
            }
        self.scaleChanged.emit(payload)

    @QtCore.Slot()
    def request_scale_status(self) -> None:
        self._emit_scale_changed()

    def _notify_alert(self, payload: dict) -> None:
        """Emit alert payload to GUI thread."""
        if not isinstance(payload, dict):
            return
        self.alertTriggered.emit(payload)

    def _handle_shared_frame(self, frame_data: FrameData) -> None:
        if not self._shared_running.is_set() or self._shared_queue is None:
            return
        try:
            self._shared_queue.put_nowait(frame_data)
        except queue.Full:
            pass

    def _start_shared_stream(self) -> None:
        if not self._shared_camera_id or self._shared_device_index is None:
            raise RuntimeError("共享攝影機資訊不完整，無法啟動")
        success = camera_stream_manager.start_stream(
            self._shared_camera_id, self._shared_device_index
        )
        if not success:
            raise RuntimeError("無法啟動共享攝影機流")
        consumer_id = f"gui-{self._task_id}-{uuid.uuid4().hex[:6]}"
        consumer = StreamConsumer(consumer_id, self._handle_shared_frame)
        if not camera_stream_manager.add_consumer(self._shared_camera_id, consumer):
            raise RuntimeError("無法註冊共享攝影機流消費者")
        self._shared_consumer = consumer
        self._shared_running.set()

    def _stop_shared_stream(self) -> None:
        self._shared_running.clear()
        if self._shared_consumer and self._shared_camera_id:
            camera_stream_manager.remove_consumer(
                self._shared_camera_id, self._shared_consumer.consumer_id
            )
        self._shared_consumer = None
        if self._shared_queue:
            with self._shared_queue.mutex:
                self._shared_queue.queue.clear()

    def _wait_shared_frame(self, timeout: float = 1.0) -> tuple[np.ndarray, datetime]:
        if not self._shared_queue:
            raise RuntimeError("共享攝影機佇列未初始化")
        try:
            frame_data = self._shared_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("共享攝影機無影格可用")
        frame = frame_data.frame.copy()
        timestamp = frame_data.timestamp or datetime.now(timezone.utc)
        return frame, timestamp

    def _save_detection_thumbnail(
        self,
        frame: np.ndarray,
        bbox: Iterable[float],
        tracker_id: int | None,
        frame_number: int,
        frame_timestamp: datetime,
    ) -> str | None:
        """裁切目前幀的偵測區域並儲存縮圖，回傳相對 uploads 的路徑。"""
        try:
            if frame is None:
                return None
            x1, y1, x2, y2 = bbox
            height, width = frame.shape[:2]
            x1_i = max(0, min(width, int(x1)))
            y1_i = max(0, min(height, int(y1)))
            x2_i = max(0, min(width, int(x2)))
            y2_i = max(0, min(height, int(y2)))
            if x2_i <= x1_i or y2_i <= y1_i:
                return None
            crop = frame[y1_i:y2_i, x1_i:x2_i]
            if crop.size == 0:
                return None
            self._thumbnails_dir.mkdir(parents=True, exist_ok=True)
            tracker_part = f"{tracker_id}" if tracker_id is not None else "det"
            timestamp_part = frame_timestamp.strftime("%Y%m%d%H%M%S%f")
            filename = f"{tracker_part}_f{frame_number}_{timestamp_part}.jpg"
            save_path = self._thumbnails_dir / filename
            success = cv2.imwrite(str(save_path), crop)
            if not success:
                return None
            relative = Path("detections") / str(self._task_id) / filename
            return relative.as_posix()
        except Exception as exc:  # noqa: BLE001
            detection_logger.error(f"儲存縮圖失敗: {exc}")
            return None

    def _detect_fall_candidates(
        self, detections: sv.Detections
    ) -> list[tuple[int | None, float]]:
        events: list[tuple[int | None, float]] = []
        if detections.xyxy is None or len(detections.xyxy) == 0:
            return events
        for idx, bbox in enumerate(detections.xyxy):
            width = float(bbox[2] - bbox[0])
            height = float(bbox[3] - bbox[1])
            if height <= 1e-3 or width <= 1e-3:
                continue
            aspect_ratio = width / height
            if aspect_ratio < self._fall_aspect_ratio_threshold:
                continue
            tracker_id = None
            if detections.tracker_id is not None and idx < len(detections.tracker_id):
                tracker_value = detections.tracker_id[idx]
                tracker_id = int(tracker_value) if tracker_value is not None else None
            events.append((tracker_id, aspect_ratio))
        return events

    def _log_fall_events(
        self, events: list[tuple[int | None, float]], frame_timestamp: datetime
    ) -> None:
        if not events:
            return
        now = time.perf_counter()
        for tracker_id, ratio in events:
            key = tracker_id if tracker_id is not None else -1
            last_logged = self._fall_event_log.get(key, 0.0)
            if now - last_logged < self._fall_log_cooldown:
                continue
            self._fall_event_log[key] = now
            tracker_part = (
                f"追蹤 ID {tracker_id}" if tracker_id is not None else "未知目標"
            )
            detection_logger.warning(
                f"[FallAlert][Task {self._task_id}] {tracker_part} 疑似跌倒 (寬高比 {ratio:.2f})"
            )

    @QtCore.Slot(str, bool)
    def set_toggle(self, name: str, value: bool) -> None:
        with self._toggle_lock:
            if name in self._toggles and self._toggles[name] != value:
                self._toggles[name] = value
                state = "開啟" if value else "關閉"
                self.statusMessage.emit(f"{name} 註解器已{state}")

    @QtCore.Slot()
    def reset_heatmap(self) -> None:
        self._reset_heatmap_event.set()

    @QtCore.Slot(object)
    def add_line(self, line_points: object) -> None:
        if line_points is None:
            return
        try:
            start_xy, end_xy = line_points
            start_point = sv.Point(x=int(start_xy[0]), y=int(start_xy[1]))
            end_point = sv.Point(x=int(end_xy[0]), y=int(end_xy[1]))
        except (TypeError, ValueError):
            self.statusMessage.emit("穿越線設定失敗，請確認點位。")
            return
        line_zone = sv.LineZone(
            start=start_point,
            end=end_point,
            triggering_anchors=(sv.Position.CENTER,),
        )
        line_id = self._next_line_id
        self._next_line_id += 1
        label = f"Line {line_id}"
        palette = sv.ColorPalette.DEFAULT
        color = palette.by_idx((line_id - 1) % len(palette.colors))
        line_annotator = sv.LineZoneAnnotator(
            text_orient_to_line=True,
            thickness=3,
            color=color,
            display_in_count=False,
            display_out_count=False,
            display_text_box=False,
        )
        line_state = LineState(
            label=label,
            zone=line_zone,
            annotator=line_annotator,
            color=color,
        )
        with self._config_lock:
            self._lines.append(line_state)
        self._emit_lines_changed()
        self.statusMessage.emit(
            f"新增穿越線 {label}: {start_point.as_xy_int_tuple()} -> {end_point.as_xy_int_tuple()}"
        )

    @QtCore.Slot(int)
    def remove_line(self, index: int) -> None:
        removed_label: str | None = None
        with self._config_lock:
            if 0 <= index < len(self._lines):
                removed_label = self._lines.pop(index).label
        if removed_label is not None:
            self._emit_lines_changed()
            self.statusMessage.emit(f"已移除穿越線 {removed_label}")

    @QtCore.Slot()
    def clear_lines(self) -> None:
        with self._config_lock:
            had_lines = bool(self._lines)
            self._lines.clear()
            self._next_line_id = 1
        if had_lines:
            self._emit_lines_changed()
            self.statusMessage.emit("已移除全部穿越線")

    @QtCore.Slot(object)
    def add_zone(self, polygon_points: object) -> None:
        if polygon_points is None:
            return
        try:
            polygon_array = np.array(polygon_points, dtype=np.int32)
        except (TypeError, ValueError):
            self.statusMessage.emit("停留區域設定失敗，請確認多邊形點位。")
            return
        if polygon_array.ndim != 2 or polygon_array.shape[0] < 3:
            self.statusMessage.emit("停留區域需至少三個點。")
            return
        polygon_zone = sv.PolygonZone(
            polygon=polygon_array,
            triggering_anchors=(sv.Position.CENTER,),
        )
        zone_id = self._next_zone_id
        self._next_zone_id += 1
        label = f"Zone {zone_id}"
        palette = sv.ColorPalette.DEFAULT
        color = palette.by_idx((zone_id - 1) % len(palette.colors))
        zone_annotator = sv.PolygonZoneAnnotator(
            zone=polygon_zone,
            color=color,
            thickness=2,
            text_color=sv.Color.from_hex("#000000"),
            text_scale=0.6,
            text_thickness=1,
            opacity=0.25,
        )
        zone_state = ZoneState(
            label=label,
            zone=polygon_zone,
            annotator=zone_annotator,
            color=color,
        )
        with self._config_lock:
            self._zones.append(zone_state)
        self._emit_zones_changed()
        self.statusMessage.emit(
            f"新增停留區域 {label}，頂點數：{polygon_array.shape[0]}"
        )

    @QtCore.Slot(int)
    def remove_zone(self, index: int) -> None:
        removed_label: str | None = None
        with self._config_lock:
            if 0 <= index < len(self._zones):
                removed_label = self._zones.pop(index).label
        if removed_label is not None:
            self._emit_zones_changed()
            self.statusMessage.emit(f"已移除停留區域 {removed_label}")

    @QtCore.Slot()
    def clear_zones(self) -> None:
        with self._config_lock:
            had_zones = bool(self._zones)
            self._zones.clear()
            self._next_zone_id = 1
        if had_zones:
            self._emit_zones_changed()
            self.statusMessage.emit("已移除全部停留區域")

    @QtCore.Slot(object)
    def set_distance_scale(self, payload: object) -> None:
        if payload is None:
            return
        try:
            start_xy, end_xy, distance_m = payload
            start_x, start_y = start_xy
            end_x, end_y = end_xy
            distance_m = float(distance_m)
            if distance_m <= 0:
                raise ValueError
            pixel_distance = math.hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
            if pixel_distance <= 0:
                raise ValueError
        except (TypeError, ValueError):
            self.statusMessage.emit("速度標尺設定失敗：請確認距離與點位。")
            return
        meters_per_pixel = distance_m / pixel_distance
        with self._config_lock:
            self._meters_per_pixel = meters_per_pixel
            self._scale_reference_pixels = pixel_distance
            self._scale_reference_distance = distance_m
            self._speed_states.clear()
        self._emit_scale_changed()
        self.statusMessage.emit(
            f"速度標尺已設定：{distance_m:.2f} 公尺 對應 {pixel_distance:.1f} 像素"
        )

    @QtCore.Slot()
    def clear_distance_scale(self) -> None:
        with self._config_lock:
            had_scale = self._meters_per_pixel is not None
            self._meters_per_pixel = None
            self._scale_reference_pixels = None
            self._scale_reference_distance = None
            self._speed_states.clear()
        if had_scale:
            self._emit_scale_changed()
            self.statusMessage.emit("已清除速度標尺設定")

    def last_frame_copy(self) -> np.ndarray | None:
        with self._frame_lock:
            if self._last_frame is None:
                return None
            return self._last_frame.copy()

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            self._processing_loop()
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))

    def _processing_loop(self) -> None:
        args = self._args
        capture: cv2.VideoCapture | None = None
        if self._shared_enabled:
            self._start_shared_stream()
            try:
                frame, frame_timestamp = self._wait_shared_frame(timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                self._stop_shared_stream()
                raise RuntimeError(f"無法從共享攝影機取得影像：{exc}") from exc
        else:
            source = self._source_value
            capture = cv2.VideoCapture(source)
            if not capture.isOpened():
                raise RuntimeError(f"無法開啟影像來源：{args.source}")
            success, frame = capture.read()
            if not success:
                raise RuntimeError("無法讀取影像來源的第一個影格")
            frame_timestamp = datetime.now(timezone.utc)

        model = YOLO(args.model)
        tracker = sv.ByteTrack()

        heatmap_annotator = sv.HeatMapAnnotator(
            radius=args.heatmap_radius, opacity=args.heatmap_opacity
        )
        blur_annotator = sv.BlurAnnotator(kernel_size=args.blur_kernel)
        trace_annotator = sv.TraceAnnotator(trace_length=args.trace_length)
        box_annotator = sv.BoxCornerAnnotator()
        label_annotator = sv.LabelAnnotator()

        with self._frame_lock:
            self._last_frame = frame.copy()

        fps_value = 0.0
        last_frame_time = time.perf_counter()

        try:
            while not self.isInterruptionRequested():
                if self._shared_enabled:
                    try:
                        frame, frame_timestamp = self._wait_shared_frame(timeout=1.0)
                    except TimeoutError:
                        continue
                else:
                    success, next_frame = capture.read() if capture else (False, None)
                    if not success or next_frame is None:
                        self.statusMessage.emit("影像來源結束或中斷，停止串流")
                        break
                    frame = next_frame
                    frame_timestamp = datetime.now(timezone.utc)

                current_time = time.perf_counter()
                elapsed = current_time - last_frame_time
                last_frame_time = current_time
                if elapsed > 0:
                    fps_value = 1.0 / elapsed

                with self._config_lock:
                    lines_snapshot = list(self._lines)
                    zones_snapshot = list(self._zones)
                    meters_per_pixel = self._meters_per_pixel
                    speed_unit = self._speed_unit

                result = model(
                    frame,
                    imgsz=args.imgsz,
                    conf=args.conf,
                    device=args.device,
                    verbose=False,
                )[0]
                detections = sv.Detections.from_ultralytics(result)

                names = getattr(result, "names", None)
                if names is not None and detections.class_id is not None:
                    mask = np.array(
                        [
                            str(names.get(int(class_id), "")).lower() == "person"
                            for class_id in detections.class_id
                        ],
                        dtype=bool,
                    )
                    detections = detections[mask]

                detections = tracker.update_with_detections(detections)
                if len(detections) and (
                    detections.tracker_id is None or not len(detections.tracker_id)
                ):
                    if not self._tracker_warning_sent:
                        self._tracker_warning_sent = True
                        self.statusMessage.emit(
                            "警告：追蹤器未能產生 ID，穿越線、區域與速度計算可能失效。"
                        )
                else:
                    self._tracker_warning_sent = False

                self._frame_index += 1
                frame_number = self._frame_index
                frame_timestamp = datetime.now(timezone.utc)
                num_detections = len(detections)
                if num_detections and detections.tracker_id is not None:
                    tracker_ids = [
                        int(tracker_id) if tracker_id is not None else None
                        for tracker_id in detections.tracker_id
                    ]
                else:
                    tracker_ids = [None] * num_detections
                zone_labels_per_detection = [[] for _ in range(num_detections)]
                line_event_records: list[dict[str, object]] = []
                zone_event_records: list[dict[str, object]] = []
                zone_live_event_records: list[dict[str, object]] = []
                speed_event_records: list[dict[str, object]] = []
                fall_events: list[tuple[int | None, float]] = []

                with self._toggle_lock:
                    toggles_snapshot = dict(self._toggles)

                if self._fall_alert_enabled and len(detections):
                    fall_events = self._detect_fall_candidates(detections)
                    if fall_events:
                        self._log_fall_events(fall_events, frame_timestamp)

                annotated = frame.copy()

                object_types = None
                if names is not None and detections.class_id is not None:
                    object_types = [
                        str(names.get(int(class_id), "person"))
                        for class_id in detections.class_id
                    ]

                if toggles_snapshot["heatmap"]:
                    annotated = heatmap_annotator.annotate(
                        scene=annotated, detections=detections
                    )
                if toggles_snapshot["blur"] and len(detections):
                    annotated = blur_annotator.annotate(
                        scene=annotated, detections=detections
                    )
                if toggles_snapshot["trace"] and len(detections):
                    annotated = trace_annotator.annotate(
                        scene=annotated, detections=detections
                    )

                speed_lookup: dict[int, float] = {}
                if (
                    meters_per_pixel is not None
                    and len(detections)
                    and detections.tracker_id is not None
                    and len(detections.tracker_id)
                ):
                    current_ids: set[int] = set()
                    for idx, tracker_id in enumerate(detections.tracker_id):
                        if tracker_id is None:
                            continue
                        tracker_int = int(tracker_id)
                        current_ids.add(tracker_int)
                        state = self._speed_states.setdefault(tracker_int, SpeedState())
                        xyxy = detections.xyxy[idx]
                        center_x = float(xyxy[0] + xyxy[2]) / 2.0
                        center_y = float(xyxy[1] + xyxy[3]) / 2.0
                        if state.last_point is not None:
                            dt = current_time - state.last_time
                            if dt > 1e-6:
                                pixel_distance = math.hypot(
                                    center_x - state.last_point[0],
                                    center_y - state.last_point[1],
                                )
                                meters = pixel_distance * meters_per_pixel
                                speed_mps = meters / dt
                                state.speed_mps = speed_mps
                                speed_lookup[tracker_int] = speed_mps
                        state.last_point = (center_x, center_y)
                        state.last_time = current_time
                    stale_ids = [
                        tracker_id
                        for tracker_id, state in self._speed_states.items()
                        if tracker_id not in current_ids
                        and current_time - state.last_time > 1.0
                    ]
                    for tracker_id in stale_ids:
                        self._speed_states.pop(tracker_id, None)

                if speed_lookup:
                    for tracker_id, speed_value in speed_lookup.items():
                        speed_event_records.append(
                            {
                                "tracker_id": tracker_id,
                                "speed_avg": speed_value,
                                "speed_max": speed_value,
                                "threshold": None,
                                "frame_number": frame_number,
                                "event_timestamp": frame_timestamp,
                                "extra": None,
                            }
                        )

                line_total_in = 0
                line_total_out = 0
                line_summaries: list[dict[str, int]] = []
                for line_state in lines_snapshot:
                    crossed_in, crossed_out = line_state.zone.trigger(detections=detections)
                    in_count = int(line_state.zone.in_count)
                    out_count = int(line_state.zone.out_count)
                    line_total_in += in_count
                    line_total_out += out_count
                    line_summaries.append(
                        {
                            "label": line_state.label,
                            "in": in_count,
                            "out": out_count,
                        }
                    )
                    if num_detections:
                        for detection_idx, tracker_int in enumerate(tracker_ids):
                            if detection_idx < len(crossed_in) and crossed_in[detection_idx]:
                                line_event_records.append(
                                    {
                                        "line_id": line_state.label,
                                        "direction": "in",
                                        "tracker_id": tracker_int,
                                        "frame_number": frame_number,
                                        "frame_timestamp": frame_timestamp,
                                        "extra": None,
                                    }
                                )
                            if detection_idx < len(crossed_out) and crossed_out[detection_idx]:
                                line_event_records.append(
                                    {
                                        "line_id": line_state.label,
                                        "direction": "out",
                                        "tracker_id": tracker_int,
                                        "frame_number": frame_number,
                                        "frame_timestamp": frame_timestamp,
                                        "extra": None,
                                    }
                                )
                    annotated = line_state.annotator.annotate(
                        frame=annotated, line_counter=line_state.zone
                    )
                    annotated = _draw_line_overlay(annotated, line_state)

                zone_total_current = 0
                zone_summaries: list[dict[str, float]] = []
                dwell_lookup: dict[int, float] = {}
                now_wall = time.time()

                def append_zone_event_record(
                    zone_label: str,
                    tracker_int: int,
                    entered_at_wall: float | None,
                    dwell_seconds: float,
                ) -> None:
                    if tracker_int is None or entered_at_wall is None or dwell_seconds <= 0:
                        return
                    zone_event_records.append(
                        {
                            "tracker_id": tracker_int,
                            "zone_id": zone_label,
                            "entered_at": datetime.fromtimestamp(entered_at_wall, tz=timezone.utc),
                            "exited_at": frame_timestamp,
                            "dwell_seconds": dwell_seconds,
                            "frame_number": frame_number,
                            "event_timestamp": frame_timestamp,
                            "extra": None,
                        }
                    )

                for zone_state in zones_snapshot:
                    zone = zone_state.zone
                    zone_mask = zone.trigger(detections=detections)
                    if zone_mask is None:
                        zone_mask = np.zeros(len(detections), dtype=bool)

                    dwell_states = zone_state.dwell_states
                    current_inside_ids: set[int] = set()
                    current_detection_ids: set[int] = set()

                    if detections.tracker_id is not None and len(detections.tracker_id):
                        for detection_idx, tracker_id in enumerate(detections.tracker_id):
                            if tracker_id is None:
                                continue
                            tracker_int = int(tracker_id)
                            current_detection_ids.add(tracker_int)
                            state = dwell_states.setdefault(tracker_int, TrackerDwell())
                            state.last_seen = now_wall

                            in_zone = bool(
                                detection_idx < len(zone_mask) and zone_mask[detection_idx]
                            )
                            if in_zone:
                                if detection_idx < len(zone_labels_per_detection):
                                    zone_labels_per_detection[detection_idx].append(
                                        zone_state.label
                                    )
                                current_inside_ids.add(tracker_int)
                                if not state.is_inside:
                                    state.entered_at = now_wall
                                state.is_inside = True
                                entered_at_wall = state.entered_at
                                if entered_at_wall is not None:
                                    dwell_seconds_live = max(0.0, now_wall - entered_at_wall)
                                    zone_live_event_records.append(
                                        {
                                            "tracker_id": tracker_int,
                                            "zone_id": zone_state.label,
                                            "entered_at": datetime.fromtimestamp(
                                                entered_at_wall
                                            ),
                                            "dwell_seconds": dwell_seconds_live,
                                            "frame_number": frame_number,
                                            "event_timestamp": frame_timestamp,
                                            "extra": None,
                                        }
                                    )
                            else:
                                if state.is_inside:
                                    dwell_increment = 0.0
                                    if state.entered_at is not None:
                                        dwell_increment = max(
                                            0.0, now_wall - state.entered_at
                                        )
                                        append_zone_event_record(
                                            zone_state.label,
                                            tracker_int,
                                            state.entered_at,
                                            dwell_increment,
                                        )
                                        state.total_time += dwell_increment
                                        state.entered_at = None
                                    state.is_inside = False

                    for tracker_id, state in list(dwell_states.items()):
                        if tracker_id not in current_detection_ids and state.is_inside:
                            dwell_increment = 0.0
                            if state.entered_at is not None:
                                dwell_increment = max(0.0, now_wall - state.entered_at)
                                append_zone_event_record(
                                    zone_state.label,
                                    tracker_id,
                                    state.entered_at,
                                    dwell_increment,
                                )
                                state.total_time += dwell_increment
                                state.entered_at = None
                            state.is_inside = False
                        if (not state.is_inside) and (now_wall - state.last_seen > 30.0):
                            dwell_states.pop(tracker_id, None)

                    inside_durations = [
                        _current_dwell_seconds(dwell_states[tracker_id], now_wall)
                        for tracker_id in current_inside_ids
                        if tracker_id in dwell_states
                    ]
                    zone_current_count = len(current_inside_ids)
                    zone_average_dwell = (
                        sum(inside_durations) / len(inside_durations)
                        if inside_durations
                        else 0.0
                    )
                    zone_max_dwell = max(inside_durations, default=0.0)

                    for tracker_id, state in dwell_states.items():
                        dwell_lookup[tracker_id] = max(
                            dwell_lookup.get(tracker_id, 0.0),
                            _current_dwell_seconds(state, now_wall),
                        )

                    zone_total_current += zone_current_count
                    zone_summaries.append(
                        {
                            "label": zone_state.label,
                            "current": zone_current_count,
                            "average": zone_average_dwell,
                            "max": zone_max_dwell,
                        }
                    )

                    annotated = zone_state.annotator.annotate(scene=annotated)

                detection_rows: list[dict[str, object]] = []
                if num_detections:
                    xyxy = detections.xyxy
                    confidence_values = (
                        detections.confidence
                        if detections.confidence is not None
                        else [None] * num_detections
                    )
                    for detection_idx in range(num_detections):
                        bbox = xyxy[detection_idx]
                        center_x = float(bbox[0] + bbox[2]) / 2.0
                        center_y = float(bbox[1] + bbox[3]) / 2.0
                        tracker_int = (
                            tracker_ids[detection_idx]
                            if detection_idx < len(tracker_ids)
                            else None
                        )
                        confidence_val = (
                            float(confidence_values[detection_idx])
                            if detection_idx < len(confidence_values)
                            and confidence_values[detection_idx] is not None
                            else None
                        )
                        zones_for_detection = (
                            zone_labels_per_detection[detection_idx]
                            if detection_idx < len(zone_labels_per_detection)
                            else []
                        )
                        thumbnail_path = self._save_detection_thumbnail(
                            frame,
                            bbox,
                            tracker_int,
                            frame_number,
                            frame_timestamp,
                        )
                        detection_rows.append(
                            {
                                "tracker_id": tracker_int,
                                "object_type": (
                                    object_types[detection_idx]
                                    if object_types and detection_idx < len(object_types)
                                    else "person"
                                ),
                                "confidence": confidence_val,
                                "bbox": [
                                    float(bbox[0]),
                                    float(bbox[1]),
                                    float(bbox[2]),
                                    float(bbox[3]),
                                ],
                                "center": [center_x, center_y],
                                "zones": zones_for_detection,
                                "object_speed": speed_lookup.get(tracker_int)
                                if tracker_int is not None
                                else None,
                                "thumbnail_path": thumbnail_path,
                            }
                        )

                if toggles_snapshot["corner"] and len(detections):
                    annotated = box_annotator.annotate(
                        scene=annotated, detections=detections
                    )
                    label_speed_lookup = (
                        speed_lookup if (toggles_snapshot.get("speed") and meters_per_pixel is not None) else None
                    )
                    annotated = label_annotator.annotate(
                        scene=annotated,
                        detections=detections,
                        labels=resolve_labels(
                            detections,
                            object_types=object_types,
                            dwell_lookup=dwell_lookup,
                            speed_lookup=label_speed_lookup,
                            speed_unit=speed_unit,
                        ),
                    )

                with self._frame_lock:
                    self._last_frame = frame.copy()

                image = _to_qimage(annotated)
                self.frameReady.emit(image)

                person_count = len(detections)
                avg_confidence = 0.0
                if person_count and detections.confidence is not None:
                    values = [
                        float(conf)
                        for conf in detections.confidence
                        if conf is not None
                    ]
                    if values:
                        avg_confidence = sum(values) / len(values)

                speed_values = list(speed_lookup.values()) if speed_lookup else []
                avg_speed = max_speed = 0.0
                if speed_values:
                    avg_speed = sum(speed_values) / len(speed_values)
                    max_speed = max(speed_values)
                speed_factor = 3.6 if speed_unit == "km/h" else 1.0

                stats_payload = {
                    "toggles": toggles_snapshot,
                    "person_count": person_count,
                    "avg_confidence": avg_confidence,
                    "fps": fps_value,
                    "line_total_in": line_total_in,
                    "line_total_out": line_total_out,
                    "line_configured": bool(lines_snapshot),
                    "line_summaries": line_summaries,
                    "zone_configured": bool(zones_snapshot),
                    "zone_total_current": zone_total_current,
                    "zone_summaries": zone_summaries,
                    "speed_configured": meters_per_pixel is not None,
                    "speed_unit": speed_unit,
                    "avg_speed": avg_speed * speed_factor,
                    "max_speed": max_speed * speed_factor,
                }
                self.statsUpdated.emit(stats_payload)
                db_stats_payload = {
                    "fps": fps_value,
                    "person_count": person_count,
                    "avg_confidence": avg_confidence,
                    "line_stats": {
                        summary["label"]: {"in": summary["in"], "out": summary["out"]}
                        for summary in line_summaries
                    },
                    "zone_stats": {
                        summary["label"]: {
                            "current": summary["current"],
                            "average": summary["average"],
                            "max": summary["max"],
                        }
                        for summary in zone_summaries
                    },
                    "speed_stats": {
                        "configured": meters_per_pixel is not None,
                        "unit": speed_unit,
                        "avg_speed": stats_payload["avg_speed"],
                        "max_speed": stats_payload["max_speed"],
                    },
                    "extra": {
                        "line_total_in": line_total_in,
                        "line_total_out": line_total_out,
                        "zone_total_current": zone_total_current,
                        "toggles": toggles_snapshot,
                    },
                }
                if self._db_writer:
                    self._db_writer.persist_frame(
                        frame_number=frame_number,
                        frame_timestamp=frame_timestamp,
                        detections=detection_rows,
                        line_events=line_event_records,
                        zone_events=zone_event_records,
                        speed_events=speed_event_records,
                        stats_payload=db_stats_payload,
                    )
                if self._alert_evaluator:
                    try:
                        self._alert_evaluator.evaluate(
                            frame=frame,
                            frame_timestamp=frame_timestamp,
                            line_events=line_event_records,
                            zone_events=zone_event_records,
                            zone_live_events=zone_live_event_records,
                            speed_events=speed_event_records,
                            person_count=person_count,
                            line_summaries=line_summaries,
                            zone_summaries=zone_summaries,
                        )
                    except Exception as exc:  # noqa: BLE001
                        detection_logger.error(f"警報規則處理失敗: {exc}")

                if self._reset_heatmap_event.is_set():
                    heatmap_annotator.heat_mask = None
                    self._reset_heatmap_event.clear()
                    self.statusMessage.emit("熱圖已重置")

        finally:
            if capture:
                capture.release()
            cv2.destroyAllWindows()
            if self._shared_enabled:
                self._stop_shared_stream()
            if self._db_writer:
                self._db_writer.close()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self._args = args
        self._current_pixmap: QtGui.QPixmap | None = None
        self._start_hidden = bool(getattr(args, "start_hidden", False))
        self._allow_window_close = bool(getattr(args, "allow_window_close", False))
        self._shutdown_requested = False
        self._control_server: ControlCommandServer | None = None
        self._parent_watcher: ParentWatcher | None = None
        self._alert_sound: QSoundEffect | None = None

        self.worker = DetectionWorker(args)
        self.worker.frameReady.connect(self.update_image)
        self.worker.statsUpdated.connect(self.update_stats)
        self.worker.statusMessage.connect(self.show_status_message)
        self.worker.errorOccurred.connect(self.handle_worker_error)
        self.worker.linesChanged.connect(self.refresh_line_list)
        self.worker.zonesChanged.connect(self.refresh_zone_list)
        self.worker.scaleChanged.connect(self.update_scale_info)
        self.worker.alertTriggered.connect(self.handle_alert_notification)

        self._init_alert_sound()
        self._build_ui()
        self.worker.request_scale_status()
        self.worker.start()

        parent_pid = getattr(args, "parent_pid", None)
        if parent_pid:
            self._start_parent_watchdog(int(parent_pid))

    def attach_control_server(self, server: ControlCommandServer) -> None:
        self._control_server = server

    def _start_parent_watchdog(self, parent_pid: int) -> None:
        watcher = ParentWatcher(parent_pid, self)
        watcher.start()
        self._parent_watcher = watcher

    def _init_alert_sound(self) -> None:
        sound_path = PROJECT_ROOT / "assets" / "sounds" / "alert.wav"
        if sound_path.exists():
            sound = QSoundEffect(self)
            sound.setSource(QtCore.QUrl.fromLocalFile(str(sound_path)))
            sound.setLoopCount(1)
            sound.setVolume(0.8)
            self._alert_sound = sound
        else:
            self._alert_sound = None

    def _play_alert_sound(self) -> None:
        if self._alert_sound and self._alert_sound.status() == QSoundEffect.Status.Ready:
            self._alert_sound.stop()
            self._alert_sound.play()
        else:
            QtWidgets.QApplication.beep()

    def _build_ui(self) -> None:
        self.setWindowTitle(self._args.window_name or "Supervision Live")
        self.resize(1280, 720)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        layout = QtWidgets.QHBoxLayout()
        central.setLayout(layout)

        self.video_label = QtWidgets.QLabel()
        self.video_label.setAlignment(QtCore.Qt.AlignCenter)
        self.video_label.setMinimumSize(800, 450)
        self.video_label.setStyleSheet(
            "background-color: #101010; border: 1px solid #2a2a2a;"
        )
        layout.addWidget(self.video_label, stretch=2)

        side_panel = QtWidgets.QFrame()
        side_panel.setMinimumWidth(360)
        side_panel.setFrameShape(QtWidgets.QFrame.StyledPanel)
        side_layout = QtWidgets.QVBoxLayout()
        side_panel.setLayout(side_layout)
        layout.addWidget(side_panel, stretch=1)

        toggle_group = QtWidgets.QGroupBox("註解開關")
        toggle_layout = QtWidgets.QVBoxLayout()
        toggle_group.setLayout(toggle_layout)
        side_layout.addWidget(toggle_group)

        self.toggle_widgets: dict[str, QtWidgets.QCheckBox] = {}
        label_map = {
            "corner": "框線顯示",
            "blur": "模糊遮罩",
            "trace": "移動軌跡",
            "heatmap": "熱度圖",
            "speed": "速度標籤",
        }
        defaults = {
            "corner": True,
            "blur": False,
            "trace": False,
            "heatmap": False,
            "speed": True,
        }
        for key, label in label_map.items():
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(defaults[key])
            checkbox.toggled.connect(
                lambda checked, name=key: self.worker.set_toggle(name, checked)
            )
            self.toggle_widgets[key] = checkbox
            toggle_layout.addWidget(checkbox)

        toggle_layout.addStretch(1)

        control_group = QtWidgets.QGroupBox("控制")
        control_layout = QtWidgets.QVBoxLayout()
        control_group.setLayout(control_layout)
        side_layout.addWidget(control_group)

        self.reset_button = QtWidgets.QPushButton("重置熱圖")
        self.reset_button.clicked.connect(self.worker.reset_heatmap)
        control_layout.addWidget(self.reset_button)

        line_group = QtWidgets.QGroupBox("穿越線")
        line_layout = QtWidgets.QVBoxLayout()
        line_group.setLayout(line_layout)
        control_layout.addWidget(line_group)

        self.line_list = QtWidgets.QListWidget()
        self.line_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        line_layout.addWidget(self.line_list)
        self.line_list.itemSelectionChanged.connect(self._update_line_buttons)

        line_buttons = QtWidgets.QHBoxLayout()
        self.add_line_button = QtWidgets.QPushButton("新增")
        self.add_line_button.clicked.connect(self.open_line_dialog)
        line_buttons.addWidget(self.add_line_button)

        self.remove_line_button = QtWidgets.QPushButton("移除所選")
        self.remove_line_button.clicked.connect(self.remove_selected_line)
        self.remove_line_button.setEnabled(False)
        line_buttons.addWidget(self.remove_line_button)

        self.clear_lines_button = QtWidgets.QPushButton("全部移除")
        self.clear_lines_button.clicked.connect(self.worker.clear_lines)
        self.clear_lines_button.setEnabled(False)
        line_buttons.addWidget(self.clear_lines_button)
        line_layout.addLayout(line_buttons)

        zone_group = QtWidgets.QGroupBox("停留區域")
        zone_layout = QtWidgets.QVBoxLayout()
        zone_group.setLayout(zone_layout)
        control_layout.addWidget(zone_group)

        self.zone_list = QtWidgets.QListWidget()
        self.zone_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        zone_layout.addWidget(self.zone_list)
        self.zone_list.itemSelectionChanged.connect(self._update_zone_buttons)

        zone_buttons = QtWidgets.QHBoxLayout()
        self.add_zone_button = QtWidgets.QPushButton("新增")
        self.add_zone_button.clicked.connect(self.open_zone_dialog)
        zone_buttons.addWidget(self.add_zone_button)

        self.remove_zone_button = QtWidgets.QPushButton("移除所選")
        self.remove_zone_button.clicked.connect(self.remove_selected_zone)
        self.remove_zone_button.setEnabled(False)
        zone_buttons.addWidget(self.remove_zone_button)

        self.clear_zones_button = QtWidgets.QPushButton("全部移除")
        self.clear_zones_button.clicked.connect(self.worker.clear_zones)
        self.clear_zones_button.setEnabled(False)
        zone_buttons.addWidget(self.clear_zones_button)
        zone_layout.addLayout(zone_buttons)

        speed_group = QtWidgets.QGroupBox("速度估計")
        speed_layout = QtWidgets.QVBoxLayout()
        speed_group.setLayout(speed_layout)
        control_layout.addWidget(speed_group)

        self.scale_label = QtWidgets.QLabel("標尺未設定")
        self.scale_label.setWordWrap(True)
        speed_layout.addWidget(self.scale_label)

        speed_buttons = QtWidgets.QHBoxLayout()
        self.set_scale_button = QtWidgets.QPushButton("設定標尺")
        self.set_scale_button.clicked.connect(self.open_scale_dialog)
        speed_buttons.addWidget(self.set_scale_button)

        self.clear_scale_button = QtWidgets.QPushButton("清除標尺")
        self.clear_scale_button.clicked.connect(self.worker.clear_distance_scale)
        self.clear_scale_button.setEnabled(False)
        speed_buttons.addWidget(self.clear_scale_button)
        speed_layout.addLayout(speed_buttons)

        control_layout.addStretch(1)

        stats_group = QtWidgets.QGroupBox("狀態資訊")
        stats_layout = QtWidgets.QVBoxLayout()
        stats_group.setLayout(stats_layout)
        side_layout.addWidget(stats_group)

        self.stats_label = QtWidgets.QLabel("等待影像資料...")
        self.stats_label.setWordWrap(True)
        self.stats_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        stats_layout.addWidget(self.stats_label)

        side_layout.addStretch(1)

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)

    def open_line_dialog(self) -> None:
        frame = self.worker.last_frame_copy()
        if frame is None:
            QtWidgets.QMessageBox.warning(self, "無法設定", "目前尚未取得影像。")
            return
        dialog = LineConfigDialog(frame, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            result = dialog.result()
            if result is not None:
                self.worker.add_line(result)

    def remove_selected_line(self) -> None:
        row = self.line_list.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "移除穿越線", "請先選擇要移除的穿越線。")
            return
        self.worker.remove_line(row)

    @QtCore.Slot(list)
    def refresh_line_list(self, labels: list[str]) -> None:
        current_row = self.line_list.currentRow()
        self.line_list.blockSignals(True)
        self.line_list.clear()
        self.line_list.addItems(labels)
        self.line_list.blockSignals(False)
        if labels:
            if current_row < 0 or current_row >= len(labels):
                current_row = 0
            self.line_list.setCurrentRow(current_row)
        else:
            self.line_list.clearSelection()
        self._update_line_buttons()

    def _update_line_buttons(self) -> None:
        has_items = self.line_list.count() > 0
        has_selection = self.line_list.currentRow() >= 0
        self.remove_line_button.setEnabled(has_items and has_selection)
        self.clear_lines_button.setEnabled(has_items)

    def open_zone_dialog(self) -> None:
        frame = self.worker.last_frame_copy()
        if frame is None:
            QtWidgets.QMessageBox.warning(self, "無法設定", "目前尚未取得影像。")
            return
        dialog = ZoneConfigDialog(frame, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            polygon = dialog.result()
            if polygon is not None:
                self.worker.add_zone(polygon)

    def open_scale_dialog(self) -> None:
        frame = self.worker.last_frame_copy()
        if frame is None:
            QtWidgets.QMessageBox.warning(self, "無法設定", "目前尚未取得影像。")
            return
        dialog = ScaleConfigDialog(frame, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            result = dialog.result()
            if result is not None:
                self.worker.set_distance_scale(result)

    def remove_selected_zone(self) -> None:
        row = self.zone_list.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "移除停留區域", "請先選擇要移除的區域。")
            return
        self.worker.remove_zone(row)

    @QtCore.Slot(list)
    def refresh_zone_list(self, labels: list[str]) -> None:
        current_row = self.zone_list.currentRow()
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        self.zone_list.addItems(labels)
        self.zone_list.blockSignals(False)
        if labels:
            if current_row < 0 or current_row >= len(labels):
                current_row = 0
            self.zone_list.setCurrentRow(current_row)
        else:
            self.zone_list.clearSelection()
        self._update_zone_buttons()

    def _update_zone_buttons(self) -> None:
        has_items = self.zone_list.count() > 0
        has_selection = self.zone_list.currentRow() >= 0
        self.remove_zone_button.setEnabled(has_items and has_selection)
        self.clear_zones_button.setEnabled(has_items)

    @QtCore.Slot(dict)
    def update_scale_info(self, payload: dict) -> None:
        configured = bool(payload.get("configured", False))
        if configured:
            distance = float(payload.get("reference_distance", 0.0) or 0.0)
            pixels = float(payload.get("reference_pixels", 0.0) or 0.0)
            meters_per_pixel = float(payload.get("meters_per_pixel", 0.0) or 0.0)
            self.scale_label.setText(
                (
                    "標尺：{distance:.2f} 公尺 / {pixels:.1f} 像素 "
                    "(1 像素 ≈ {mpp:.4f} 公尺)"
                ).format(distance=distance, pixels=pixels, mpp=meters_per_pixel)
            )
            self.clear_scale_button.setEnabled(True)
        else:
            self.scale_label.setText("標尺未設定")
            self.clear_scale_button.setEnabled(False)

    @QtCore.Slot(QtGui.QImage)
    def update_image(self, image: QtGui.QImage) -> None:
        pixmap = QtGui.QPixmap.fromImage(image)
        self._current_pixmap = pixmap
        self._set_video_pixmap(pixmap)

    def _set_video_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        if pixmap is None:
            return
        scaled = pixmap.scaled(
            self.video_label.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)

    @QtCore.Slot(dict)
    def update_stats(self, stats: dict) -> None:
        toggles = stats.get("toggles", {})
        for name, checkbox in self.toggle_widgets.items():
            desired = QtCore.Qt.Checked if toggles.get(name, False) else QtCore.Qt.Unchecked
            if checkbox.checkState() != desired:
                checkbox.blockSignals(True)
                checkbox.setCheckState(desired)
                checkbox.blockSignals(False)

        line_configured = bool(stats.get("line_configured", False))
        line_total_in = int(stats.get("line_total_in", 0))
        line_total_out = int(stats.get("line_total_out", 0))
        line_summaries = stats.get("line_summaries", [])

        zone_configured = bool(stats.get("zone_configured", False))
        zone_total_current = int(stats.get("zone_total_current", 0))
        zone_summaries = stats.get("zone_summaries", [])

        speed_configured = bool(stats.get("speed_configured", False))
        speed_unit = stats.get("speed_unit", "m/s") or "m/s"
        avg_speed_value = float(stats.get("avg_speed", 0.0) or 0.0)
        max_speed_value = float(stats.get("max_speed", 0.0) or 0.0)

        lines = [
            " | ".join(
                [
                    f"框線：{'開' if toggles.get('corner') else '關'}",
                    f"模糊：{'開' if toggles.get('blur') else '關'}",
                    f"軌跡：{'開' if toggles.get('trace') else '關'}",
                    f"熱圖：{'開' if toggles.get('heatmap') else '關'}",
                    f"速度：{'開' if toggles.get('speed') else '關'}",
                ]
            ),
            f"偵測人數：{stats.get('person_count', 0)}",
            f"平均信心值：{stats.get('avg_confidence', 0.0):.2f}",
            f"FPS：{stats.get('fps', 0.0):.1f}",
        ]
        if line_configured:
            lines.append(f"穿越線總計 進入：{line_total_in} | 離開：{line_total_out}")
            for summary in line_summaries:
                lines.append(
                    f"  {summary.get('label', 'Line')} 進入：{summary.get('in', 0)} | 離開：{summary.get('out', 0)}"
                )
        else:
            lines.append("穿越線：未設定")

        if zone_configured:
            lines.append(f"停留區域總計 目前：{zone_total_current}")
            for summary in zone_summaries:
                lines.append(
                    (
                        "  {label} 目前：{current} | 平均：{average:.1f}s | 最長：{maximum:.1f}s"
                    ).format(
                        label=summary.get("label", "Zone"),
                        current=summary.get("current", 0),
                        average=summary.get("average", 0.0),
                        maximum=summary.get("max", 0.0),
                    )
                )
        else:
            lines.append("停留區域：未設定")

        if speed_configured:
            if toggles.get("speed", False):
                lines.append(
                    f"速度：平均 {avg_speed_value:.1f}{speed_unit} | 最高 {max_speed_value:.1f}{speed_unit}"
                )
            else:
                lines.append("速度：標尺已設定（顯示關閉）")
        else:
            lines.append("速度：標尺未設定")

        self.stats_label.setText("\n".join(lines))

    @QtCore.Slot(str)
    def show_status_message(self, message: str) -> None:
        self.status_bar.showMessage(message, 5000)

    @QtCore.Slot(dict)
    def handle_alert_notification(self, payload: dict) -> None:
        self._play_alert_sound()
        severity_raw = str(payload.get("severity") or "")
        severity_lower = severity_raw.lower()
        icon = QtWidgets.QMessageBox.Icon.Information
        if "高" in severity_raw or severity_lower in {"high", "critical"}:
            icon = QtWidgets.QMessageBox.Icon.Critical
        elif "低" in severity_raw or severity_lower in {"low", "info"}:
            icon = QtWidgets.QMessageBox.Icon.Warning
        title = payload.get("rule_name") or payload.get("rule_label") or "警報通知"
        details = []
        description = payload.get("description")
        if description:
            details.append(str(description))
        for line in payload.get("extra_lines") or []:
            if line:
                details.append(str(line))
        timestamp_text = payload.get("timestamp")
        if timestamp_text:
            details.append(f"發生時間：{timestamp_text}")
        camera_name = payload.get("camera_name")
        if camera_name:
            details.append(f"攝影機：{camera_name}")
        task_id = payload.get("task_id")
        if task_id:
            details.append(f"任務 ID：{task_id}")
        message = "\n".join(details) if details else "偵測到警報事件。"
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(message)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setModal(False)
        box.show()

    @QtCore.Slot(str)
    def handle_worker_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "執行錯誤", message)
        self.close()

    @QtCore.Slot()
    def handle_control_show(self) -> None:
        if self.isVisible():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    @QtCore.Slot()
    def handle_control_hide(self) -> None:
        self.hide()
        if self.status_bar:
            self.status_bar.showMessage("視窗已隱藏，偵測仍在運行。", 5000)

    @QtCore.Slot()
    def handle_control_shutdown(self) -> None:
        self._shutdown_requested = True
        self.close()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._current_pixmap is not None:
            self._set_video_pixmap(self._current_pixmap)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if (
            self._control_server
            and not self._shutdown_requested
            and self._start_hidden
            and not self._allow_window_close
        ):
            event.ignore()
            self.hide()
            if self.status_bar:
                self.status_bar.showMessage("視窗已隱藏，偵測仍在運行。", 5000)
            return

        self.worker.stop()
        self.worker.wait()
        if self._control_server:
            self._control_server.stop()
            self._control_server = None
        if self._parent_watcher:
            self._parent_watcher.stop()
            self._parent_watcher = None
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用 PySide6 顯示的即時人員偵測 GUI",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="0",
        help="攝影機索引或串流路徑（預設：0）。",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="yolo11n.pt",
        help="Ultralytics 模型權重檔（預設：yolo11n.pt）。",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="推論影像尺寸（預設：640）。",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.35,
        help="偵測信心值門檻（預設：0.35）。",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Ultralytics 推論裝置，例如 cpu 或 cuda:0。",
    )
    parser.add_argument(
        "--trace-length",
        type=int,
        default=30,
        help="TraceAnnotator 保留的歷史點數（預設：30）。",
    )
    parser.add_argument(
        "--heatmap-radius",
        type=int,
        default=40,
        help="熱圖累積半徑（預設：40）。",
    )
    parser.add_argument(
        "--heatmap-opacity",
        type=float,
        default=0.5,
        help="熱圖透明度，介於 0 到 1（預設：0.5）。",
    )
    parser.add_argument(
        "--blur-kernel",
        type=int,
        default=25,
        help="BlurAnnotator 使用的核大小（預設：25）。",
    )
    parser.add_argument(
        "--window-name",
        type=str,
        default="Supervision Live",
        help="GUI 視窗標題。",
    )
    parser.add_argument(
        "--task-id",
        type=int,
        required=True,
        help="分析任務的 ID（analysis_tasks.id）。",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="負責啟動偵測程式的後端行程 PID，用於意外終止時自動釋放資源。",
    )
    parser.add_argument(
        "--start-hidden",
        action="store_true",
        help="啟動時不顯示 GUI 視窗，但持續執行偵測。",
    )
    parser.add_argument(
        "--control-host",
        type=str,
        default="127.0.0.1",
        help="控制指令伺服器綁定位址（預設 127.0.0.1）。",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=None,
        help="控制指令伺服器埠號；指定後可遠端顯示/隱藏視窗或結束偵測。",
    )
    parser.add_argument(
        "--control-token",
        type=str,
        default=None,
        help="控制指令認證 token，若設定需在指令中提供相同值。",
    )
    parser.add_argument(
        "--allow-window-close",
        action="store_true",
        help="允許使用者關閉視窗並終止偵測（預設會改為隱藏）。",
    )
    parser.add_argument(
        "--enable-fall-alert",
        action="store_true",
        help="啟用跌倒警報邏輯，偵測到可疑姿態時輸出日誌。",
    )
    parser.add_argument(
        "--alert-rules",
        type=str,
        default=None,
        help="指定警報規則設定檔路徑。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(args)
    if args.control_port:
        control_server = ControlCommandServer(
            window,
            host=args.control_host,
            port=args.control_port,
            token=args.control_token,
        )
        window.attach_control_server(control_server)
        control_server.start()
    if not args.start_hidden:
        window.show()
    else:
        window.hide()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

