# 資料庫 ORM 模型 (SQLAlchemy)
# 與 deployment/db/init.sql 保持同步

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.core.config import settings
import json

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, reconstructor

Base = declarative_base()


def _safe_iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        try:
            display_tz = ZoneInfo(getattr(settings, "alert_display_timezone", "UTC"))
        except Exception:
            display_tz = None

        if display_tz:
            if getattr(value, "tzinfo", None):
                value = value.astimezone(display_tz)
            else:
                value = value.replace(tzinfo=display_tz)
        return value.isoformat()
    return str(value)


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    source_id = Column(Integer, ForeignKey("data_sources.id"), nullable=True)
    source_width = Column(Integer)
    source_height = Column(Integer)
    source_fps = Column(Float)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    task_name = Column(String(200))
    camera_id = Column(String(100))
    camera_name = Column(String(200))
    camera_type = Column(String(50))
    device_index = Column(Integer)
    model_path = Column(String(255))
    model_id = Column(String(100))
    confidence_threshold = Column(Float, default=0.5)
    iou_threshold = Column(Float, default=0.4)

    data_source = relationship("DataSource", back_populates="analysis_tasks")
    detection_results = relationship(
        "DetectionResult", back_populates="task", cascade="all, delete-orphan"
    )
    line_events = relationship(
        "LineCrossingEvent", back_populates="task", cascade="all, delete-orphan"
    )
    zone_events = relationship(
        "ZoneDwellEvent", back_populates="task", cascade="all, delete-orphan"
    )
    speed_events = relationship(
        "SpeedEvent", back_populates="task", cascade="all, delete-orphan"
    )
    statistics = relationship(
        "TaskStatistics",
        uselist=False,
        back_populates="task",
        cascade="all, delete-orphan",
    )

    def __init__(self, *args, **kwargs):
        source_info_payload = kwargs.pop("source_info", None)
        super().__init__(*args, **kwargs)
        self._source_info_cache: dict | None = None
        if source_info_payload is not None:
            self.source_info = source_info_payload

    @reconstructor
    def _init_on_load(self):
        self._source_info_cache = None

    def to_dict(self):
        return {
            "id": self.id,
            "task_type": self.task_type,
            "status": self.status,
            "source_id": self.source_id,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "source_fps": self.source_fps,
            "start_time": _safe_iso(self.start_time),
            "end_time": _safe_iso(self.end_time),
            "created_at": _safe_iso(self.created_at),
            "task_name": self.task_name,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "camera_type": self.camera_type,
            "device_index": self.device_index,
            "model_path": self.model_path,
            "model_id": self.model_id,
            "confidence_threshold": self.confidence_threshold,
            "iou_threshold": self.iou_threshold,
            "source_info": self.source_info,
        }

    def _normalize_source_info(self, payload):
        if payload is None:
            return {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return payload

    def _apply_source_info(self, info: dict) -> None:
        source_id = info.get("source_id")
        if source_id is not None:
            try:
                self.source_id = int(source_id)
            except (TypeError, ValueError):
                pass
        self.camera_id = info.get("camera_id", self.camera_id)
        self.camera_name = info.get("camera_name", self.camera_name)
        self.camera_type = info.get("camera_type", self.camera_type)
        self.device_index = info.get("device_index", self.device_index)
        self.model_path = info.get("model_path", self.model_path)
        self.model_id = info.get("model_id", self.model_id)
        self.confidence_threshold = info.get(
            "confidence", info.get("confidence_threshold", self.confidence_threshold)
        )
        self.iou_threshold = info.get("iou_threshold", self.iou_threshold)
        self.source_width = info.get("source_width", self.source_width)
        self.source_height = info.get("source_height", self.source_height)
        self.source_fps = info.get("source_fps", self.source_fps)
        if "task_name" in info and not self.task_name:
            self.task_name = info.get("task_name")

    @property
    def source_info(self) -> dict:
        if self._source_info_cache is None:
            self._source_info_cache = {
                "source_id": self.source_id,
                "camera_id": self.camera_id,
                "camera_name": self.camera_name,
                "camera_type": self.camera_type,
                "device_index": self.device_index,
                "model_path": self.model_path,
                "model_id": self.model_id,
                "confidence_threshold": self.confidence_threshold,
                "iou_threshold": self.iou_threshold,
                "source_width": self.source_width,
                "source_height": self.source_height,
                "source_fps": self.source_fps,
                "task_name": self.task_name,
            }
        return dict(self._source_info_cache)

    @source_info.setter
    def source_info(self, payload) -> None:
        info = self._normalize_source_info(payload)
        self._source_info_cache = info
        self._apply_source_info(info)


class DetectionResult(Base):
    __tablename__ = "detection_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        Integer, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False
    )
    tracker_id = Column(Integer)
    object_speed = Column(Float)
    zones = Column(JSON)
    frame_number = Column(Integer)
    frame_timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    object_type = Column(String(50))
    confidence = Column(Float)
    bbox_x1 = Column(Float)
    bbox_y1 = Column(Float)
    bbox_x2 = Column(Float)
    bbox_y2 = Column(Float)
    center_x = Column(Float)
    center_y = Column(Float)
    thumbnail_path = Column(String(255))

    task = relationship("AnalysisTask", back_populates="detection_results")

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tracker_id": self.tracker_id,
            "object_speed": self.object_speed,
            "zones": self.zones,
            "frame_number": self.frame_number,
            "frame_timestamp": _safe_iso(self.frame_timestamp),
            "object_type": self.object_type,
            "confidence": self.confidence,
            "bbox_x1": self.bbox_x1,
            "bbox_y1": self.bbox_y1,
            "bbox_x2": self.bbox_x2,
            "bbox_y2": self.bbox_y2,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "thumbnail_path": self.thumbnail_path,
        }


class LineCrossingEvent(Base):
    __tablename__ = "line_crossing_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    is_enabled = Column(Boolean, default=True)
    task_id = Column(
        Integer, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False
    )
    tracker_id = Column(Integer)
    line_id = Column(String(50), nullable=False)
    direction = Column(String(20))
    frame_number = Column(Integer)
    frame_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    extra = Column(JSON)

    task = relationship("AnalysisTask", back_populates="line_events")

    def to_dict(self):
        return {
            "id": self.id,
            "is_enabled": self.is_enabled,
            "task_id": self.task_id,
            "tracker_id": self.tracker_id,
            "line_id": self.line_id,
            "direction": self.direction,
            "frame_number": self.frame_number,
            "frame_timestamp": _safe_iso(self.frame_timestamp),
            "extra": self.extra,
        }


class ZoneDwellEvent(Base):
    __tablename__ = "zone_dwell_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    is_enabled = Column(Boolean, default=True)
    task_id = Column(
        Integer, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False
    )
    tracker_id = Column(Integer)
    zone_id = Column(String(50), nullable=False)
    entered_at = Column(DateTime)
    exited_at = Column(DateTime)
    dwell_seconds = Column(Float)
    frame_number = Column(Integer)
    event_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    extra = Column(JSON)

    task = relationship("AnalysisTask", back_populates="zone_events")

    def to_dict(self):
        return {
            "id": self.id,
            "is_enabled": self.is_enabled,
            "task_id": self.task_id,
            "tracker_id": self.tracker_id,
            "zone_id": self.zone_id,
            "entered_at": _safe_iso(self.entered_at),
            "exited_at": _safe_iso(self.exited_at),
            "dwell_seconds": self.dwell_seconds,
            "frame_number": self.frame_number,
            "event_timestamp": _safe_iso(self.event_timestamp),
            "extra": self.extra,
        }


class SpeedEvent(Base):
    __tablename__ = "speed_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    is_enabled = Column(Boolean, default=True)
    task_id = Column(
        Integer, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False
    )
    tracker_id = Column(Integer)
    speed_avg = Column(Float)
    speed_max = Column(Float)
    threshold = Column(Float)
    frame_number = Column(Integer)
    event_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    extra = Column(JSON)

    task = relationship("AnalysisTask", back_populates="speed_events")

    def to_dict(self):
        return {
            "id": self.id,
            "is_enabled": self.is_enabled,
            "task_id": self.task_id,
            "tracker_id": self.tracker_id,
            "speed_avg": self.speed_avg,
            "speed_max": self.speed_max,
            "threshold": self.threshold,
            "frame_number": self.frame_number,
            "event_timestamp": _safe_iso(self.event_timestamp),
            "extra": self.extra,
        }


class DataSource(Base):
    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    config = Column(JSON)
    status = Column(String(20), nullable=False, default="active")
    last_check = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    analysis_tasks = relationship("AnalysisTask", back_populates="data_source")

    def to_dict(self):
        return {
            "id": self.id,
            "source_type": self.source_type,
            "name": self.name,
            "config": self.config,
            "status": self.status,
            "last_check": _safe_iso(self.last_check),
            "created_at": _safe_iso(self.created_at),
        }


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="viewer")
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "last_login": _safe_iso(self.last_login),
            "created_at": _safe_iso(self.created_at),
        }


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(100), nullable=False)
    config_type = Column(String(50), nullable=False, default="kv")
    name = Column(String(200))
    camera_id = Column(String(100))
    config_value = Column(Text)
    payload = Column(JSON)
    description = Column(Text)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "config_key": self.config_key,
            "config_type": self.config_type,
            "name": self.name,
            "camera_id": self.camera_id,
            "config_value": self.config_value,
            "payload": self.payload,
            "description": self.description,
            "enabled": bool(self.enabled),
            "created_at": _safe_iso(self.created_at),
            "updated_at": _safe_iso(self.updated_at),
        }


class TaskStatistics(Base):
    __tablename__ = "task_statistics"

    task_id = Column(
        Integer, ForeignKey("analysis_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    fps = Column(Float)
    person_count = Column(Integer)
    avg_confidence = Column(Float)
    line_stats = Column(JSON)
    zone_stats = Column(JSON)
    speed_stats = Column(JSON)
    extra = Column(JSON)

    task = relationship("AnalysisTask", back_populates="statistics")

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "updated_at": _safe_iso(self.updated_at),
            "fps": self.fps,
            "person_count": self.person_count,
            "avg_confidence": self.avg_confidence,
            "line_stats": self.line_stats,
            "zone_stats": self.zone_stats,
            "speed_stats": self.speed_stats,
            "extra": self.extra,
        }


# 索引
Index("idx_analysis_tasks_status", AnalysisTask.status)
Index("idx_analysis_tasks_type", AnalysisTask.task_type)
Index("idx_analysis_tasks_created", AnalysisTask.created_at)
Index("idx_analysis_tasks_source", AnalysisTask.source_id)

Index("idx_detection_results_task", DetectionResult.task_id)
Index("idx_detection_results_tracker", DetectionResult.tracker_id)
Index("idx_detection_results_timestamp", DetectionResult.frame_timestamp)

Index("idx_line_events_task_line", LineCrossingEvent.task_id, LineCrossingEvent.line_id)
Index("idx_line_events_tracker", LineCrossingEvent.tracker_id)

Index("idx_zone_events_task_zone", ZoneDwellEvent.task_id, ZoneDwellEvent.zone_id)
Index("idx_zone_events_tracker", ZoneDwellEvent.tracker_id)

Index("idx_speed_events_task", SpeedEvent.task_id)
Index("idx_speed_events_tracker", SpeedEvent.tracker_id)

Index("idx_data_sources_status", DataSource.status)
Index("idx_data_sources_type", DataSource.source_type)

Index("idx_system_config_key", SystemConfig.config_key)

Index("idx_task_statistics_updated", TaskStatistics.updated_at)
