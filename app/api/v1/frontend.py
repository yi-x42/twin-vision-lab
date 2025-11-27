"""
YOLOv11 前端界面 API
專門為新的前端界面提供數據和功能支持
"""

import asyncio
import json
import os
import time
import base64
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union, Literal, Iterable
import subprocess
from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    BackgroundTasks,
    File,
    UploadFile,
    Query,
    WebSocket,
    WebSocketDisconnect,
    Request,
)
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, delete, select, text, func, desc, or_
from pydantic import BaseModel, Field

from app.core.database import get_db, AsyncSessionLocal, get_async_db
from app.core.logger import api_logger
from app.core.config import settings
from app.core.paths import get_base_dir
# from app.services.yolo_service import get_yolo_service  # 暫時註解
from app.services.camera_service import CameraService
from app.services.task_service import TaskService, get_task_service
from app.services.analytics_service import AnalyticsService
from app.services.new_database_service import DatabaseService
from app.services.camera_status_monitor import get_camera_monitor
from app.services.realtime_detection_service import realtime_detection_service
from app.services.gui_launcher import realtime_gui_manager
from app.services.notification_settings_service import (
    get_email_settings,
    update_email_settings,
)
from app.services.alert_rule_service import (
    list_rules as list_alert_rule_service,
    create_rule as create_alert_rule_service,
    toggle_rule as toggle_alert_rule_service,
    delete_rule as delete_alert_rule_service,
)
from app.services.fall_detection_service import fall_detection_service
from app.services.email_notification_service import send_fall_email_alert, send_test_email
from app.services.alert_runtime_store import (
    ensure_alert_runtime_file,
    load_alert_runtime_rules,
    save_alert_runtime_rules,
)
from app.models.database import AnalysisTask, DetectionResult, DataSource, TaskStatistics

router = APIRouter(prefix="/frontend", tags=["前端界面"])

UPLOADS_ROOT = get_base_dir() / "uploads"
ALERT_SNAPSHOT_DIR = UPLOADS_ROOT / "alerts" / "snapshots"
SNAPSHOT_SUFFIXES = {".jpg", ".jpeg", ".png"}

def _resolve_timezone(name: Optional[str], fallback: str) -> ZoneInfo:
    try:
        if name:
            return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        api_logger.warning(f"無法載入時區 {name}，改用 {fallback}")
    try:
        return ZoneInfo(fallback)
    except Exception:  # noqa: BLE001
        api_logger.warning(f"無法載入備援時區 {fallback}，使用 UTC")
        return ZoneInfo("UTC")

ALERT_SOURCE_TZ = _resolve_timezone(getattr(settings, "alert_source_timezone", "UTC"), "UTC")
ALERT_DISPLAY_TZ = _resolve_timezone(
    getattr(settings, "alert_display_timezone", None) or getattr(settings, "app_timezone", "Asia/Taipei"),
    "UTC",
)

ALERT_TYPE_LABELS = {
    "linecrossing": "越線警報",
    "zonedwell": "區域滯留警報",
    "speedanomaly": "速度異常警報",
    "crowdcount": "人數警報",
    "falldetection": "跌倒警報",
}

SEVERITY_LABELS = {
    "critical": "高",
    "high": "高",
    "medium": "中",
    "low": "低",
}

BACKUP_CONFIG_PATH = get_base_dir() / "data" / "database_backup_config.json"
DEFAULT_BACKUP_DIR = Path(r"C:/Users/yi_x/Downloads")


class BackupFileInfo(BaseModel):
    name: str
    size: int
    created_at: datetime


class DatabaseBackupSettings(BaseModel):
    backup_type: Literal["full", "incremental", "differential"] = "full"
    backup_location: str = str(DEFAULT_BACKUP_DIR)
    auto_backup_enabled: bool = False
    backup_frequency: Literal["hourly", "daily", "weekly", "monthly"] = "daily"
    retention_days: int = 30
    last_backup_time: Optional[datetime] = None
    last_backup_file: Optional[str] = None
    last_backup_size: Optional[int] = None


class DatabaseBackupInfo(DatabaseBackupSettings):
    database_size_bytes: int = 0
    total_record_estimate: int = 0
    recent_backups: List[BackupFileInfo] = []

class UpdateDatabaseBackupSettingsRequest(BaseModel):
    backup_type: Literal["full", "incremental", "differential"] = "full"
    backup_location: str = str(DEFAULT_BACKUP_DIR)
    auto_backup_enabled: bool = False
    backup_frequency: Literal["hourly", "daily", "weekly", "monthly"] = "daily"
    retention_days: int = 30


class ManualBackupResponse(BaseModel):
    message: str
    backup_file: str
    backup_path: str
    size: int
    download_url: str
    finished_at: datetime


class RestoreBackupResponse(BaseModel):
    message: str
    restored_at: datetime


def _resolve_backup_location(path_value: str) -> Path:
    clean_value = path_value.strip().strip("\"'")
    path = Path(clean_value).expanduser()
    if not path.is_absolute():
        path = get_base_dir() / path
    return path


def _load_backup_config() -> DatabaseBackupSettings:
    BACKUP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if BACKUP_CONFIG_PATH.exists():
        try:
            data = json.loads(BACKUP_CONFIG_PATH.read_text(encoding="utf-8"))
            if data.get("last_backup_time"):
                try:
                    data["last_backup_time"] = datetime.fromisoformat(data["last_backup_time"])
                except ValueError:
                    data["last_backup_time"] = None
            return DatabaseBackupSettings(**data)
        except Exception as exc:
            api_logger.warning(f"載入備份設定失敗，使用預設值: {exc}")
    default_settings = DatabaseBackupSettings()
    _save_backup_config(default_settings)
    return default_settings


def _save_backup_config(settings_model: DatabaseBackupSettings) -> None:
    BACKUP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = settings_model.model_dump()
    if payload.get("last_backup_time"):
        payload["last_backup_time"] = payload["last_backup_time"].isoformat()
    BACKUP_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _get_database_stats() -> Dict[str, int]:
    async with AsyncSessionLocal() as session:
        size_result = await session.execute(
            text("SELECT pg_database_size(current_database()) AS size_bytes")
        )
        size_bytes = int(size_result.scalar() or 0)

        total_rows = 0
        for table in ("analysis_tasks", "detection_results", "behavior_events"):
            try:
                rows_result = await session.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                )
                total_rows += int(rows_result.scalar() or 0)
            except Exception:
                continue
    return {"database_size_bytes": size_bytes, "total_record_estimate": total_rows}


def _collect_recent_backups(location: str, limit: int = 5) -> List[BackupFileInfo]:
    backup_dir = _resolve_backup_location(location)
    if not backup_dir.exists():
        return []
    files = [
        BackupFileInfo(
            name=item.name,
            size=item.stat().st_size,
            created_at=datetime.fromtimestamp(item.stat().st_mtime),
        )
        for item in backup_dir.glob("*.sql")
        if item.is_file()
    ]
    files.sort(key=lambda item: item.created_at, reverse=True)
    return files[:limit]


def _cleanup_expired_backups(location: str, retention_days: int) -> None:
    if retention_days <= 0:
        return
    backup_dir = _resolve_backup_location(location)
    if not backup_dir.exists():
        return
    threshold = datetime.now() - timedelta(days=retention_days)
    for file_path in backup_dir.glob("*.sql"):
        try:
            file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
            if file_time < threshold:
                file_path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def _build_pg_connection_uri() -> str:
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _build_backup_info(
    config: Optional[DatabaseBackupSettings] = None,
) -> DatabaseBackupInfo:
    settings_model = config or _load_backup_config()
    stats = await _get_database_stats()
    recent_files = _collect_recent_backups(settings_model.backup_location)
    return DatabaseBackupInfo(
        **settings_model.model_dump(),
        **stats,
        recent_backups=recent_files,
    )


def _pg_env() -> Dict[str, str]:
    env = os.environ.copy()
    if settings.postgres_password:
        env["PGPASSWORD"] = settings.postgres_password
    return env


def _docker_exec_prefix() -> List[str]:
    container = settings.postgres_container_name or os.getenv("POSTGRES_DOCKER_CONTAINER")
    if not container:
        raise FileNotFoundError("pg_dump not found and POSTGRES_CONTAINER_NAME 未設定")

    prefix = ["docker", "exec", "-i"]
    if settings.postgres_password:
        prefix.extend(["-e", f"PGPASSWORD={settings.postgres_password}"])
    prefix.append(container)
    return prefix


def _run_pg_dump_to_file(output_path: Path) -> None:
    env = _pg_env()
    base_cmd = [
        "pg_dump",
        "--clean",
        "--if-exists",
        "-h",
        settings.postgres_server,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
        settings.postgres_db,
    ]
    try:
        with open(output_path, "wb") as outfile:
            subprocess.run(base_cmd, stdout=outfile, check=True, env=env)
        return
    except FileNotFoundError:
        docker_cmd = _docker_exec_prefix() + [
            "pg_dump",
            "--clean",
            "--if-exists",
            "-h",
            "localhost",
            "-p",
            str(settings.postgres_port),
            "-U",
            settings.postgres_user,
            settings.postgres_db,
        ]
        with open(output_path, "wb") as outfile:
            subprocess.run(docker_cmd, stdout=outfile, check=True, env=_pg_env())


def _run_psql_restore_from_file(source_path: Path) -> None:
    env = _pg_env()
    base_cmd = [
        "psql",
        "-h",
        settings.postgres_server,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
        settings.postgres_db,
    ]
    try:
        with open(source_path, "rb") as infile:
            subprocess.run(base_cmd, stdin=infile, check=True, env=env)
        return
    except FileNotFoundError:
        docker_cmd = _docker_exec_prefix() + [
            "psql",
            "-h",
            "localhost",
            "-p",
            str(settings.postgres_port),
            "-U",
            settings.postgres_user,
            settings.postgres_db,
        ]
        with open(source_path, "rb") as infile:
            subprocess.run(docker_cmd, stdin=infile, check=True, env=_pg_env())


def _normalize_rule_type_label(rule_type: Optional[str]) -> str:
    normalized = "".join(ch for ch in str(rule_type or "") if ch.isalnum()).lower()
    if not normalized:
        return "自訂警報"
    return ALERT_TYPE_LABELS.get(normalized, "自訂警報")


def _normalize_severity_label(severity: Optional[str]) -> str:
    if severity is None:
        return "中"
    raw = str(severity).strip()
    if not raw:
        return "中"
    mapped = SEVERITY_LABELS.get(raw.lower())
    return mapped or raw


def _severity_bucket_key(value: Optional[str]) -> str:
    """將任意嚴重度描述映射到 high/medium/low 三種 bucket。"""
    if not value:
        return "medium"
    normalized = str(value).strip().lower()
    if normalized in {"高", "critical", "high"}:
        return "high"
    if normalized in {"低", "low"}:
        return "low"
    return "medium"


def _normalize_rule_type_key(rule_type: Optional[str]) -> str:
    normalized = "".join(ch for ch in str(rule_type or "") if ch.isalnum()).lower()
    return normalized or "custom"


def _relative_to_uploads(path: Path) -> Optional[str]:
    try:
        relative = path.resolve().relative_to(UPLOADS_ROOT)
    except Exception:  # noqa: BLE001
        return None
    return str(relative).replace("\\", "/")


def _parse_snapshot_timestamp(raw: str, fallback: float) -> datetime:
    try:
        return datetime.strptime(raw[:20], "%Y%m%d%H%M%S%f")
    except ValueError:
        return datetime.fromtimestamp(fallback)


def _resolve_alert_task_dirs(task_id: Optional[str]) -> List[Path]:
    if not ALERT_SNAPSHOT_DIR.exists():
        return []
    if task_id is not None:
        candidate = ALERT_SNAPSHOT_DIR / str(task_id)
        if candidate.exists() and candidate.is_dir():
            return [candidate]
        return []
    return [p for p in ALERT_SNAPSHOT_DIR.iterdir() if p.is_dir()]


def _collect_alert_snapshot_events(
    task_dirs: Iterable[Path],
    start_boundary: datetime,
    end_boundary: datetime,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    rule_cache: Dict[str, Dict[str, Any]] = {}
    for dir_path in task_dirs:
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        task_id_str = dir_path.name
        lookup = rule_cache.get(task_id_str)
        for file_path in dir_path.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in SNAPSHOT_SUFFIXES:
                continue
            stem = file_path.stem
            if "_" not in stem:
                continue
            rule_id, timestamp_raw = stem.split("_", 1)
            if not rule_id:
                continue
            triggered_at = _parse_snapshot_timestamp(timestamp_raw, file_path.stat().st_mtime)
            if triggered_at < start_boundary or triggered_at > end_boundary:
                continue
            if lookup is None:
                lookup = _build_rule_lookup(task_id_str)
                rule_cache[task_id_str] = lookup
            rule_payload = lookup.get(rule_id) if lookup else None
            events.append(
                {
                    "task_id": task_id_str,
                    "rule_id": rule_id,
                    "timestamp": triggered_at,
                    "rule_payload": rule_payload,
                }
            )
    return events


def _build_rule_lookup(task_id: str) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    rules = load_alert_runtime_rules(task_id)
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        if not rule_id:
            continue
        lookup[str(rule_id)] = rule
    return lookup


def _build_alert_description(
    rule_name: str,
    type_label: str,
    rule_payload: Optional[Dict[str, Any]],
) -> str:
    trigger_values = {}
    if rule_payload:
        trigger_values = (
            rule_payload.get("trigger_values")
            or rule_payload.get("trigger")
            or {}
        )
    if isinstance(trigger_values, dict) and trigger_values:
        parts: List[str] = []
        for key, value in trigger_values.items():
            if value in (None, ""):
                continue
            parts.append(f"{key}={value}")
        if parts:
            detail = "、".join(parts)
            return f"{rule_name or type_label} 觸發條件達成（{detail}）"
    return f"{rule_name or type_label} 規則被觸發，請儘速確認。"


def _has_fall_detection_rule(rules: Optional[List[Dict[str, Any]]]) -> bool:
    if not rules:
        return False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_type = str(rule.get("rule_type") or rule.get("type") or "").lower()
        normalized_type = rule_type.replace("-", "_")
        if normalized_type in {"fall_detection", "falldetection"}:
            return True
    return False


def _update_fall_detection_monitor(
    task: AnalysisTask,
    enable: bool,
    *,
    confidence: Optional[float] = None,
) -> None:
    task_id = str(task.id)
    if not enable:
        fall_detection_service.stop_monitoring(task_id)
        return

    source_info = task.source_info or {}
    if source_info.get("client_stream"):
        api_logger.info(
            f"任務 {task_id} 使用客戶端影像上傳，暫不支援跌倒偵測服務。",
        )
        fall_detection_service.stop_monitoring(task_id)
        return

    device_index = source_info.get("device_index")
    try:
        device_index_int = int(device_index)
    except (TypeError, ValueError):
        device_index_int = None

    if device_index_int is None:
        api_logger.warning(f"任務 {task_id} 缺少 device_index，無法啟動跌倒偵測。")
        fall_detection_service.stop_monitoring(task_id)
        return

    camera_identifier = (
        source_info.get("camera_id")
        or task.camera_id
        or source_info.get("source_id")
        or f"task_{task_id}"
    )
    if not camera_identifier:
        api_logger.warning(f"任務 {task_id} 無法判斷 camera_id，無法啟動跌倒偵測。")
        fall_detection_service.stop_monitoring(task_id)
        return

    confidence_value = (
        confidence
        if confidence is not None
        else source_info.get("confidence")
        or task.confidence_threshold
        or settings.fall_confidence_default
    )

    started = fall_detection_service.start_monitoring(
        task_id=task_id,
        camera_id=str(camera_identifier),
        device_index=device_index_int,
        confidence=confidence_value,
    )
    if not started:
        api_logger.warning(f"任務 {task_id} 的跌倒偵測服務啟動失敗，請檢查攝影機設定。")
# 全域變數：用於儲存網路速度測量的歷史數據
_network_measurement_cache = {
    "last_bytes": 0,
    "last_time": 0,
    "ethernet_interface": "乙太網路"  # 預設乙太網路介面名稱
}


def _build_thumbnail_url(request: Optional[Request], thumbnail_path: Optional[str]) -> Optional[str]:
    """將存放於 uploads 下的相對路徑轉成可供前端使用的 URL"""
    if not thumbnail_path:
        return None
    normalized = str(thumbnail_path).lstrip("/\\")
    if not normalized:
        return None
    base = str(request.base_url) if request else ""
    return f"{base}uploads/{normalized}" if base else f"/uploads/{normalized}"


def _normalize_datetime_input(value: Optional[datetime]) -> Optional[datetime]:
    """將帶時區的時間轉為 UTC 並移除 tzinfo，避免與資料庫欄位比較時出錯。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _convert_alert_timestamp(value: Optional[datetime]) -> Optional[datetime]:
    """將警報時間轉換為顯示用時區。"""
    if value is None:
        return None
    base = value
    if base.tzinfo is None:
        base = base.replace(tzinfo=ALERT_SOURCE_TZ)
    else:
        base = base.astimezone(ALERT_SOURCE_TZ)
    return base.astimezone(ALERT_DISPLAY_TZ)

# ===== 工具函數 =====

def get_ethernet_speed() -> float:
    """
    計算乙太網路的即時速度 (MB/s)
    使用時間間隔測量來計算真正的網路傳輸速度
    """
    import time
    
    try:
        import psutil
        current_time = time.time()
        
        # 嘗試獲取乙太網路介面的統計數據
        net_io_per_nic = psutil.net_io_counters(pernic=True)
        ethernet_stats = None
        
        # 尋找乙太網路介面 (支援不同的命名方式)
        possible_ethernet_names = ["乙太網路", "Ethernet", "eth0", "以太网"]
        for name in possible_ethernet_names:
            if name in net_io_per_nic:
                ethernet_stats = net_io_per_nic[name]
                _network_measurement_cache["ethernet_interface"] = name
                break
        
        if not ethernet_stats:
            # 如果找不到乙太網路介面，使用總統計
            ethernet_stats = psutil.net_io_counters()
        
        # 計算當前總流量
        current_bytes = ethernet_stats.bytes_sent + ethernet_stats.bytes_recv
        
        # 檢查是否有歷史數據
        if _network_measurement_cache["last_time"] > 0:
            # 計算時間差和流量差
            time_diff = current_time - _network_measurement_cache["last_time"]
            bytes_diff = current_bytes - _network_measurement_cache["last_bytes"]
            
            # 確保時間間隔合理 (至少0.5秒，避免除零錯誤)
            if time_diff >= 0.5:
                # 計算速度 (bytes/s -> MB/s)
                speed_bps = bytes_diff / time_diff
                speed_mbps = speed_bps / (1024 * 1024)
                
                # 更新快取
                _network_measurement_cache["last_bytes"] = current_bytes
                _network_measurement_cache["last_time"] = current_time
                
                # 回傳速度，限制在合理範圍內 (0-1000 MB/s)
                return max(0.0, min(1000.0, speed_mbps))
        
        # 初次測量或時間間隔太短，更新快取並回傳0
        _network_measurement_cache["last_bytes"] = current_bytes
        _network_measurement_cache["last_time"] = current_time
        return 0.0
        
    except Exception as e:
        api_logger.warning(f"獲取乙太網路速度失敗: {e}")
        return 0.0

def find_models_directory() -> Optional[Path]:
    """
    尋找模型資料夾：優先使用專案根目錄 `uploads/models`。

    搜尋優先序：
    1) 自 `__file__` 向上搜尋，找到包含 `uploads/models` 的專案根目錄
    2) 檢查當前工作目錄下的 `uploads/models`
    3) 讀取設定值 `settings.models_directory`（如有）
    4) 使用環境變數 `YOLO_MODELS_DIR` 或 `MODELS_DIR`
    """
    current_file = Path(__file__).resolve()

    # 優先：沿父層尋找 uploads/models
    for parent in current_file.parents:
        candidate = parent / "uploads" / "models"
        if candidate.exists() and candidate.is_dir():
            return candidate

    # 其次：當前工作目錄
    cwd_candidate = Path.cwd() / "uploads" / "models"
    if cwd_candidate.exists() and cwd_candidate.is_dir():
        return cwd_candidate

    # 設定檔中的指定路徑（如有）
    try:
        from app.core.config import get_settings
        settings = get_settings()
        if getattr(settings, "models_directory", None):
            models_dir = Path(settings.models_directory)
            if models_dir.exists() and models_dir.is_dir():
                return models_dir
    except Exception:
        pass

    # 環境變數回退
    for env_var in ["YOLO_MODELS_DIR", "MODELS_DIR"]:
        env_models_dir = os.environ.get(env_var)
        if env_models_dir:
            models_dir = Path(env_models_dir)
            if models_dir.exists() and models_dir.is_dir():
                return models_dir

    api_logger.warning("無法找到模型資料夾（預期：專案 uploads/models）")
    return None

def find_videos_directory() -> Optional[Path]:
    """
    尋找影片資料夾：優先使用專案根目錄 `uploads/videos`。

    搜尋優先序：
    1) 自 `__file__` 向上搜尋 `uploads/videos`
    2) 檢查 `Path.cwd()/uploads/videos`
    3) 設定值 `settings.videos_directory`（如存在）
    4) 環境變數 `VIDEOS_DIR` 或 `UPLOADS_VIDEOS_DIR`
    """
    current_file = Path(__file__).resolve()

    for parent in current_file.parents:
        candidate = parent / "uploads" / "videos"
        if candidate.exists() and candidate.is_dir():
            return candidate

    cwd_candidate = Path.cwd() / "uploads" / "videos"
    if cwd_candidate.exists() and cwd_candidate.is_dir():
        return cwd_candidate

    try:
        from app.core.config import get_settings
        settings = get_settings()
        if getattr(settings, "videos_directory", None):
            videos_dir = Path(settings.videos_directory)
            if videos_dir.exists() and videos_dir.is_dir():
                return videos_dir
    except Exception:
        pass

    for env_var in ["VIDEOS_DIR", "UPLOADS_VIDEOS_DIR"]:
        env_dir = os.environ.get(env_var)
        if env_dir:
            p = Path(env_dir)
            if p.exists() and p.is_dir():
                return p

    api_logger.warning("無法找到影片資料夾（預期：專案 uploads/videos）")
    return None

# ===== 模型清單相關模型 =====

class ModelFileInfo(BaseModel):
    """YOLO 模型檔案資訊"""
    id: str
    name: str
    modelType: str
    parameterCount: str
    fileSize: str
    status: str
    size: int
    created_at: float
    modified_at: float
    path: str

# ===== 數據模型 =====

class SystemStats(BaseModel):
    """系統統計數據模型"""
    cpu_usage: float = Field(..., description="CPU使用率")
    memory_usage: float = Field(..., description="記憶體使用率")
    gpu_usage: float = Field(..., description="GPU使用率")
    network_usage: float = Field(..., description="乙太網路即時速度 (MB/s)")
    active_tasks: int = Field(..., description="活躍任務數")
    system_uptime_seconds: int = Field(..., description="系統運行總秒數")
    total_cameras: int = Field(0, description="攝影機總數")
    online_cameras: int = Field(0, description="線上攝影機數量")
    total_alerts_today: int = Field(0, description="今日警報總數")
    alerts_vs_yesterday: int = Field(0, description="與昨日比較的警報變化百分比")
    last_updated: datetime = Field(..., description="最後更新時間")

class TaskCreate(BaseModel):
    """任務創建模型"""
    name: str = Field(..., description="任務名稱")
    task_type: str = Field(..., description="任務類型: realtime/batch/scheduled/event")
    camera_id: Optional[str] = Field(None, description="攝影機ID")
    model_name: str = Field("yolov11s", description="YOLO模型名稱")
    confidence: float = Field(0.5, description="信心度閾值")
    iou_threshold: float = Field(0.45, description="IoU閾值")
    schedule_time: Optional[datetime] = Field(None, description="排程時間")
    description: str = Field("", description="任務描述")

class RealtimeAnalysisRequest(BaseModel):
    """即時分析請求模型"""
    task_name: str = Field(..., description="任務名稱")
    camera_id: str = Field(..., description="選中的攝影機ID")
    model_id: str = Field(..., description="選中的YOLO模型ID")
    confidence: float = Field(0.5, description="信心度閾值", ge=0.0, le=1.0)
    iou_threshold: float = Field(0.45, description="IoU閾值", ge=0.0, le=1.0)
    description: str = Field("", description="任務描述")
    client_stream: bool = Field(False, description="是否由客戶端上傳影像進行分析")
    alert_rules: Optional[List[Dict[str, Any]]] = Field(
        None, description="啟動任務時要套用的警報規則"
    )

class RealtimeAnalysisResponse(BaseModel):
    """即時分析回應模型"""
    task_id: str = Field(..., description="任務ID")
    status: str = Field(..., description="任務狀態")
    message: str = Field(..., description="回應訊息")
    camera_info: Dict[str, Any] = Field(..., description="攝影機資訊")
    model_info: Dict[str, Any] = Field(..., description="模型資訊")
    created_at: datetime = Field(..., description="創建時間")
    websocket_url: Optional[str] = Field(None, description="即時預覽 WebSocket URL")
    client_stream: bool = Field(False, description="是否需要客戶端上傳影像")


class PreviewLaunchRequest(BaseModel):
    """GUI 預覽啟動請求"""
    source_override: Optional[str] = Field(None, description="手動指定影像來源（USB index 或 RTSP URL）")
    model_override: Optional[str] = Field(None, description="覆寫模型路徑")
    imgsz: Optional[int] = Field(None, description="推論影像尺寸")
    confidence: Optional[float] = Field(None, description="信心閾值")
    device: Optional[str] = Field(None, description="推論裝置，例如 cpu 或 cuda:0")
    alert_rules: Optional[List[Dict[str, Any]]] = Field(None, description="啟用的警報規則列表")


class PreviewLaunchResponse(BaseModel):
    """GUI 預覽啟動回應"""
    task_id: int
    pid: int
    already_running: bool
    message: str


class TaskAlertRuleUpdate(BaseModel):
    """任務警報綁定更新請求"""
    rules: List[Dict[str, Any]] = Field(default_factory=list, description="套用於任務的警報規則")
    log_path: Optional[str] = Field(None, description="GUI 子行程輸出日誌路徑")


class EmailNotificationSettings(BaseModel):
    """郵件通知設定"""
    enabled: bool = Field(False, description="是否啟用郵件通知")
    address: str = Field("", description="收件者電子郵件")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="跌倒偵測信心門檻")
    cooldown_seconds: int = Field(
        30, ge=5, le=3600, description="兩次通知最小間隔秒數"
    )


class EmailNotificationUpdate(BaseModel):
    enabled: Optional[bool] = Field(None)
    address: Optional[str] = Field(None)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    cooldown_seconds: Optional[int] = Field(None, ge=5, le=3600)


class AlertRuleAction(BaseModel):
    email: bool = False
    push: bool = False
    sms: bool = False


class AlertRuleBase(BaseModel):
    name: str
    rule_type: str
    severity: str = Field("中", description="嚴重程度")
    cameras: List[str] = Field(default_factory=list)
    trigger_values: Dict[str, Any] = Field(default_factory=dict)
    actions: AlertRuleAction = Field(default_factory=AlertRuleAction)


class AlertRuleResponse(AlertRuleBase):
    id: str
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class AlertRuleToggleRequest(BaseModel):
    enabled: bool


class TriggeredAlertResponse(BaseModel):
    """觸發中的警報事件"""
    id: str
    task_id: Optional[int]
    rule_id: str
    rule_name: str
    rule_type: str
    type: str = Field(..., description="顯示用的警報類型標籤")
    severity: str
    status: str = "未處理"
    description: str
    timestamp: datetime
    camera: Optional[str]
    snapshot_url: Optional[str]
    assignee: Optional[str] = None


class EmailNotificationTestRequest(BaseModel):
    address: Optional[str] = Field(None, description="若提供則使用此收件者；否則使用配置檔")

class TaskInfo(BaseModel):
    """任務資訊模型"""
    id: str
    name: str
    task_type: str
    status: str
    progress: float
    camera_id: Optional[str]
    model_name: str
    start_time: datetime
    end_time: Optional[datetime]
    created_at: datetime

class CameraInfo(BaseModel):
    """攝影機資訊模型"""
    id: str
    name: str
    status: str  # online/offline
    camera_type: str  # USB/Network
    resolution: str
    fps: int
    group_id: Optional[str]

class ModelRequest(BaseModel):
    """模型操作請求模型"""
    model_id: Optional[str] = Field(None, description="模型ID")
    model_name: Optional[str] = Field(None, description="模型名稱（向後兼容）")
    
    def get_model_identifier(self) -> str:
        """獲取模型識別符，優先使用 model_id，否則使用 model_name"""
        if self.model_id:
            return self.model_id
        elif self.model_name:
            return self.model_name
        else:
            raise ValueError("必須提供 model_id 或 model_name")

class ModelConfigUpdate(BaseModel):
    """模型配置更新模型"""
    confidence: Optional[float] = Field(None, description="信心度閾值")
    iou_threshold: Optional[float] = Field(None, description="IoU閾值")
    image_size: Optional[int] = Field(None, description="輸入圖像大小")

class AnalyticsData(BaseModel):
    """分析數據模型"""
    detection_counts: Dict[str, int]
    hourly_trend: List[Dict[str, Any]]
    category_distribution: Dict[str, int]
    time_period_analysis: Dict[str, int]

class CameraPerformanceItem(BaseModel):
    camera_name: str = Field(..., description="攝影機名稱")
    camera_id: Optional[str] = Field(None, description="攝影機唯一識別 ID")
    detections: int = Field(0, description="指定期間內的偵測次數")
    runtime_hours: float = Field(0.0, description="指定期間內的運行小時數")
    status: Optional[str] = Field(None, description="最近一次任務的狀態")
    last_active: Optional[datetime] = Field(None, description="最後一次活動時間")


class AlertTrendPoint(BaseModel):
    date: str = Field(..., description="日期（ISO 8601）")
    high: int = Field(0, ge=0, description="高級別警報數")
    medium: int = Field(0, ge=0, description="中級別警報數")
    low: int = Field(0, ge=0, description="低級別警報數")


class AlertCategoryStat(BaseModel):
    rule_type: str = Field(..., description="警報規則類型")
    label: str = Field(..., description="顯示名稱")
    count: int = Field(0, ge=0, description="指定期間內的警報次數")


class HourlyActivityPoint(BaseModel):
    hour: str = Field(..., description="時間點 (例如 05/17 13:00)")
    count: int = Field(0, ge=0, description="該時段的偵測次數")


class HourlyActivitySummary(BaseModel):
    peak_hour: Optional[str] = Field(None, description="偵測量最高的時段")
    peak_count: int = Field(0, ge=0, description="高峰時段的偵測次數")
    low_hour: Optional[str] = Field(None, description="偵測量最低的時段")
    low_count: int = Field(0, ge=0, description="低峰時段的偵測次數")
    average_per_hour: float = Field(0.0, ge=0.0, description="每小時平均偵測次數")


class HourlyActivityResponse(BaseModel):
    data: List[HourlyActivityPoint]
    summary: HourlyActivitySummary

# ===== 資料來源管理模型 =====

class DataSourceCreate(BaseModel):
    """創建資料來源模型"""
    source_type: str = Field(..., description="資料來源類型: camera/video_file/image_folder")
    name: str = Field(..., description="資料來源名稱")
    config: Dict[str, Any] = Field(..., description="配置資訊")
    
class DataSourceUpdate(BaseModel):
    """更新資料來源模型"""
    name: Optional[str] = Field(None, description="資料來源名稱")
    config: Optional[Dict[str, Any]] = Field(None, description="配置資訊")
    status: Optional[str] = Field(None, description="狀態: active/inactive/error")

class DataSourceInfo(BaseModel):
    """資料來源資訊模型"""
    id: int
    source_type: str
    name: str
    config: Dict[str, Any]
    status: str
    last_check: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True

class CameraConfig(BaseModel):
    """攝影機配置模型"""
    device_id: Optional[int] = Field(None, description="USB攝影機裝置ID")
    url: Optional[str] = Field(None, description="網路攝影機URL")
    resolution: str = Field("1280x720", description="解析度")
    fps: int = Field(30, description="幀率")
    
class VideoFileConfig(BaseModel):
    """影片檔案配置模型"""
    file_path: str = Field(..., description="影片檔案路徑")
    auto_loop: bool = Field(False, description="自動循環播放")
    
class ImageFolderConfig(BaseModel):
    """圖片資料夾配置模型"""
    folder_path: str = Field(..., description="圖片資料夾路徑")
    supported_formats: List[str] = Field(["jpg", "jpeg", "png", "bmp"], description="支援的格式")
    scan_subdirs: bool = Field(False, description="掃描子資料夾")

# ===== YOLO 模型輔助函式 =====

def get_model_info_from_filename(filename: str, file_size: int) -> dict:
    """根據檔案名稱推斷模型資訊"""
    # 模型參數映射表
    model_params = {
        'yolo11n': {'params': '2.6M', 'type': '物體偵測'},
        'yolo11s': {'params': '9.4M', 'type': '物體偵測'},
        'yolo11m': {'params': '20.1M', 'type': '物體偵測'},
        'yolo11l': {'params': '25.3M', 'type': '物體偵測'},
        'yolo11x': {'params': '56.9M', 'type': '物體偵測'},
    }
    
    # 預設值
    model_type = "物體偵測"
    param_count = "未知"
    
    # 解析檔案名稱
    basename = filename.replace('.pt', '').lower()
    model_id = filename.replace('.pt', '')
    
    # 檢查是否為已知模型
    for model_key, info in model_params.items():
        if model_key in basename:
            param_count = info['params']
            model_type = info['type']
            break
    
    # 從快取取得狀態，如果沒有則預設為 inactive，除非是 yolo11n
    if model_id not in model_status_cache:
        if 'yolo11n' in basename:
            model_status_cache[model_id] = "active"
        else:
            model_status_cache[model_id] = "inactive"
    
    status = model_status_cache[model_id]
    
    return {
        'modelType': model_type,
        'parameterCount': param_count,
        'fileSize': f"{file_size / (1024 * 1024):.1f} MB",
        'status': status
    }

# ===== YOLO 模型清單 API =====

@router.get("/models/list", response_model=List[ModelFileInfo])
async def list_yolo_models():
    """列出專案 `uploads/models` 資料夾下所有模型檔案"""
    try:
        # 自動尋找專案 `uploads/models` 模型資料夾
        model_dir = find_models_directory()
        
        # 檢查資料夾是否存在
        if not model_dir or not model_dir.exists():
            api_logger.warning(f"模型資料夾不存在: {model_dir}")
            return []
        
        model_files = []
        
        # 掃描 .pt 檔案
        for file_path in model_dir.iterdir():
            if file_path.suffix == '.pt' and file_path.is_file():
                stat = file_path.stat()
                
                # 根據檔案名稱推斷模型資訊
                model_info = get_model_info_from_filename(file_path.name, stat.st_size)
                
                model_files.append(ModelFileInfo(
                    id=file_path.stem,  # 檔案名稱（不含副檔名）
                    name=file_path.name,  # 完整檔案名稱
                    modelType=model_info['modelType'],
                    parameterCount=model_info['parameterCount'],
                    fileSize=model_info['fileSize'],
                    status=model_info['status'],
                    size=stat.st_size,
                    created_at=stat.st_ctime,
                    modified_at=stat.st_mtime,
                    path=str(file_path)  # 轉為字串
                ))
        
        api_logger.info(f"找到 {len(model_files)} 個模型檔案")
        return model_files
        
    except Exception as e:
        api_logger.error(f"列出模型檔案時發生錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"無法取得模型清單: {str(e)}")

# ===== 模型狀態管理 API =====

# 全域變數來儲存模型狀態（實際專案中應該使用資料庫）
model_status_cache = {}

@router.post("/models/{model_id}/toggle")
async def toggle_model_status(model_id: str):
    """切換模型啟用狀態"""
    try:
        # 取得當前狀態
        current_status = model_status_cache.get(model_id, "inactive")
        
        # 切換狀態（允許多個模型同時啟用）
        new_status = "active" if current_status == "inactive" else "inactive"
        
        model_status_cache[model_id] = new_status
        
        api_logger.info(f"模型 {model_id} 狀態切換為: {new_status}")
        
        return {
            "success": True,
            "model_id": model_id,
            "new_status": new_status,
            "message": f"模型已{'啟用' if new_status == 'active' else '停用'}"
        }
        
    except Exception as e:
        api_logger.error(f"切換模型狀態時發生錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"無法切換模型狀態: {str(e)}")

@router.get("/models/active", response_model=List[ModelFileInfo])
async def get_active_models():
    """取得已啟用的模型清單（供其他功能使用）"""
    try:
        # 先獲取所有模型
        all_models = await list_yolo_models()
        
        # 只回傳已啟用的模型
        active_models = [model for model in all_models if model.status == "active"]
        
        api_logger.info(f"找到 {len(active_models)} 個已啟用的模型")
        return active_models
        
    except Exception as e:
        api_logger.error(f"取得已啟用模型時發生錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"無法取得已啟用模型: {str(e)}")

# ===== 系統狀態 API =====

@router.get("/stats", response_model=SystemStats)
async def get_system_stats(db: AsyncSession = Depends(get_db)):
    """獲取系統統計數據"""
    try:
        import psutil
        
        # 獲取真實的系統監控數據
        cpu_usage = psutil.cpu_percent()  # 移除 interval=1 避免阻塞
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        
        # 嘗試獲取 GPU 使用率（如果有 GPU 監控庫）
        gpu_usage = 0.0
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu_usage = gpus[0].load * 100
        except ImportError:
            # 如果沒有 GPU 監控庫，使用 nvidia-smi 備用方案
            try:
                import subprocess
                result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    gpu_usage = float(result.stdout.strip())
            except:
                gpu_usage = 0.0
        
        # 獲取乙太網路即時速度 (MB/s)
        network_usage = get_ethernet_speed()
        
        # 從資料庫獲取活躍任務數和攝影機統計
        active_tasks = 0
        total_cameras = 0
        online_cameras = 0
        
        try:
            # 獲取活躍任務數
            from sqlalchemy import select, func
            from app.models.database import AnalysisTask, DataSource
            
            api_logger.info("開始獲取系統統計數據...")
            
            # 計算活躍任務
            active_tasks_result = await db.execute(
                select(func.count(AnalysisTask.id)).where(
                    AnalysisTask.status.in_(['running', 'pending'])
                )
            )
            active_tasks = active_tasks_result.scalar() or 0
            api_logger.info(f"活躍任務數: {active_tasks}")
            
            # 計算攝影機總數 - 從 data_sources 表中的攝影機類型資料來源
            total_cameras_result = await db.execute(
                select(func.count(DataSource.id)).where(
                    DataSource.source_type == 'camera'
                )
            )
            total_cameras = total_cameras_result.scalar() or 0
            api_logger.info(f"資料庫中攝影機總數: {total_cameras}")
            
            # 獲取線上攝影機數量 - 簡化實作，暫時假設所有攝影機都在線
            # 後續可以優化為真正的即時檢測
            online_cameras = total_cameras
            api_logger.info(f"攝影機數量統計: 總數={total_cameras}, 線上={online_cameras}")
            
        except Exception as db_error:
            api_logger.error(f"無法從資料庫獲取統計數據: {db_error}")
            api_logger.exception("詳細錯誤信息:")

        # 獲取系統運行時間
        from app.core.uptime import get_system_uptime
        uptime_seconds = get_system_uptime()
        
        api_logger.info(f"準備構造SystemStats: total_cameras={total_cameras}, online_cameras={online_cameras}")
        
        try:
            stats = SystemStats(
                cpu_usage=round(cpu_usage, 1),
                memory_usage=round(memory_usage, 1),
                gpu_usage=round(gpu_usage, 1),
                network_usage=round(network_usage, 2),
                active_tasks=active_tasks,
                system_uptime_seconds=int(uptime_seconds),
                total_cameras=total_cameras,
                online_cameras=online_cameras,
                total_alerts_today=0,  # 暫時設為0，可以後續擴展
                alerts_vs_yesterday=0,  # 暫時設為0，可以後續擴展
                last_updated=datetime.now()
            )
            api_logger.info(f"✅ SystemStats 構造成功: {stats}")
        except Exception as stats_error:
            api_logger.error(f"❌ SystemStats 構造失敗: {stats_error}")
            # 提供基本的fallback統計數據
            stats = SystemStats(
                cpu_usage=round(cpu_usage, 1),
                memory_usage=round(memory_usage, 1),
                gpu_usage=round(gpu_usage, 1),
                network_usage=round(network_usage, 2),
                active_tasks=0,
                system_uptime_seconds=int(uptime_seconds),
                total_cameras=0,
                online_cameras=0,
                total_alerts_today=0,
                alerts_vs_yesterday=0,
                last_updated=datetime.now()
            )
        
        return stats
        
    except Exception as e:
        api_logger.error(f"獲取系統統計數據失敗: {e}")
        raise HTTPException(status_code=500, detail=f"系統統計數據獲取失敗: {str(e)}")


@router.get(
    "/alerts/notification-settings/email",
    response_model=EmailNotificationSettings,
)
async def get_email_notification_settings_api():
    """取得郵件通知設定。"""
    settings_data = get_email_settings()
    return EmailNotificationSettings(**settings_data)


@router.put(
    "/alerts/notification-settings/email",
    response_model=EmailNotificationSettings,
)
async def update_email_notification_settings_api(
    payload: EmailNotificationUpdate,
):
    """更新郵件通知設定。"""
    updated = update_email_settings(payload.dict(exclude_none=True))
    return EmailNotificationSettings(**updated)


@router.get("/alerts/rules", response_model=List[AlertRuleResponse])
async def list_alert_rules_api(db: AsyncSession = Depends(get_db)):
    """取得所有警報規則。"""
    rules = await list_alert_rule_service(db)
    return [AlertRuleResponse(**rule) for rule in rules]


@router.post("/alerts/rules", response_model=AlertRuleResponse)
async def create_alert_rule_api(payload: AlertRuleBase, db: AsyncSession = Depends(get_db)):
    """新增警報規則。"""
    rule = await create_alert_rule_service(db, payload.dict())
    return AlertRuleResponse(**rule)


@router.patch("/alerts/rules/{rule_id}/toggle", response_model=AlertRuleResponse)
async def toggle_alert_rule_api(
    rule_id: str, payload: AlertRuleToggleRequest, db: AsyncSession = Depends(get_db)
):
    """切換警報規則啟用狀態。"""
    rule = await toggle_alert_rule_service(db, rule_id, payload.enabled)
    if not rule:
        raise HTTPException(status_code=404, detail="找不到警報規則")
    return AlertRuleResponse(**rule)


@router.delete("/alerts/rules/{rule_id}")
async def delete_alert_rule_api(rule_id: str, db: AsyncSession = Depends(get_db)):
    """刪除警報規則。"""
    success = await delete_alert_rule_service(db, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="找不到警報規則")
    return {"success": True}


@router.post("/alerts/notification-settings/email/test")
async def send_email_notification_test(
    payload: EmailNotificationTestRequest,
):
    """手動發送一封測試郵件給目前設定的收件者。"""
    settings_data = get_email_settings()
    receiver = (payload.address or "").strip() or settings_data.get("address")
    if not receiver:
        raise HTTPException(status_code=400, detail="請先設定郵件地址再測試")
    if not settings.smtp_username or not settings.smtp_password:
        raise HTTPException(status_code=400, detail="SMTP 帳號或密碼未設定")

    success = send_test_email(receiver)
    if not success:
        raise HTTPException(status_code=500, detail="測試郵件寄送失敗，請檢查 SMTP 設定")
    return {"success": True, "message": f"測試郵件已寄出至 {receiver}"}


@router.get("/alerts/active", response_model=List[TriggeredAlertResponse])
async def list_active_alerts_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=200, description="最多回傳的警報數量"),
    task_id: Optional[str] = Query(None, description="只查看指定任務的警報"),
):
    """列出最近觸發的警報（依照快照檔案排序）。"""
    if not ALERT_SNAPSHOT_DIR.exists():
        return []

    target_dirs: List[Path] = []
    if task_id is not None:
        candidate = ALERT_SNAPSHOT_DIR / str(task_id)
        if not candidate.exists() or not candidate.is_dir():
            return []
        target_dirs.append(candidate)
    else:
        target_dirs = [p for p in ALERT_SNAPSHOT_DIR.iterdir() if p.is_dir()]

    events: List[Dict[str, Any]] = []
    for dir_path in target_dirs:
        for file_path in dir_path.iterdir():
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in SNAPSHOT_SUFFIXES:
                continue
            stem = file_path.stem
            if "_" not in stem:
                continue
            rule_id, timestamp_raw = stem.split("_", 1)
            rule_id = rule_id.strip()
            if not rule_id:
                continue
            mtime = file_path.stat().st_mtime
            triggered_at = _parse_snapshot_timestamp(timestamp_raw, mtime)
            display_timestamp = _convert_alert_timestamp(triggered_at) or triggered_at
            events.append(
                {
                    "task_id": dir_path.name,
                    "file_name": file_path.name,
                    "path": file_path,
                    "timestamp": display_timestamp,
                    "rule_id": rule_id,
                }
            )

    if not events:
        return []

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    trimmed_events = events[:limit]

    task_ids = {item["task_id"] for item in trimmed_events}
    task_info_map: Dict[str, Dict[str, Any]] = {}
    db_task_ids: List[int] = []
    for task_id_str in task_ids:
        try:
            db_task_ids.append(int(task_id_str))
        except ValueError:
            continue

    if db_task_ids:
        result = await db.execute(
            select(
                AnalysisTask.id,
                AnalysisTask.camera_name,
                AnalysisTask.task_name,
            ).where(AnalysisTask.id.in_(db_task_ids))
        )
        for row in result.all():
            task_info_map[str(row.id)] = {
                "task_id": row.id,
                "camera": row.camera_name or row.task_name or f"任務 {row.id}",
            }

    rule_cache: Dict[str, Dict[str, Any]] = {}
    responses: List[TriggeredAlertResponse] = []
    for event in trimmed_events:
        task_id_str = event["task_id"]
        rule_lookup = rule_cache.get(task_id_str)
        if rule_lookup is None:
            rule_lookup = _build_rule_lookup(task_id_str)
            rule_cache[task_id_str] = rule_lookup
        rule_payload = rule_lookup.get(event["rule_id"])

        raw_rule_type = (
            rule_payload.get("rule_type") if rule_payload else None
        ) or "custom"
        type_label = _normalize_rule_type_label(raw_rule_type)
        rule_name = (
            (rule_payload.get("name") if rule_payload else None)
            or type_label
            or event["rule_id"]
        )
        severity = _normalize_severity_label(
            rule_payload.get("severity") if rule_payload else None
        )
        description = _build_alert_description(rule_name, type_label, rule_payload)

        task_info = task_info_map.get(task_id_str)
        camera_label = (
            task_info.get("camera") if task_info else f"任務 {task_id_str}"
        )
        task_id_value = task_info.get("task_id") if task_info else None

        relative_path = _relative_to_uploads(event["path"])
        snapshot_url = (
            _build_thumbnail_url(request, relative_path) if relative_path else None
        )

        responses.append(
            TriggeredAlertResponse(
                id=f"{task_id_str}-{event['file_name']}",
                task_id=task_id_value,
                rule_id=event["rule_id"],
                rule_name=rule_name,
                rule_type=str(raw_rule_type),
                type=type_label,
                severity=severity,
                status="未處理",
                description=description,
                timestamp=event["timestamp"],
                camera=camera_label,
                snapshot_url=snapshot_url,
            )
        )

    return responses

@router.get("/detection-summary")
async def get_detection_summary(db: AsyncSession = Depends(get_db)):
    """獲取檢測結果摘要"""
    try:
        # 實際從資料庫查詢最近檢測結果
        from sqlalchemy import func, desc
        from sqlalchemy.future import select
        
        # 查詢最近24小時的檢測結果
        yesterday = datetime.now() - timedelta(days=1)
        
        # 總檢測數
        total_query = select(func.count(DetectionResult.id)).where(
            DetectionResult.frame_timestamp >= yesterday
        )
        total_result = await db.execute(total_query)
        total_detections = total_result.scalar() or 0
        
        # 按類別統計
        category_query = select(
            DetectionResult.class_name,
            func.count(DetectionResult.id).label('count')
        ).where(
            DetectionResult.frame_timestamp >= yesterday
        ).group_by(DetectionResult.class_name)
        
        category_result = await db.execute(category_query)
        category_counts = {row.class_name: row.count for row in category_result}
        
        # 最近檢測記錄
        recent_query = select(DetectionResult).where(
            DetectionResult.frame_timestamp >= yesterday
        ).order_by(desc(DetectionResult.frame_timestamp)).limit(10)
        
        recent_result = await db.execute(recent_query)
        recent_detections = recent_result.scalars().all()
        
        return {
            "total_detections": total_detections,
            "category_counts": category_counts,
            "recent_detections": [
                {
                    "id": detection.id,
                    "class_name": detection.class_name,
                    "confidence": detection.confidence,
                    "timestamp": detection.frame_timestamp.isoformat() if detection.frame_timestamp else None,
                    "bbox": [detection.x_min, detection.y_min, detection.x_max, detection.y_max]
                }
                for detection in recent_detections
            ]
        }
        
    except Exception as e:
        api_logger.error(f"獲取檢測摘要失敗: {e}")
        raise HTTPException(status_code=500, detail=f"檢測摘要獲取失敗: {str(e)}")

# ===== 任務管理 API =====

@router.post("/tasks", response_model=Dict[str, Any])
async def create_task(
    task_data: TaskCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    task_service: TaskService = Depends(get_task_service)
):
    """創建新任務"""
    try:
        print(f"🔧 API 端點接收到的數據: {task_data}")
        print(f"🔧 task_service 實例: {task_service}")
        print(f"🔧 task_service 類型: {type(task_service)}")
        print(f"🔧 task_service create_task 方法: {task_service.create_task}")
        
        # 檢查方法源碼
        import inspect
        try:
            source_lines = inspect.getsource(task_service.create_task).split('\n')[:5]
            print("🔧 create_task 方法源碼前5行:")
            for i, line in enumerate(source_lines):
                print(f"  {i+1}: {line}")
        except Exception as e:
            print(f"🔧 無法獲取源碼: {e}")
        
        # 創建任務配置
        config = {
            'camera_id': task_data.camera_id,
            'model_name': task_data.model_name,
            'confidence': task_data.confidence,
            'iou_threshold': task_data.iou_threshold,
            'description': task_data.description,
            'schedule_time': task_data.schedule_time
        }
        print(f"🔧 任務配置: {config}")
        
        # 映射前端任務類型到資料庫任務類型
        task_type_mapping = {
            "realtime": "realtime_camera",
            "batch": "video_file",
            "scheduled": "video_file",
            "event": "realtime_camera"
        }
        
        db_task_type = task_type_mapping.get(task_data.task_type, "video_file")
        print(f"🔧 映射後的任務類型: {task_data.task_type} -> {db_task_type}")
        
        print("🔧 即將調用 task_service.create_task")
        # 創建任務
        task_id = await task_service.create_task(
            task_name=task_data.name,
            task_type=db_task_type,  # 使用映射後的類型
            config=config,
            db=db
        )
        print(f"🔧 task_service.create_task 返回: {task_id}")
        
        # 如果是即時任務，立即開始執行
        if task_data.task_type == "realtime":
            background_tasks.add_task(task_service.start_realtime_task, task_id, db)
        
        api_logger.info(f"任務創建成功: {task_id}")
        
        return {
            "task_id": task_id,
            "message": "任務創建成功",
            "status": "created"
        }
        
    except Exception as e:
        print(f"🔧 API 端點捕獲到異常: {e}")
        import traceback
        traceback.print_exc()
        api_logger.error(f"任務創建失敗: {e}")
        raise HTTPException(status_code=500, detail=f"任務創建失敗: {str(e)}")

@router.get("/tasks", response_model=List[TaskInfo])
async def get_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    task_service: TaskService = Depends(get_task_service)
):
    """獲取任務列表"""
    try:
        tasks = await task_service.get_all_tasks(db=db)
        
        return [
            TaskInfo(
                id=task["id"],
                name=task["name"],
                task_type=task["type"],
                status=task["status"],
                progress=task["progress"],
                camera_id=task.get("camera_id"),
                model_name=task.get("model_name", "yolo11n"),
                start_time=task.get("start_time") or task.get("created_at"),
                end_time=task.get("end_time"),
                created_at=task.get("created_at")
            )
            for task in tasks
        ]
        
    except Exception as e:
        api_logger.error(f"獲取任務列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"任務列表獲取失敗: {str(e)}")

@router.put("/tasks/{task_id}/stop")
async def stop_task(task_id: str, db: AsyncSession = Depends(get_db), task_service: TaskService = Depends(get_task_service)):
    """停止任務"""
    try:
        await task_service.stop_task(task_id, db)
        
        return {"message": "任務已停止", "task_id": task_id}
        
    except Exception as e:
        api_logger.error(f"停止任務失敗: {e}")
        raise HTTPException(status_code=500, detail=f"停止任務失敗: {str(e)}")

@router.put("/tasks/{task_id}/toggle")
async def toggle_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """切換任務狀態（暫停/恢復）"""
    try:
        from sqlalchemy import select, update
        from app.models.database import AnalysisTask
        
        # 獲取當前任務狀態
        result = await db.execute(select(AnalysisTask).where(AnalysisTask.id == int(task_id)))
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(status_code=404, detail="任務不存在")
        
        # 保存原始狀態
        old_status = task.status
        
        # 只允許切換運行中和暫停狀態
        if old_status == 'running':
            new_status = 'paused'
            message = "任務已暫停"
        elif old_status == 'paused':
            new_status = 'running'
            message = "任務已恢復"
        else:
            raise HTTPException(status_code=400, detail=f"無法切換狀態：任務當前狀態為 {old_status}")
        
        # 更新任務狀態
        await db.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == int(task_id))
            .values(status=new_status)
        )
        await db.commit()
        
        return {
            "message": message,
            "task_id": int(task_id),
            "old_status": old_status,
            "new_status": new_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"切換任務狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"切換任務狀態失敗: {str(e)}")

@router.get("/tasks/stats")
async def get_task_stats(db: AsyncSession = Depends(get_db), task_service: TaskService = Depends(get_task_service)):
    """獲取任務統計"""
    try:
        stats = await task_service.get_task_statistics(db)
        
        return stats
        
    except Exception as e:
        api_logger.error(f"獲取任務統計失敗: {e}")
        raise HTTPException(status_code=500, detail=f"任務統計獲取失敗: {str(e)}")

@router.post("/analysis/start-realtime", response_model=RealtimeAnalysisResponse)
async def start_realtime_analysis(
    request: RealtimeAnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    """開始即時分析任務"""
    try:
        api_logger.info(f"收到即時分析請求: {request}")
        
        # 1. 驗證攝影機
        camera_service = CameraService()
        cameras = await camera_service.get_cameras()
        camera_info = None
        for cam in cameras:
            if str(cam.id) == str(request.camera_id):
                # 轉換 Camera dataclass 為字典
                camera_info = {
                    "id": cam.id,
                    "name": cam.name,
                    "status": cam.status,
                    "camera_type": cam.camera_type,
                    "resolution": cam.resolution,
                    "fps": cam.fps,
                    "group_id": cam.group_id,
                    "device_index": cam.device_index,
                    "rtsp_url": cam.rtsp_url
                }
                break
        
        if not camera_info:
            raise HTTPException(status_code=404, detail=f"攝影機 {request.camera_id} 未找到")
        
        # 臨時允許所有狀態的攝影機進行測試
        # if camera_info["status"] not in ["online", "active", "inactive"]:
        #     raise HTTPException(status_code=400, detail=f"攝影機 {request.camera_id} 不可用，狀態: {camera_info['status']}")
        
        # # 如果攝影機為 offline 狀態，則拒絕請求
        # if camera_info["status"] == "offline":
        #     raise HTTPException(status_code=400, detail=f"攝影機 {request.camera_id} 離線，無法啟動即時分析")
        
        api_logger.info(f"使用攝影機: {camera_info['name']} (狀態: {camera_info['status']})")

        # 1.1 驗證資料來源（與攝影機對應）
        try:
            source_id = int(camera_info["id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="攝影機來源 ID 無效")

        data_source = await db.get(DataSource, source_id)
        if data_source is None:
            raise HTTPException(status_code=404, detail=f"資料來源 {source_id} 未找到，請重新建立攝影機")
        
        # 2. 驗證模型：統一使用專案 uploads/models
        model_files = []
        import os
        models_dir = find_models_directory()
        api_logger.info(f"查找模型目錄: {models_dir}")
        if not models_dir or not models_dir.exists():
            raise HTTPException(status_code=500, detail=f"模型目錄不存在: {models_dir}")
            
        for file in models_dir.iterdir():
            if file.is_file() and file.suffix in ['.pt', '.onnx']:
                model_files.append({
                    "id": file.stem,  # 檔案名稱不含副檔名
                    "filename": file.name,
                    "path": str(file)
                })
                
        api_logger.info(f"找到 {len(model_files)} 個模型檔案: {[m['filename'] for m in model_files]}")
        
        model_info = None
        for model in model_files:
            if model["id"] == request.model_id:
                model_info = model
                break
        
        if not model_info:
            raise HTTPException(status_code=404, detail=f"YOLO模型 {request.model_id} 未找到")
        
        if not os.path.exists(model_info["path"]):
            raise HTTPException(status_code=400, detail=f"模型檔案不存在: {model_info['path']}")
        
        # 3. 獲取攝影機解析度資訊 (使用共享攝影機流管理器)
        camera_width = 640
        camera_height = 480
        camera_fps = 30.0
        
        try:
            from app.services.camera_stream_manager import camera_stream_manager
            if camera_info["camera_type"] == "USB":
                device_index = camera_info.get("device_index", 0)
                # 使用共享攝影機流管理器獲取解析度
                resolution_info = camera_stream_manager.get_camera_resolution(device_index)
                if resolution_info:
                    camera_width = resolution_info.get("width", 640)
                    camera_height = resolution_info.get("height", 480)
                    camera_fps = resolution_info.get("fps", 30.0)
                    api_logger.info(f"攝影機解析度: {camera_width}x{camera_height}@{camera_fps}fps")
        except Exception as e:
            api_logger.warning(f"獲取攝影機解析度失敗，使用預設值: {e}")
        
        # 4. 創建 analysis_tasks 記錄
        task_id = None
        try:
            source_info = {
                "source_id": source_id,
                "camera_id": request.camera_id,
                "camera_name": camera_info.get("name", f"Camera-{request.camera_id}"),
                "camera_type": camera_info.get("camera_type", "USB"),
                "device_index": camera_info.get("device_index", 0),
                "model_id": request.model_id,
                "model_path": model_info["path"],
                "confidence": request.confidence,
                "iou_threshold": request.iou_threshold,
                "client_stream": request.client_stream
            }

            analysis_task = AnalysisTask(
                task_type="realtime_camera",
                status="pending",
                source_id=source_id,
                source_info=source_info,
                source_width=camera_width,
                source_height=camera_height,
                source_fps=camera_fps,
                created_at=datetime.now(),
                task_name=request.task_name,
                model_id=request.model_id,
                confidence_threshold=request.confidence
            )
            
            db.add(analysis_task)
            await db.commit()
            await db.refresh(analysis_task)
            task_id = analysis_task.id

            api_logger.info(f"創建分析任務記錄成功: {task_id}")

        except Exception as e:
            await db.rollback()
            api_logger.error(f"創建任務記錄失敗: {e}")
            raise HTTPException(status_code=500, detail=f"創建任務記錄失敗: {str(e)}")

        # 5. 準備警報設定並啟動 PySide6 偵測程式（預設隱藏）
        if request.alert_rules is not None:
            alert_rules = request.alert_rules
            alert_rules_path = save_alert_runtime_rules(task_id, alert_rules)
        else:
            alert_rules = load_alert_runtime_rules(task_id)
            alert_rules_path = ensure_alert_runtime_file(task_id)
        fall_alert_enabled = _has_fall_detection_rule(alert_rules)
        if camera_info.get("camera_type") == "USB":
            source_value = str(camera_info.get("device_index", 0))
        else:
            source_value = camera_info.get("rtsp_url") or camera_info.get("id")

        if not source_value:
            raise HTTPException(status_code=400, detail="無法解析攝影機來源")

        try:
            realtime_gui_manager.start_detection(
                task_id=str(task_id),
                source=source_value,
                model_path=model_info["path"],
                window_name=request.task_name or camera_info.get("name"),
                confidence=request.confidence,
                imgsz=camera_width,
                device=None,
                start_hidden=True,
                fall_alert_enabled=fall_alert_enabled,
                alert_rules_path=str(alert_rules_path),
            )
        except Exception as exc:
            api_logger.error(f"啟動 PySide6 偵測程式失敗: {exc}")
            raise HTTPException(
                status_code=500, detail=f"偵測程式啟動失敗：{exc}"
            ) from exc

        if fall_alert_enabled:
            _update_fall_detection_monitor(
                analysis_task,
                True,
                confidence=request.confidence,
            )
        else:
            fall_detection_service.stop_monitoring(str(task_id))

        analysis_task.status = "running"
        analysis_task.start_time = datetime.now()
        await db.commit()

        api_logger.info(f"即時分析任務 {task_id} 已啟動")

        return RealtimeAnalysisResponse(
            task_id=str(task_id),
            status="started",
            message="即時分析任務已成功啟動",
            camera_info={
                "id": camera_info["id"],
                "name": camera_info.get("name", ""),
                "resolution": f"{camera_width}x{camera_height}",
                "fps": camera_fps,
            },
            model_info={
                "id": model_info["id"],
                "filename": model_info["filename"],
                "confidence": request.confidence,
                "iou_threshold": request.iou_threshold,
            },
            created_at=datetime.now(),
            websocket_url=None,
            client_stream=False,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"啟動即時分析失敗: {e}")
        raise HTTPException(status_code=500, detail=f"啟動即時分析失敗: {str(e)}")


@router.get(
    "/analysis/tasks/{task_id}/regions",
    summary="取得分析任務的線段與區域配置",
)
async def get_analysis_task_regions(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    提供前端查詢某個任務目前已配置的線段與區域標籤，
    來源為 task_statistics 表的 line_stats / zone_stats 欄位。
    """
    try:
        stats = await db.get(TaskStatistics, task_id)
        if not stats:
            task = await db.get(AnalysisTask, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="分析任務不存在")
            return {
                "task_id": task_id,
                "lines": [],
                "zones": [],
                "updated_at": None,
            }

        def _convert_items(payload):
            items = []
            if isinstance(payload, dict):
                for label, metrics in payload.items():
                    if label is None:
                        continue
                    items.append(
                        {
                            "id": str(label),
                            "label": str(label),
                            "metrics": metrics or {},
                        }
                    )
            return items

        return {
            "task_id": task_id,
            "lines": _convert_items(stats.line_stats),
            "zones": _convert_items(stats.zone_stats),
            "updated_at": stats.updated_at.isoformat() if stats.updated_at else None,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        api_logger.error(f"取得任務線段/區域配置失敗: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"查詢任務線段/區域配置失敗: {exc}",
        )


@router.get("/analysis/tasks/{task_id}/alerts", summary="取得任務警報設定")
async def get_task_alert_config(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    rules = load_alert_runtime_rules(task_id)
    return {"task_id": task_id, "rules": rules}


@router.put("/analysis/tasks/{task_id}/alerts", summary="更新任務警報設定")
async def update_task_alert_config(
    task_id: int,
    payload: TaskAlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    rules = payload.rules or []
    path = save_alert_runtime_rules(task_id, rules)
    fall_enabled = _has_fall_detection_rule(rules)
    if task.status == "running":
        _update_fall_detection_monitor(task, fall_enabled)
    else:
        fall_detection_service.stop_monitoring(str(task.id))
    return {
        "task_id": task_id,
        "rules": rules,
        "config_path": str(path),
        "fall_detection_enabled": fall_enabled,
    }

# ===== 攝影機管理 API =====
@router.websocket("/analysis/live-person-camera/{task_id}/ws")
async def live_person_camera_websocket(websocket: WebSocket, task_id: str):
    """Live Person Camera 即時預覽 WebSocket"""
    await realtime_detection_service.register_preview_client(task_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await realtime_detection_service.unregister_preview_client(task_id, websocket)
    except Exception:
        await realtime_detection_service.unregister_preview_client(task_id, websocket)


@router.websocket("/analysis/live-person-camera/{task_id}/upload")
async def live_person_camera_upload(websocket: WebSocket, task_id: str):
    """接收前端上傳的即時分析影像"""
    await websocket.accept()

    try:
        while True:
            try:
                data = await websocket.receive()
            except RuntimeError as exc:
                api_logger.info(f"Live Person Camera 上傳連線終止: {task_id} - {exc}")
                break

            message_type = data.get("type")

            if message_type == "websocket.disconnect":
                api_logger.info(f"Live Person Camera 上傳連線關閉: {task_id}")
                break

            if message_type == "websocket.ping":
                await websocket.send_bytes(b"")
                continue

            if message_type not in {"websocket.receive", None}:
                continue

            message: Optional[str] = data.get("text")
            if message is None:
                raw_bytes = data.get("bytes")
                if raw_bytes is None:
                    continue
                try:
                    message = raw_bytes.decode("utf-8")
                except Exception:
                    continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "invalid_json"}))
                continue

            if payload.get("type") != "frame":
                continue

            image_data = payload.get("image")
            if not isinstance(image_data, str):
                await websocket.send_text(json.dumps({"type": "error", "message": "missing_image"}))
                continue

            if image_data.startswith("data:"):
                image_data = image_data.split(",", 1)[-1]

            try:
                frame_bytes = base64.b64decode(image_data)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "decode_failed"}))
                continue

            if not frame_bytes:
                await websocket.send_text(json.dumps({"type": "error", "message": "empty_frame"}))
                continue

            success = await realtime_detection_service.ingest_external_frame(task_id, frame_bytes)
            if not success:
                await websocket.send_text(json.dumps({"type": "error", "message": "frame_rejected"}))
    except WebSocketDisconnect:
        api_logger.info(f"Live Person Camera 上傳連線結束: {task_id}")
    except Exception as e:
        api_logger.error(f"Live Person Camera 上傳處理錯誤: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@router.get("/cameras")
async def get_cameras(
    real_time_check: bool = Query(False, description="是否進行即時狀態檢測"),
    db: AsyncSession = Depends(get_async_db)
) -> List[CameraInfo]:
    """獲取攝影機列表（支援即時狀態檢測）"""
    print(f"🚨 DEBUG: get_cameras 函數被調用! real_time_check={real_time_check}")
    try:
        print(f"🚨 DEBUG: 進入 try 區塊")
        api_logger.info(f"🔍 get_cameras 被調用，real_time_check={real_time_check}")
        camera_service = CameraService()
        print(f"🚨 DEBUG: CameraService 建立完成")
        api_logger.info("✅ CameraService 建立成功")
        cameras = await camera_service.get_cameras()
        print(f"🚨 DEBUG: 獲取到攝影機列表，數量: {len(cameras)}")
        api_logger.info(f"✅ 獲取到 {len(cameras)} 個攝影機")
        
        # 如果需要進行即時檢測
        if real_time_check:
            api_logger.info("🔄 開始進行即時狀態檢測")
            print(f"🚨 DEBUG: 開始即時檢測，攝影機數量: {len(cameras)}")
            # 對每台攝影機進行即時狀態檢測
            for i, camera in enumerate(cameras):
                try:
                    print(f"🚨 DEBUG: 檢測攝影機 {i+1}: {camera.name}")
                    # 執行真正的即時檢測
                    actual_status = await camera_service.check_camera_status_realtime(camera)
                    print(f"🚨 DEBUG: 攝影機 {i+1} 即時狀態: {actual_status}")
                    
                    # 更新攝影機狀態
                    if hasattr(camera, '__dict__'):
                        camera.__dict__['status'] = actual_status
                    else:
                        # 如果是 dataclass，需要創建新的實例
                        from dataclasses import replace
                        camera = replace(camera, status=actual_status)
                        cameras[i] = camera  # 更新列表中的攝影機
                    
                    api_logger.info(f"✅ 攝影機 {camera.name} 即時狀態更新為: {actual_status}")
                    
                except Exception as e:
                    print(f"🚨 DEBUG: 攝影機 {i+1} 即時檢測失敗: {e}")
                    api_logger.error(f"❌ 攝影機 {camera.name} 即時檢測失敗: {e}")
                    # 即時檢測失敗時，標記為錯誤狀態
                    if hasattr(camera, '__dict__'):
                        camera.__dict__['status'] = 'error'
                    else:
                        from dataclasses import replace
                        camera = replace(camera, status='error')
                        cameras[i] = camera
        
        # 將 Camera dataclass 轉換為 CameraInfo Pydantic 模型
        camera_infos = []
        for i, camera in enumerate(cameras):
            try:
                api_logger.info(f"🔄 轉換攝影機 {i}: type={type(camera)}")
                api_logger.info(f"  camera object repr: {repr(camera)}")
                
                # 逐個嘗試訪問屬性
                try:
                    camera_id = camera.id
                    api_logger.info(f"  ✅ ID: {camera_id}")
                except Exception as e:
                    api_logger.error(f"  ❌ ID 訪問失敗: {e}")
                    raise
                
                try:
                    camera_name = camera.name
                    api_logger.info(f"  ✅ Name: {camera_name}")
                except Exception as e:
                    api_logger.error(f"  ❌ Name 訪問失敗: {e}")
                    raise
                
                try:
                    camera_status = camera.status
                    api_logger.info(f"  ✅ Status: {camera_status}")
                except Exception as e:
                    api_logger.error(f"  ❌ Status 訪問失敗: {e}")
                    raise
                
                try:
                    camera_type = camera.camera_type
                    api_logger.info(f"  ✅ Camera Type: {camera_type}")
                except Exception as e:
                    api_logger.error(f"  ❌ Camera Type 訪問失敗: {e}")
                    raise
                
                try:
                    camera_resolution = camera.resolution
                    api_logger.info(f"  ✅ Resolution: {camera_resolution}")
                except Exception as e:
                    api_logger.error(f"  ❌ Resolution 訪問失敗: {e}")
                    raise
                
                try:
                    camera_fps = camera.fps
                    api_logger.info(f"  ✅ FPS: {camera_fps}")
                except Exception as e:
                    api_logger.error(f"  ❌ FPS 訪問失敗: {e}")
                    raise
                
                try:
                    camera_group_id = camera.group_id
                    api_logger.info(f"  ✅ Group ID: {camera_group_id}")
                except Exception as e:
                    api_logger.error(f"  ❌ Group ID 訪問失敗: {e}")
                    raise
                
                camera_info = CameraInfo(
                    id=camera_id,
                    name=camera_name,
                    status=camera_status,
                    camera_type=camera_type,
                    resolution=camera_resolution,
                    fps=camera_fps,
                    group_id=camera_group_id
                )
                api_logger.info(f"  ✅ CameraInfo 建立成功")
                camera_infos.append(camera_info)
                
            except Exception as camera_error:
                api_logger.error(f"❌ 攝影機 {i} 轉換失敗: {camera_error}")
                import traceback
                api_logger.error(f"完整錯誤堆疊:\n{traceback.format_exc()}")
                raise camera_error
        
        return camera_infos
        
    except Exception as e:
        api_logger.error(f"獲取攝影機列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機列表獲取失敗: {str(e)}")

@router.post("/cameras")
async def add_camera(camera_data: dict):
    """添加新攝影機"""
    try:
        camera_service = CameraService()
        
        # 提取攝影機數據
        name = camera_data.get("name")
        camera_type = camera_data.get("camera_type")
        resolution = camera_data.get("resolution", "1920x1080")
        fps = camera_data.get("fps", 30)
        device_index = camera_data.get("device_index")
        rtsp_url = camera_data.get("rtsp_url")
        
        if not name or not camera_type:
            raise HTTPException(status_code=400, detail="攝影機名稱和類型為必填項")
        
        camera_id = await camera_service.add_camera(
            name=name,
            camera_type=camera_type,
            resolution=resolution,
            fps=fps,
            device_index=device_index,
            rtsp_url=rtsp_url
        )
        
        return {
            "success": True,
            "camera_id": camera_id,
            "message": "攝影機添加成功"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"添加攝影機失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機添加失敗: {str(e)}")

@router.delete("/cameras/{camera_id}")
async def remove_camera(camera_id: str):
    """刪除攝影機"""
    try:
        camera_service = CameraService()
        await camera_service.remove_camera(camera_id)
        
        return {
            "success": True,
            "message": "攝影機刪除成功"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        api_logger.error(f"刪除攝影機失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機刪除失敗: {str(e)}")

@router.put("/cameras/{camera_id}/toggle")
async def toggle_camera(camera_id: str):
    """切換攝影機狀態"""
    try:
        camera_service = CameraService()
        new_status = await camera_service.toggle_camera(camera_id)
        
        return {
            "camera_id": camera_id,
            "status": new_status,
            "message": f"攝影機已{'啟動' if new_status == 'online' else '停止'}"
        }
        
    except Exception as e:
        api_logger.error(f"切換攝影機狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機狀態切換失敗: {str(e)}")

@router.get("/cameras/{camera_id}/status")
async def check_camera_status(camera_id: int):
    """檢查單個攝影機的即時狀態"""
    try:
        db_service = DatabaseService()
        camera_monitor = get_camera_monitor(db_service)
        
        # 進行即時狀態檢測
        status = await camera_monitor.get_camera_status_immediately(camera_id)
        
        if status is None:
            raise HTTPException(status_code=404, detail=f"攝影機 {camera_id} 不存在")
        
        return {
            "camera_id": camera_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "message": f"攝影機狀態: {status}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"檢查攝影機 {camera_id} 狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機狀態檢測失敗: {str(e)}")

@router.post("/cameras/status/check-all")
async def check_all_cameras_status():
    """檢查所有攝影機的即時狀態"""
    try:
        db_service = DatabaseService()
        camera_monitor = get_camera_monitor(db_service)
        
        # 檢查所有攝影機狀態
        results = await camera_monitor.check_all_cameras()
        
        return {
            "message": f"已檢查 {len(results)} 個攝影機的狀態",
            "timestamp": datetime.now().isoformat(),
            "results": results
        }
        
    except Exception as e:
        api_logger.error(f"檢查所有攝影機狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機狀態檢測失敗: {str(e)}")

@router.post("/cameras/scan")
async def scan_cameras(
    register_new: bool = Query(
        True,
        description="是否將掃描到的新攝影機自動加入設備列表"
    )
):
    """掃描可用攝影機，必要時自動註冊為設備列表"""
    try:
        camera_service = CameraService()

        # 先取得現有設備，用於比對是否已註冊
        existing_cameras = await camera_service.get_cameras()
        existing_indices = {
            cam.device_index for cam in existing_cameras if cam.device_index is not None
        }

        # 掃描本機可用攝影機
        discovered = await camera_service.scan_cameras()
        registered_devices: List[Dict[str, Any]] = []

        if register_new:
            for device in discovered:
                device_index = device.get("device_index")
                if device_index is None:
                    continue

                if device_index in existing_indices:
                    continue

                camera_name = device.get("name") or f"USB Camera {device_index}"
                resolution = device.get("resolution") or "640x480"
                fps_value = device.get("fps") or 30
                try:
                    fps = int(fps_value)
                except (TypeError, ValueError):
                    fps = 30

                new_id = await camera_service.add_camera(
                    name=camera_name,
                    camera_type="USB",
                    resolution=resolution,
                    fps=fps,
                    device_index=device_index,
                )

                existing_indices.add(device_index)
                registered_devices.append(
                    {
                        "id": new_id,
                        "name": camera_name,
                        "device_index": device_index,
                        "resolution": resolution,
                        "fps": fps,
                    }
                )

        return {
            "message": f"掃描完成，發現 {len(discovered)} 個攝影機",
            "cameras": discovered,
            "registered": registered_devices,
        }

    except Exception as e:
        api_logger.error(f"掃描攝影機失敗: {e}")
        raise HTTPException(status_code=500, detail=f"攝影機掃描失敗: {str(e)}")

@router.get("/cameras/{camera_index}/preview")
async def get_camera_preview(camera_index: int):
    """獲取攝影機即時預覽影像（JPEG格式）- 使用共享視訊流"""
    try:
        from fastapi.responses import Response
        from app.services.camera_stream_manager import camera_stream_manager
        import cv2
        
        camera_id = f"camera_{camera_index}"
        
        # 確保攝影機流正在運行
        if not camera_stream_manager.is_stream_running(camera_id):
            # 嘗試啟動流
            success = camera_stream_manager.start_stream(camera_id, camera_index)
            if not success:
                raise HTTPException(status_code=404, detail=f"攝影機 {camera_index} 無法開啟")
        
        # 獲取最新幀
        latest_frame = camera_stream_manager.get_latest_frame(camera_id)
        if latest_frame is None:
            raise HTTPException(status_code=500, detail=f"攝影機 {camera_index} 無法讀取影格")
        
        # 轉換為JPEG
        _, jpeg_buffer = cv2.imencode('.jpg', latest_frame.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        
        return Response(
            content=jpeg_buffer.tobytes(),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"獲取攝影機預覽失敗: {e}")
        raise HTTPException(status_code=500, detail=f"預覽獲取失敗: {str(e)}")

@router.get("/cameras/{camera_index}/stream")
async def camera_stream(camera_index: int):
    """攝影機即時串流（MJPEG格式）- 使用共享視訊流"""
    try:
        from fastapi.responses import StreamingResponse
        from app.services.camera_stream_manager import camera_stream_manager, StreamConsumer, FrameData
        import cv2
        import asyncio
        import threading
        import queue
        
        camera_id = f"camera_{camera_index}"
        
        # 檢查攝影機流狀態並記錄調試信息
        is_running = camera_stream_manager.is_stream_running(camera_id)
        api_logger.info(f"攝影機串流請求: {camera_id}, 當前是否運行: {is_running}")
        
        # 如果攝影機流未運行，嘗試啟動
        if not is_running:
            api_logger.info(f"嘗試啟動攝影機流: {camera_id} (設備索引: {camera_index})")
            success = camera_stream_manager.start_stream(camera_id, camera_index)
            if not success:
                api_logger.error(f"攝影機流啟動失敗: {camera_id}")
                raise HTTPException(status_code=404, detail=f"攝影機 {camera_index} 無法開啟，可能正被其他應用使用")
        else:
            api_logger.info(f"使用現有攝影機流: {camera_id}")
        
        # 使用同步佇列來避免事件循環問題
        import queue
        frame_queue = queue.Queue(maxsize=5)
        
        def frame_callback(frame_data: FrameData):
            """接收新幀的回調函數"""
            try:
                # 使用同步佇列，非阻塞方式放入
                if not frame_queue.full():
                    frame_queue.put_nowait(frame_data)
            except queue.Full:
                # 如果佇列滿了，移除最舊的幀再放入新的
                try:
                    frame_queue.get_nowait()
                    frame_queue.put_nowait(frame_data)
                except queue.Empty:
                    pass
            except Exception as e:
                api_logger.error(f"幀回調錯誤: {e}")
        
        # 建立消費者
        consumer_id = f"stream_{camera_index}_{id(frame_callback)}"
        consumer = StreamConsumer(consumer_id, frame_callback)
        
        # 註冊消費者
        camera_stream_manager.add_consumer(camera_id, consumer)
        
        async def generate_frames():
            try:
                while True:
                    try:
                        # 從同步佇列獲取幀，使用短超時避免阻塞
                        try:
                            frame_data = frame_queue.get(timeout=0.1)
                        except queue.Empty:
                            # 如果沒有新幀，等待一小段時間再繼續
                            await asyncio.sleep(0.03)  # 約30FPS
                            continue
                        
                        # 編碼為JPEG
                        _, jpeg_buffer = cv2.imencode('.jpg', frame_data.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        
                        # MJPEG串流格式
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' +
                               jpeg_buffer.tobytes() + b'\r\n')
                        
                    except Exception as e:
                        api_logger.error(f"幀生成錯誤: {e}")
                        break
                        
            finally:
                # 清理：移除消費者
                camera_stream_manager.remove_consumer(camera_id, consumer_id)
                api_logger.info(f"攝影機串流 {camera_index} 已清理")
        
        return StreamingResponse(
            generate_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "keep-alive"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"攝影機串流失敗: {e}")
        raise HTTPException(status_code=500, detail=f"串流失敗: {str(e)}")

        raise HTTPException(status_code=500, detail=f"攝影機掃描失敗: {str(e)}")

# ===== 分析統計 API =====

@router.get(
    "/analytics/camera-performance",
    response_model=List[CameraPerformanceItem],
)
async def get_camera_performance(
    days: int = Query(7, ge=1, le=90, description="統計回溯天數"),
    limit: int = Query(5, ge=1, le=50, description="回傳的攝影機數量"),
    db: AsyncSession = Depends(get_db),
):
    """依據歷史偵測結果與任務資料輸出攝影機效能指標"""
    try:
        now = datetime.now()
        start_time = now - timedelta(days=days)

        detection_stmt = (
            select(
                AnalysisTask.camera_name.label("camera_name"),
                AnalysisTask.camera_id.label("camera_id"),
                func.count(DetectionResult.id).label("detections"),
                func.max(DetectionResult.frame_timestamp).label("last_detection"),
            )
            .join(DetectionResult, DetectionResult.task_id == AnalysisTask.id)
            .where(
                AnalysisTask.camera_name.isnot(None),
                DetectionResult.frame_timestamp >= start_time,
            )
            .group_by(AnalysisTask.camera_name, AnalysisTask.camera_id)
        )

        detection_rows = (await db.execute(detection_stmt)).all()
        metrics: Dict[str, Dict[str, Any]] = {}

        for row in detection_rows:
            key = row.camera_id or row.camera_name
            if not key:
                continue
            metrics[key] = {
                "camera_name": row.camera_name or "未命名攝影機",
                "camera_id": row.camera_id,
                "detections": int(row.detections or 0),
                "runtime_seconds": 0.0,
                "status": None,
                "last_active": _normalize_datetime_input(row.last_detection),
            }

        task_stmt = (
            select(
                AnalysisTask.camera_name,
                AnalysisTask.camera_id,
                AnalysisTask.start_time,
                AnalysisTask.end_time,
                AnalysisTask.created_at,
                AnalysisTask.status,
            )
            .where(
                AnalysisTask.camera_name.isnot(None),
                or_(
                    AnalysisTask.end_time.is_(None),
                    AnalysisTask.end_time >= start_time,
                    AnalysisTask.start_time >= start_time,
                    AnalysisTask.created_at >= start_time,
                ),
            )
        )

        task_rows = (await db.execute(task_stmt)).all()

        for row in task_rows:
            key = row.camera_id or row.camera_name
            if not key:
                continue

            metric = metrics.get(key)
            if metric is None:
                metric = {
                    "camera_name": row.camera_name or "未命名攝影機",
                    "camera_id": row.camera_id,
                    "detections": 0,
                    "runtime_seconds": 0.0,
                    "status": None,
                    "last_active": None,
                }
                metrics[key] = metric

            start_candidate = (
                _normalize_datetime_input(row.start_time)
                or _normalize_datetime_input(row.created_at)
            )
            if not start_candidate:
                continue

            effective_start = max(start_candidate, start_time)
            end_candidate = _normalize_datetime_input(row.end_time) or now
            if end_candidate <= start_time or end_candidate <= effective_start:
                continue

            metric["runtime_seconds"] += max(
                0.0, (end_candidate - effective_start).total_seconds()
            )
            metric["status"] = metric["status"] or row.status

            latest_point = _normalize_datetime_input(row.end_time) or now
            if metric["last_active"] is None or (
                latest_point and latest_point > metric["last_active"]
            ):
                metric["last_active"] = latest_point

        if not metrics:
            return []

        performance_items: List[CameraPerformanceItem] = []
        for payload in metrics.values():
            runtime_hours = round(payload["runtime_seconds"] / 3600, 1)
            performance_items.append(
                CameraPerformanceItem(
                    camera_name=payload["camera_name"],
                    camera_id=payload["camera_id"],
                    detections=payload["detections"],
                    runtime_hours=runtime_hours,
                    status=payload["status"],
                    last_active=payload["last_active"],
                )
            )

        performance_items.sort(
            key=lambda item: (item.detections, item.runtime_hours),
            reverse=True,
        )

        return performance_items[:limit]

    except Exception as exc:
        api_logger.error(f"獲取攝影機效能數據失敗: {exc}")
        raise HTTPException(status_code=500, detail="無法取得攝影機效能數據")


@router.get(
    "/analytics/alert-trends",
    response_model=List[AlertTrendPoint],
)
async def get_alert_trends(
    days: int = Query(7, ge=1, le=90, description="統計最近幾天的警報趨勢"),
    task_id: Optional[str] = Query(None, description="僅統計指定任務的警報"),
):
    """由快照檔案彙整警報趨勢資料。"""
    now = datetime.now()
    effective_days = max(1, min(days, 90))
    start_date = (now - timedelta(days=effective_days - 1)).date()
    start_boundary = datetime.combine(start_date, datetime.min.time())
    end_boundary = datetime.combine(now.date(), datetime.max.time())

    buckets: Dict[str, Dict[str, Any]] = {}
    for offset in range(effective_days):
        day = start_date + timedelta(days=offset)
        day_key = day.isoformat()
        buckets[day_key] = {"date": day_key, "high": 0, "medium": 0, "low": 0}

    task_dirs = _resolve_alert_task_dirs(task_id)
    if not task_dirs:
        return []

    events = _collect_alert_snapshot_events(task_dirs, start_boundary, end_boundary)
    if not events:
        return []

    for event in events:
        day_key = event["timestamp"].date().isoformat()
        bucket = buckets.get(day_key)
        if bucket is None:
            continue
        rule_payload = event.get("rule_payload") or {}
        severity_label = _normalize_severity_label(rule_payload.get("severity"))
        bucket_key = _severity_bucket_key(severity_label)
        bucket[bucket_key] += 1

    return list(buckets.values())


@router.get(
    "/analytics/alert-categories",
    response_model=List[AlertCategoryStat],
)
async def get_alert_category_stats(
    days: int = Query(7, ge=1, le=90, description="統計最近幾天的警報類別"),
    limit: int = Query(4, ge=1, le=20, description="最多顯示的類別數"),
    task_id: Optional[str] = Query(None, description="僅統計指定任務的警報"),
):
    now = datetime.now()
    effective_days = max(1, min(days, 90))
    start_date = (now - timedelta(days=effective_days - 1)).date()
    start_boundary = datetime.combine(start_date, datetime.min.time())
    end_boundary = datetime.combine(now.date(), datetime.max.time())

    task_dirs = _resolve_alert_task_dirs(task_id)
    if not task_dirs:
        return []

    events = _collect_alert_snapshot_events(task_dirs, start_boundary, end_boundary)
    if not events:
        return []

    aggregates: Dict[str, Dict[str, Any]] = {}
    for event in events:
        rule_payload = event.get("rule_payload") or {}
        raw_rule_type = rule_payload.get("rule_type") if rule_payload else None
        key = _normalize_rule_type_key(raw_rule_type)
        label = _normalize_rule_type_label(raw_rule_type)
        entry = aggregates.get(key)
        if entry is None:
            entry = {"rule_type": key, "label": label, "count": 0}
            aggregates[key] = entry
        entry["count"] += 1

    if not aggregates:
        return []

    stats = [AlertCategoryStat(**payload) for payload in aggregates.values()]
    stats.sort(key=lambda item: item.count, reverse=True)
    return stats[:limit]


@router.get(
    "/analytics/hourly-activity",
    response_model=HourlyActivityResponse,
)
async def get_hourly_activity(
    hours: int = Query(24, ge=1, le=168, description="統計最近幾小時的偵測活動"),
    db: AsyncSession = Depends(get_db),
):
    """取得指定期間內每小時的偵測量，供前端 24 小時活動圖表使用。"""
    try:
        now = datetime.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        window_hours = max(1, min(hours, 168))
        start_hour = current_hour - timedelta(hours=window_hours - 1)
        end_boundary = current_hour + timedelta(hours=1)

        hour_bucket = func.date_trunc("hour", DetectionResult.frame_timestamp).label("hour_bucket")
        count_expr = func.count(DetectionResult.id).label("count")

        stmt = (
            select(hour_bucket, count_expr)
            .where(
                DetectionResult.frame_timestamp >= start_hour,
                DetectionResult.frame_timestamp < end_boundary,
            )
            .group_by(hour_bucket)
            .order_by(hour_bucket)
        )

        result = await db.execute(stmt)
        aggregated: Dict[datetime, int] = {}
        for row in result.all():
            bucket_time = getattr(row, "hour_bucket", None)
            if bucket_time is None:
                continue
            if bucket_time.tzinfo is not None:
                bucket_time = bucket_time.replace(tzinfo=None)
            aggregated[bucket_time] = int(getattr(row, "count", 0) or 0)

        data_points: List[HourlyActivityPoint] = []
        total_count = 0
        for idx in range(window_hours):
            bucket_time = start_hour + timedelta(hours=idx)
            count = aggregated.get(bucket_time, 0)
            total_count += count
            label = bucket_time.strftime("%m/%d %H:00")
            data_points.append(HourlyActivityPoint(hour=label, count=count))

        has_activity = any(point.count > 0 for point in data_points)
        average = round(total_count / len(data_points), 2) if data_points else 0.0

        if has_activity:
            peak_point = max(data_points, key=lambda item: item.count)
            low_point = min(data_points, key=lambda item: item.count)
            summary = HourlyActivitySummary(
                peak_hour=peak_point.hour,
                peak_count=peak_point.count,
                low_hour=low_point.hour,
                low_count=low_point.count,
                average_per_hour=average,
            )
        else:
            summary = HourlyActivitySummary(
                peak_hour=None,
                peak_count=0,
                low_hour=None,
                low_count=0,
                average_per_hour=average,
            )

        return HourlyActivityResponse(data=data_points, summary=summary)

    except Exception as exc:
        api_logger.error(f"獲取小時活動數據失敗: {exc}")
        raise HTTPException(status_code=500, detail="無法取得小時活動數據")


@router.get("/analytics", response_model=AnalyticsData)
async def get_analytics(
    period: str = "24h",
    db: AsyncSession = Depends(get_db)
):
    """獲取分析統計數據"""
    try:
        analytics_service = AnalyticsService()
        data = await analytics_service.get_analytics_data(period=period, db=db)
        
        return AnalyticsData(
            detection_counts=data["detection_counts"],
            hourly_trend=data["hourly_trend"],
            category_distribution=data["category_distribution"],
            time_period_analysis=data["time_period_analysis"]
        )
        
    except Exception as e:
        api_logger.error(f"獲取分析數據失敗: {e}")
        raise HTTPException(status_code=500, detail=f"分析數據獲取失敗: {str(e)}")

@router.get("/analytics/heatmap")
async def get_heatmap_data(db: AsyncSession = Depends(get_db)):
    """獲取熱點圖數據"""
    try:
        analytics_service = AnalyticsService()
        heatmap_data = await analytics_service.get_heatmap_data(db=db)
        
        return heatmap_data
        
    except Exception as e:
        api_logger.error(f"獲取熱點圖數據失敗: {e}")
        raise HTTPException(status_code=500, detail=f"熱點圖數據獲取失敗: {str(e)}")

@router.get("/detection-results")
async def get_detection_results(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None, description="關鍵字，匹配任務名稱/攝影機/物件"),
    camera_name: Optional[str] = Query(None, description="攝影機名稱"),
    camera_id: Optional[str] = Query(None, description="攝影機 ID"),
    task_id: Optional[int] = Query(None, description="任務 ID"),
    object_type: Optional[str] = Query(None, description="物件種類"),
    start_time: Optional[datetime] = Query(None, description="開始時間 (ISO 8601)"),
    end_time: Optional[datetime] = Query(None, description="結束時間 (ISO 8601)"),
    min_confidence: Optional[float] = Query(None, description="最低信心度"),
    max_confidence: Optional[float] = Query(None, description="最高信心度"),
    db: AsyncSession = Depends(get_db),
):
    """
    取得偵測記錄列表：
    - 同一個追蹤 ID (tracker_id) 只會顯示一次
    - start_time / end_time 分別對應首次出現與最後一次出現的時間
    """
    try:
        offset = (page - 1) * limit
        normalized_start = _normalize_datetime_input(start_time)
        normalized_end = _normalize_datetime_input(end_time)

        tracker_group = func.coalesce(DetectionResult.tracker_id, DetectionResult.id)
        partition_key = (DetectionResult.task_id, tracker_group)

        start_time_col = func.min(DetectionResult.frame_timestamp).over(
            partition_by=partition_key
        ).label("start_time")

        end_time_col = func.max(DetectionResult.frame_timestamp).over(
            partition_by=partition_key
        ).label("end_time")

        row_rank_col = func.row_number().over(
            partition_by=partition_key,
            order_by=DetectionResult.frame_timestamp.desc(),
        ).label("row_rank")

        base_stmt = (
            select(
                DetectionResult.id.label("id"),
                DetectionResult.task_id.label("task_id"),
                DetectionResult.tracker_id.label("tracker_id"),
                DetectionResult.frame_timestamp.label("frame_timestamp"),
                DetectionResult.object_type.label("object_type"),
                DetectionResult.confidence.label("confidence"),
                DetectionResult.bbox_x1.label("bbox_x1"),
                DetectionResult.bbox_y1.label("bbox_y1"),
                DetectionResult.bbox_x2.label("bbox_x2"),
                DetectionResult.bbox_y2.label("bbox_y2"),
                DetectionResult.center_x.label("center_x"),
                DetectionResult.center_y.label("center_y"),
                DetectionResult.thumbnail_path.label("thumbnail_path"),
                AnalysisTask.task_name.label("task_name"),
                AnalysisTask.task_type.label("task_type"),
                AnalysisTask.camera_name.label("camera_name"),
                AnalysisTask.camera_id.label("camera_id"),
                AnalysisTask.id.label("analysis_task_id"),
                start_time_col,
                end_time_col,
                row_rank_col,
            )
            .outerjoin(AnalysisTask, DetectionResult.task_id == AnalysisTask.id)
        )

        filters = []

        if task_id is not None:
            filters.append(DetectionResult.task_id == task_id)

        if object_type:
            filters.append(DetectionResult.object_type.ilike(f"%{object_type}%"))

        if camera_name:
            filters.append(AnalysisTask.camera_name.ilike(f"%{camera_name}%"))

        if camera_id:
            filters.append(AnalysisTask.camera_id == camera_id)

        if normalized_start:
            filters.append(DetectionResult.frame_timestamp >= normalized_start)

        if normalized_end:
            filters.append(DetectionResult.frame_timestamp <= normalized_end)

        if min_confidence is not None:
            filters.append(DetectionResult.confidence >= min_confidence)

        if max_confidence is not None:
            filters.append(DetectionResult.confidence <= max_confidence)

        if search:
            keyword = f"%{search}%"
            filters.append(
                or_(
                    DetectionResult.object_type.ilike(keyword),
                    AnalysisTask.task_name.ilike(keyword),
                    AnalysisTask.camera_name.ilike(keyword),
                )
            )

        if filters:
            base_stmt = base_stmt.where(*filters)

        ranked_cte = base_stmt.cte("ranked_detections")

        deduplicated_stmt = (
            select(ranked_cte)
            .where(ranked_cte.c.row_rank == 1)
            .order_by(desc(ranked_cte.c.end_time))
            .offset(offset)
            .limit(limit)
        )

        count_stmt = (
            select(func.count())
            .select_from(ranked_cte)
            .where(ranked_cte.c.row_rank == 1)
        )

        result = await db.execute(deduplicated_stmt)
        detection_rows = result.fetchall()

        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one_or_none() or 0

        results = []
        for row in detection_rows:
            bbox_x1 = row.bbox_x1 or 0
            bbox_y1 = row.bbox_y1 or 0
            bbox_x2 = row.bbox_x2 or 0
            bbox_y2 = row.bbox_y2 or 0
            width = bbox_x2 - bbox_x1
            height = bbox_y2 - bbox_y1
            area = width * height

            results.append(
                {
                    "id": row.id,
                    "tracker_id": row.tracker_id,
                    "timestamp": row.frame_timestamp.isoformat()
                    if row.frame_timestamp
                    else None,
                    "start_time": row.start_time.isoformat()
                    if row.start_time
                    else None,
                    "end_time": row.end_time.isoformat() if row.end_time else None,
                    "task_id": row.task_id,
                    "task_name": row.task_name,
                    "task_type": row.task_type,
                    "camera_name": row.camera_name,
                    "camera_id": row.camera_id,
                    "object_type": row.object_type,
                    "object_chinese": row.object_type,
                    "object_id": f"obj_{row.id}",
                    "confidence": row.confidence,
                    "bbox_x1": row.bbox_x1,
                    "bbox_y1": row.bbox_y1,
                    "bbox_x2": row.bbox_x2,
                    "bbox_y2": row.bbox_y2,
                    "center_x": row.center_x,
                    "center_y": row.center_y,
                    "width": width,
                    "height": height,
                    "area": area,
                    "zone": f"zone_{row.task_id}",
                    "zone_chinese": f"區域{row.task_id}",
                    "status": "completed",
                    "thumbnail_path": row.thumbnail_path,
                    "thumbnail_url": _build_thumbnail_url(request, row.thumbnail_path),
                }
            )

        return {
            "results": results,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }

    except Exception as e:
        api_logger.error(f"獲取檢測結果失敗: {e}")
        raise HTTPException(status_code=500, detail=f"檢測結果獲取失敗: {str(e)}")

@router.get("/detection-results/{detection_id}")
async def get_detection_detail(
    detection_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """獲取特定檢測結果的詳細信息"""
    try:
        from app.models.database import DetectionResult
        from sqlalchemy import select
        from uuid import UUID
        
        # 查詢特定檢測結果
        stmt = select(DetectionResult).where(DetectionResult.id == UUID(detection_id))
        result = await db.execute(stmt)
        detection = result.scalar_one_or_none()
        
        if not detection:
            raise HTTPException(status_code=404, detail="檢測結果不存在")
        
        return {
            "id": str(detection.id),
            "timestamp": detection.frame_timestamp.isoformat() if detection.frame_timestamp else None,
            "camera_id": detection.camera_id,
            "object_type": detection.object_type,
            "confidence": detection.confidence,
            "bbox": detection.bbox,
            "status": "completed",
            "processing_time": getattr(detection, 'processing_time', None),
            "image_url": getattr(detection, 'image_url', None),
            "thumbnail_path": detection.thumbnail_path,
            "thumbnail_url": _build_thumbnail_url(request, detection.thumbnail_path),
        }
        
    except ValueError:
        raise HTTPException(status_code=400, detail="無效的檢測結果ID")
    except Exception as e:
        api_logger.error(f"獲取檢測詳情失敗: {e}")
        raise HTTPException(status_code=500, detail=f"檢測詳情獲取失敗: {str(e)}")

# ===== YOLO 模型管理 API =====

@router.get("/models")
async def get_available_models():
    """獲取可用的YOLO模型（來自 `uploads/models`）"""
    try:
        from pathlib import Path
        
        found_models = []
        
        # 預定義的模型信息
        model_info = {
            "yolo11n.pt": {
                "id": "yolov11n",
                "name": "YOLOv11n",
                "params": "2.6M",
                "speed": "Fast",
                "accuracy": "Good",
                "size_mb": 5.2
            },
            "yolo11s.pt": {
                "id": "yolov11s", 
                "name": "YOLOv11s",
                "params": "9.4M",
                "speed": "Fast",
                "accuracy": "Good",
                "size_mb": 18.8
            },
            "yolo11m.pt": {
                "id": "yolov11m",
                "name": "YOLOv11m", 
                "params": "20.1M",
                "speed": "Medium",
                "accuracy": "Better",
                "size_mb": 40.2
            },
            "yolo11l.pt": {
                "id": "yolov11l",
                "name": "YOLOv11l",
                "params": "25.3M", 
                "speed": "Medium",
                "accuracy": "Better",
                "size_mb": 50.6
            },
            "yolo11x.pt": {
                "id": "yolov11x",
                "name": "YOLOv11x",
                "params": "56.9M",
                "speed": "Slow", 
                "accuracy": "Best",
                "size_mb": 113.8
            }
        }
        
        # 掃描 uploads/models 下的模型文件
        models_dir = find_models_directory()
        if models_dir and models_dir.exists():
            for pt_file in models_dir.glob("*.pt"):
                filename = pt_file.name
                if filename in model_info:
                    file_size = pt_file.stat().st_size / (1024 * 1024)  # MB
                    model_data = model_info[filename].copy()
                    model_data.update({
                        "file": filename,
                        "status": "unloaded",  # 預設為未載入
                        "available": True,
                        "file_path": str(pt_file.absolute()),
                        "actual_size_mb": round(file_size, 1)
                    })
                    found_models.append(model_data)
        
        # 如果沒找到任何模型，返回預設的模型列表（但標记為不可用）
        if not found_models:
            for filename, info in model_info.items():
                model_data = info.copy()
                model_data.update({
                    "file": filename,
                    "status": "unavailable",
                    "available": False,
                    "file_path": None,
                    "actual_size_mb": 0
                })
                found_models.append(model_data)
        
        # 假設第一個可用的模型是當前載入的模型
        current_model = None
        available_models = [m for m in found_models if m["available"]]
        if available_models:
            available_models[0]["status"] = "loaded"
            current_model = available_models[0]["id"]
        
        return {
            "models": found_models,
            "current_model": current_model,
            "total_models": len(found_models),
            "available_models": len(available_models)
        }
        
    except Exception as e:
        api_logger.error(f"獲取模型列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型列表獲取失敗: {str(e)}")

@router.get("/models/current")
async def get_current_model():
    """獲取當前載入的模型"""
    try:
        return {
            "model_id": "yolov11s",
            "model_name": "YOLOv11s",
            "status": "loaded",
            "loaded_at": "2024-01-01T10:00:00Z"
        }
    except Exception as e:
        api_logger.error(f"獲取當前模型失敗: {e}")
        raise HTTPException(status_code=500, detail=f"當前模型獲取失敗: {str(e)}")

@router.post("/models/load")
async def load_model(request: ModelRequest):
    """載入指定的YOLO模型"""
    try:
        model_identifier = request.get_model_identifier()
        
        # 模擬載入延遲
        import asyncio
        await asyncio.sleep(1)
        
        return {
            "message": f"模型 {model_identifier} 載入成功",
            "model_id": model_identifier,
            "status": "loaded"
        }
        
    except ValueError as e:
        api_logger.error(f"模型識別符錯誤: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        api_logger.error(f"載入模型失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型載入失敗: {str(e)}")

@router.post("/models/unload")
async def unload_model(request: ModelRequest):
    """卸載指定的YOLO模型"""
    try:
        model_identifier = request.get_model_identifier()
        
        # 模擬卸載延遲
        import asyncio
        await asyncio.sleep(0.5)
        
        return {
            "message": f"模型 {model_identifier} 卸載成功",
            "model_id": model_identifier,
            "status": "unloaded"
        }
        
    except ValueError as e:
        api_logger.error(f"模型識別符錯誤: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        api_logger.error(f"卸載模型失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型卸載失敗: {str(e)}")

@router.get("/models/config")
async def get_model_config():
    """獲取模型配置"""
    try:
        return {
            "confidence": 0.5,
            "iou": 0.45,
            "image_size": 640,
            "max_detections": 1000,
            "classes": None  # None 表示檢測所有類別
        }
    except Exception as e:
        api_logger.error(f"獲取模型配置失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型配置獲取失敗: {str(e)}")

@router.put("/models/config")
async def update_model_config(config: ModelConfigUpdate):
    """更新模型配置"""
    try:
        # 驗證配置參數
        confidence = config.confidence if config.confidence is not None else 0.5
        iou = config.iou_threshold if config.iou_threshold is not None else 0.45
        image_size = config.image_size if config.image_size is not None else 640
        
        if not (0.0 <= confidence <= 1.0):
            raise HTTPException(status_code=400, detail="confidence 必須在 0.0 到 1.0 之間")
        if not (0.0 <= iou <= 1.0):
            raise HTTPException(status_code=400, detail="iou 必須在 0.0 到 1.0 之間")
        if image_size not in [320, 480, 640, 800, 1024]:
            raise HTTPException(status_code=400, detail="image_size 必須是 320, 480, 640, 800, 1024 中的一個")
        
        # 模擬保存配置
        return {
            "message": "模型配置更新成功",
            "config": {
                "confidence": confidence,
                "iou": iou,
                "image_size": image_size
            }
        }
    except Exception as e:
        api_logger.error(f"更新模型配置失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型配置更新失敗: {str(e)}")

@router.put("/models/{model_name}/load")
async def load_model_legacy(model_name: str):
    """載入指定的YOLO模型（舊版API，向後兼容）"""
    try:
        # 模擬載入延遲
        import asyncio
        await asyncio.sleep(1)
        
        return {
            "message": f"模型 {model_name} 載入成功",
            "model_name": model_name
        }
        
    except Exception as e:
        api_logger.error(f"載入模型失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型載入失敗: {str(e)}")

@router.post("/models/upload")
async def upload_model(file: UploadFile = File(...)):
    """上傳自定義模型"""
    try:
        if not file.filename.endswith('.pt'):
            raise HTTPException(status_code=400, detail="只支援 .pt 格式的模型文件")
        
        # 保存上傳的模型文件
        model_dir = "models"
        os.makedirs(model_dir, exist_ok=True)
        file_path = os.path.join(model_dir, file.filename)
        
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        return {
            "message": f"模型 {file.filename} 上傳成功",
            "file_path": file_path
        }
        
    except Exception as e:
        api_logger.error(f"上傳模型失敗: {e}")
        raise HTTPException(status_code=500, detail=f"模型上傳失敗: {str(e)}")

# ===== 即時檢測 API =====

@router.get("/detection/stream/{camera_id}")
async def get_detection_stream(camera_id: str):
    """獲取攝影機檢測串流（WebSocket將在另一個文件實現）"""
    try:
        # 這裡返回串流端點信息
        return {
            "camera_id": camera_id,
            "stream_url": f"/ws/detection/{camera_id}",
            "message": "請使用WebSocket連接獲取即時檢測串流"
        }
        
    except Exception as e:
        api_logger.error(f"獲取檢測串流失敗: {e}")
        raise HTTPException(status_code=500, detail=f"檢測串流獲取失敗: {str(e)}")

# ===== 資料來源管理 API =====

@router.get("/data-sources", response_model=List[DataSourceInfo])
async def get_data_sources(
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """獲取資料來源列表"""
    try:
        db_service = DatabaseService()
        sources = await db_service.get_data_sources(db, source_type=source_type, status=status)
        
        return [
            DataSourceInfo(
                id=source.id,
                source_type=source.source_type,
                name=source.name,
                config=source.config or {},
                status=source.status,
                last_check=source.last_check,
                created_at=source.created_at
            )
            for source in sources
        ]
        
    except Exception as e:
        api_logger.error(f"獲取資料來源列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取資料來源列表失敗: {str(e)}")

@router.post("/data-sources", response_model=DataSourceInfo)
async def create_data_source(
    source_data: DataSourceCreate,
    db: AsyncSession = Depends(get_db)
):
    """創建新的資料來源"""
    try:
        api_logger.info(f"📥 收到創建資料來源請求: {source_data.dict()}")
        
        db_service = DatabaseService()
        
        # 驗證配置格式
        if source_data.source_type == "camera":
            api_logger.info(f"🔍 驗證攝影機配置: {source_data.config}")
            # 驗證攝影機配置
            if "device_id" not in source_data.config and "url" not in source_data.config:
                api_logger.error(f"❌ 攝影機配置驗證失敗: 缺少 device_id 或 url")
                raise HTTPException(status_code=400, detail="攝影機來源必須提供 device_id 或 url")
        elif source_data.source_type == "video_file":
            # 驗證影片檔案配置
            if "file_path" not in source_data.config:
                raise HTTPException(status_code=400, detail="影片檔案來源必須提供 file_path")
        elif source_data.source_type == "image_folder":
            # 驗證圖片資料夾配置
            if "folder_path" not in source_data.config:
                raise HTTPException(status_code=400, detail="圖片資料夾來源必須提供 folder_path")
        
        source = await db_service.create_data_source(db, {
            "source_type": source_data.source_type,
            "name": source_data.name,
            "config": source_data.config,
            "status": "active"
        })
        
        return DataSourceInfo(
            id=source.id,
            source_type=source.source_type,
            name=source.name,
            config=source.config or {},
            status=source.status,
            last_check=source.last_check,
            created_at=source.created_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"創建資料來源失敗: {e}")
        raise HTTPException(status_code=500, detail=f"創建資料來源失敗: {str(e)}")

@router.get("/data-sources/{source_id}", response_model=DataSourceInfo)
async def get_data_source(
    source_id: int,
    db: AsyncSession = Depends(get_db)
):
    """獲取特定資料來源詳情"""
    try:
        db_service = DatabaseService()
        source = await db_service.get_data_source(db, source_id)
        
        if not source:
            raise HTTPException(status_code=404, detail="資料來源不存在")
        
        return DataSourceInfo(
            id=source.id,
            source_type=source.source_type,
            name=source.name,
            config=source.config or {},
            status=source.status,
            last_check=source.last_check,
            created_at=source.created_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"獲取資料來源詳情失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取資料來源詳情失敗: {str(e)}")

@router.put("/data-sources/{source_id}", response_model=DataSourceInfo)
async def update_data_source(
    source_id: int,
    update_data: DataSourceUpdate,
    db: AsyncSession = Depends(get_db)
):
    """更新資料來源"""
    try:
        db_service = DatabaseService()
        
        # 檢查資料來源是否存在
        source = await db_service.get_data_source(db, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="資料來源不存在")
        
        # 準備更新數據
        update_dict = {}
        if update_data.name is not None:
            update_dict["name"] = update_data.name
        if update_data.config is not None:
            update_dict["config"] = update_data.config
        if update_data.status is not None:
            update_dict["status"] = update_data.status
            update_dict["last_check"] = datetime.now()
        
        # 執行更新
        from sqlalchemy import update
        await db.execute(
            update(DataSource)
            .where(DataSource.id == source_id)
            .values(**update_dict)
        )
        await db.commit()
        
        # 返回更新後的資料
        updated_source = await db_service.get_data_source(db, source_id)
        
        return DataSourceInfo(
            id=updated_source.id,
            source_type=updated_source.source_type,
            name=updated_source.name,
            config=updated_source.config or {},
            status=updated_source.status,
            last_check=updated_source.last_check,
            created_at=updated_source.created_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"更新資料來源失敗: {e}")
        raise HTTPException(status_code=500, detail=f"更新資料來源失敗: {str(e)}")

@router.delete("/data-sources/{source_id}")
async def delete_data_source(
    source_id: int,
    db: AsyncSession = Depends(get_db)
):
    """刪除資料來源"""
    try:
        # 檢查資料來源是否存在
        from sqlalchemy import select, delete
        
        result = await db.execute(
            select(DataSource).where(DataSource.id == source_id)
        )
        source = result.scalar_one_or_none()
        
        if not source:
            raise HTTPException(status_code=404, detail="資料來源不存在")
        
        source_name = source.name
        
        # 執行刪除 (簡化版本，跳過複雜的關聯檢查)
        await db.execute(
            delete(DataSource).where(DataSource.id == source_id)
        )
        await db.commit()
        
        api_logger.info(f"資料來源 {source_name} (ID: {source_id}) 已被刪除")
        return {"message": f"資料來源 {source_name} 已成功刪除"}
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"刪除資料來源失敗 (ID: {source_id}): {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"刪除資料來源失敗: {str(e)}")

@router.post("/data-sources/{source_id}/test")
async def test_data_source(
    source_id: int,
    db: AsyncSession = Depends(get_db)
):
    """測試資料來源連接"""
    try:
        db_service = DatabaseService()
        source = await db_service.get_data_source(db, source_id)
        
        if not source:
            raise HTTPException(status_code=404, detail="資料來源不存在")
        
        test_result = {"source_id": source_id, "status": "success", "message": ""}
        
        if source.source_type == "camera":
            # 測試攝影機連接 (使用攝影機流管理器)
            config = source.config or {}
            if "device_id" in config:
                # USB 攝影機測試
                try:
                    from app.services.camera_stream_manager import camera_stream_manager
                    resolution_info = camera_stream_manager.get_camera_resolution(config["device_id"])
                    if resolution_info:
                        test_result["message"] = f"USB攝影機 {config['device_id']} 連接正常 ({resolution_info['width']}x{resolution_info['height']})"
                    else:
                        test_result["status"] = "error"
                        test_result["message"] = f"無法開啟USB攝影機 {config['device_id']}"
                except Exception as e:
                    test_result["status"] = "error"
                    test_result["message"] = f"USB攝影機測試失敗: {str(e)}"
            
            elif "url" in config:
                # 網路攝影機測試 (暫時仍使用直接存取，因為流管理器主要處理USB攝影機)
                import cv2
                try:
                    cap = cv2.VideoCapture(config["url"])
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret:
                            test_result["message"] = f"網路攝影機 {config['url']} 連接正常"
                        else:
                            test_result["status"] = "error"
                            test_result["message"] = f"網路攝影機 {config['url']} 無法讀取幀"
                    else:
                        test_result["status"] = "error"
                        test_result["message"] = f"無法連接網路攝影機 {config['url']}"
                    cap.release()
                except Exception as e:
                    test_result["status"] = "error"
                    test_result["message"] = f"網路攝影機測試失敗: {str(e)}"
        
        elif source.source_type == "video_file":
            # 測試影片檔案
            config = source.config or {}
            file_path = config.get("file_path")
            if file_path and os.path.exists(file_path):
                import cv2
                try:
                    cap = cv2.VideoCapture(file_path)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret:
                            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            fps = cap.get(cv2.CAP_PROP_FPS)
                            duration = frame_count / fps if fps > 0 else 0
                            test_result["message"] = f"影片檔案 {file_path} 可正常讀取，總幀數: {frame_count}，時長: {duration:.2f}秒"
                        else:
                            test_result["status"] = "error"
                            test_result["message"] = f"影片檔案 {file_path} 無法讀取幀"
                    else:
                        test_result["status"] = "error"
                        test_result["message"] = f"無法開啟影片檔案 {file_path}"
                    cap.release()
                except Exception as e:
                    test_result["status"] = "error"
                    test_result["message"] = f"影片檔案測試失敗: {str(e)}"
            else:
                test_result["status"] = "error"
                test_result["message"] = f"影片檔案 {file_path} 不存在"
        
        elif source.source_type == "image_folder":
            # 測試圖片資料夾
            config = source.config or {}
            folder_path = config.get("folder_path")
            if folder_path and os.path.exists(folder_path):
                supported_formats = config.get("supported_formats", ["jpg", "jpeg", "png", "bmp"])
                scan_subdirs = config.get("scan_subdirs", False)
                
                image_count = 0
                if scan_subdirs:
                    for root, dirs, files in os.walk(folder_path):
                        for file in files:
                            if any(file.lower().endswith(f".{fmt}") for fmt in supported_formats):
                                image_count += 1
                else:
                    for file in os.listdir(folder_path):
                        if any(file.lower().endswith(f".{fmt}") for fmt in supported_formats):
                            image_count += 1
                
                test_result["message"] = f"圖片資料夾 {folder_path} 找到 {image_count} 張圖片"
                if image_count == 0:
                    test_result["status"] = "warning"
                    test_result["message"] += "（未找到支援格式的圖片）"
            else:
                test_result["status"] = "error"
                test_result["message"] = f"圖片資料夾 {folder_path} 不存在"
        
        # 更新測試狀態到資料庫
        await db_service.update_data_source_status(
            db, source_id, 
            "active" if test_result["status"] == "success" else "error"
        )
        
        return test_result
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"測試資料來源失敗: {e}")
        raise HTTPException(status_code=500, detail=f"測試資料來源失敗: {str(e)}")

@router.get("/data-sources/types/camera/devices")
async def get_available_cameras():
    """獲取可用的攝影機設備 (使用攝影機流管理器避免資源衝突)"""
    try:
        from app.services.camera_stream_manager import camera_stream_manager
        available_cameras = camera_stream_manager.detect_available_cameras()
        
        return {
            "cameras": available_cameras,
            "message": f"找到 {len(available_cameras)} 個可用的攝影機"
        }
        
    except Exception as e:
        api_logger.error(f"獲取可用攝影機失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取可用攝影機失敗: {str(e)}")

@router.get("/data-sources/types/video/files")
async def get_video_files(directory: Optional[str] = None):
    """獲取指定目錄下的影片檔案"""
    try:
        base_dir = directory or (str(find_videos_directory()) if find_videos_directory() else ".")
        if not os.path.exists(base_dir):
            raise HTTPException(status_code=404, detail="目錄不存在")
        
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm']
        video_files = []
        
        for file in os.listdir(base_dir):
            file_path = os.path.join(base_dir, file)
            if os.path.isfile(file_path):
                _, ext = os.path.splitext(file)
                if ext.lower() in video_extensions:
                    stat = os.stat(file_path)
                    video_files.append({
                        "filename": file,
                        "path": file_path,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
        
        return {
            "directory": base_dir,
            "files": video_files,
            "count": len(video_files)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"獲取影片檔案失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取影片檔案失敗: {str(e)}")

@router.post("/data-sources/upload/video")
async def upload_video_file(file: UploadFile = File(...)):
    """上傳影片檔案"""
    try:
        # 檢查檔案類型
        allowed_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm']
        file_ext = os.path.splitext(file.filename)[1].lower()
        
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"不支援的檔案格式。支援的格式: {', '.join(allowed_extensions)}"
            )
        
        # 檢查檔案大小 (限制為 500MB)
        max_size = 500 * 1024 * 1024  # 500MB
        if file.size and file.size > max_size:
            raise HTTPException(
                status_code=400, 
                detail=f"檔案太大。最大支援 500MB，您的檔案為 {file.size / 1024 / 1024:.1f}MB"
            )
        
        # 創建上傳目錄（uploads/videos）
        videos_dir = find_videos_directory() or (Path("uploads") / "videos")
        upload_dir = str(videos_dir)
        os.makedirs(upload_dir, exist_ok=True)
        
        # 生成唯一檔案名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"{timestamp}_{file.filename}"
        file_path = os.path.join(upload_dir, safe_filename)
        
        # 保存檔案
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # 驗證影片檔案
        try:
            import cv2
            cap = cv2.VideoCapture(file_path)
            if not cap.isOpened():
                os.remove(file_path)  # 刪除無效檔案
                raise HTTPException(status_code=400, detail="無效的影片檔案")
            
            # 獲取影片資訊
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            duration = frame_count / fps if fps > 0 else 0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail=f"影片檔案驗證失敗: {str(e)}")
        
        # 創建資料來源記錄
        async with AsyncSessionLocal() as db:
            db_service = DatabaseService()
            
            # 創建資料來源配置
            video_config = {
                "path": file_path,  # 使用 "path" 符合模型期望
                "file_path": file_path,  # 保留向後相容性
                "original_name": file.filename,
                "file_size": os.path.getsize(file_path),
                "duration": round(duration, 2),
                "fps": round(fps, 2),
                "resolution": f"{width}x{height}",
                "frame_count": frame_count,
                "upload_time": datetime.now().isoformat()
            }
            
            # 創建資料來源記錄
            source_data = {
                "source_type": "video_file",  # 使用 "video_file" 符合資料庫約束
                "name": file.filename,
                "config": video_config,
                "status": "active"
            }
            
            created_source = await db_service.create_data_source(db, source_data)
            
            return {
                "message": f"影片檔案 {file.filename} 上傳成功",
                "source_id": created_source.id,
                "file_path": file_path,
                "original_name": file.filename,
                "size": os.path.getsize(file_path),
                "video_info": {
                    "duration": round(duration, 2),
                    "fps": round(fps, 2),
                    "resolution": f"{width}x{height}",
                    "frame_count": frame_count
                }
            }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"上傳影片檔案失敗: {e}")
        raise HTTPException(status_code=500, detail=f"上傳影片檔案失敗: {str(e)}")

@router.get("/data-sources/upload/video/progress/{task_id}")
async def get_upload_progress(task_id: str):
    """獲取上傳進度（未來功能）"""
    return {
        "task_id": task_id,
        "progress": 100,
        "status": "completed",
        "message": "上傳完成"
    }

# ===== 數據管理 API =====

@router.get("/storage-analysis")
async def get_storage_analysis():
    """獲取儲存空間分析"""
    try:
        import os
        import psutil
        from pathlib import Path
        
        # 計算不同類型文件的大小
        detection_size = 0
        video_size = 0
        log_size = 0
        
        # 檢測結果數據庫大小（估算）
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(text("SELECT COUNT(*) FROM detection_results"))
                count = result.scalar()
                detection_size = count * 500  # 估算每筆記錄約500字節
        except:
            detection_size = 0
        
        # 檢查影片文件夾大小
        video_dirs = ["uploads", "videos", "media"]
        for dir_name in video_dirs:
            dir_path = Path(dir_name)
            if dir_path.exists():
                for file_path in dir_path.rglob("*"):
                    if file_path.is_file():
                        video_size += file_path.stat().st_size
        
        # 檢查日誌文件夾大小
        log_dirs = ["logs", "log"]
        for dir_name in log_dirs:
            dir_path = Path(dir_name)
            if dir_path.exists():
                for file_path in dir_path.rglob("*"):
                    if file_path.is_file():
                        log_size += file_path.stat().st_size
        
        total_size = detection_size + video_size + log_size
        
        return {
            "detection_size": detection_size,
            "video_size": video_size,
            "log_size": log_size,
            "total_size": total_size,
            "free_space": psutil.disk_usage('.').free
        }
    except Exception as e:
        api_logger.error(f"獲取儲存分析失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取儲存分析失敗: {str(e)}")

@router.get("/quick-stats")
async def get_quick_stats():
    """獲取快速統計數據"""
    try:
        async with AsyncSessionLocal() as db:
            # 今日檢測數
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            result = await db.execute(text("""
                SELECT COUNT(*) FROM detection_results 
                WHERE timestamp >= :today_start
            """), {"today_start": today_start})
            today_detections = result.scalar() or 0
            
            # 平均信心度
            result = await db.execute(text("SELECT AVG(confidence) FROM detection_results"))
            avg_confidence = result.scalar() or 0
            
            # 最常見物件
            result = await db.execute(text("""
                SELECT object_type, COUNT(*) as count 
                FROM detection_results 
                GROUP BY object_type 
                ORDER BY count DESC 
                LIMIT 1
            """))
            most_common = result.fetchone()
            most_common_object = most_common[0] if most_common else "N/A"
            
            # 活躍時段
            result = await db.execute(text("""
                SELECT EXTRACT(hour FROM timestamp) as hour, COUNT(*) as count
                FROM detection_results 
                GROUP BY hour 
                ORDER BY count DESC 
                LIMIT 1
            """))
            peak_hour = result.fetchone()
            peak_hours = f"{int(peak_hour[0]):02d}:00-{int(peak_hour[0])+1:02d}:00" if peak_hour else "N/A"
            
            # 高信心度檢測百分比
            result = await db.execute(text("""
                SELECT 
                    CASE 
                        WHEN COUNT(*) > 0 THEN
                            COUNT(CASE WHEN confidence >= 0.8 THEN 1 END) * 100.0 / COUNT(*)
                        ELSE 0 
                    END as high_confidence_percentage
                FROM detection_results
            """))
            high_confidence_percentage = result.scalar() or 0
            
            # 追蹤連續性（簡化計算）
            tracking_continuity = 85.0  # 假設值，實際應該根據object_id連續性計算
            
            return {
                "today_detections": int(today_detections),
                "avg_confidence": float(avg_confidence),
                "most_common_object": most_common_object,
                "peak_hours": peak_hours,
                "high_confidence_percentage": float(high_confidence_percentage),
                "tracking_continuity": tracking_continuity
            }
    except Exception as e:
        api_logger.error(f"獲取快速統計失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取快速統計失敗: {str(e)}")

@router.get("/export-data")
async def export_data(
    format: str = Query(..., description="匯出格式: csv, json, excel"),
    ids: Optional[str] = Query(None, description="要匯出的記錄ID，用逗號分隔"),
    object_type: Optional[str] = Query(None, description="物件類型篩選"),
    confidence_min: Optional[float] = Query(None, description="最小信心度"),
    confidence_max: Optional[float] = Query(None, description="最大信心度"),
    start_date: Optional[str] = Query(None, description="開始日期"),
    end_date: Optional[str] = Query(None, description="結束日期")
):
    """匯出檢測數據"""
    try:
        from fastapi.responses import StreamingResponse
        import csv
        import json
        import io
        
        async with AsyncSessionLocal() as db:
            # 構建查詢
            query = "SELECT * FROM detection_results WHERE 1=1"
            params = {}
            
            if ids:
                id_list = [int(id.strip()) for id in ids.split(',')]
                query += " AND id = ANY(:ids)"
                params["ids"] = id_list
            
            if object_type:
                query += " AND object_type = :object_type"
                params["object_type"] = object_type
            
            if confidence_min is not None:
                query += " AND confidence >= :confidence_min"
                params["confidence_min"] = confidence_min
                
            if confidence_max is not None:
                query += " AND confidence <= :confidence_max"
                params["confidence_max"] = confidence_max
            
            if start_date:
                query += " AND timestamp >= :start_date"
                params["start_date"] = start_date
                
            if end_date:
                query += " AND timestamp <= :end_date"
                params["end_date"] = end_date
            
            query += " ORDER BY timestamp DESC"
            
            result = await db.execute(text(query), params)
            records = result.fetchall()
            
            if format.lower() == 'csv':
                output = io.StringIO()
                writer = csv.writer(output)
                
                # 寫入標題
                if records:
                    writer.writerow(records[0]._fields)
                    
                # 寫入數據
                for record in records:
                    writer.writerow(record)
                
                output.seek(0)
                return StreamingResponse(
                    io.BytesIO(output.getvalue().encode('utf-8')),
                    media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=detection_results_{datetime.now().strftime('%Y%m%d')}.csv"}
                )
            
            elif format.lower() == 'json':
                data = []
                for record in records:
                    record_dict = {}
                    for i, field in enumerate(record._fields):
                        value = record[i]
                        if isinstance(value, datetime):
                            value = value.isoformat()
                        record_dict[field] = value
                    data.append(record_dict)
                
                json_str = json.dumps(data, ensure_ascii=False, indent=2)
                return StreamingResponse(
                    io.BytesIO(json_str.encode('utf-8')),
                    media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=detection_results_{datetime.now().strftime('%Y%m%d')}.json"}
                )
            
            else:
                raise HTTPException(status_code=400, detail="不支援的匯出格式")
                
    except Exception as e:
        api_logger.error(f"匯出數據失敗: {e}")
        raise HTTPException(status_code=500, detail=f"匯出數據失敗: {str(e)}")

@router.delete("/detection-results/{detection_id}")
async def delete_detection_result(detection_id: int):
    """刪除檢測結果"""
    try:
        async with AsyncSessionLocal() as db:
            # 檢查記錄是否存在
            result = await db.execute(
                text("SELECT id FROM detection_results WHERE id = :id"),
                {"id": detection_id}
            )
            if not result.fetchone():
                raise HTTPException(status_code=404, detail="記錄不存在")
            
            # 刪除記錄
            await db.execute(
                text("DELETE FROM detection_results WHERE id = :id"),
                {"id": detection_id}
            )
            await db.commit()
            
            return {"message": "記錄已刪除", "id": detection_id}
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"刪除檢測結果失敗: {e}")
        raise HTTPException(status_code=500, detail=f"刪除檢測結果失敗: {str(e)}")

@router.post("/optimize-database")
async def optimize_database():
    """優化資料庫"""
    try:
        async with AsyncSessionLocal() as db:
            # PostgreSQL 優化命令
            await db.execute(text("VACUUM ANALYZE detection_results"))
            await db.execute(text("VACUUM ANALYZE analysis_tasks"))
            await db.execute(text("VACUUM ANALYZE behavior_events"))
            await db.commit()
            
        return {"message": "資料庫優化完成"}
    except Exception as e:
        api_logger.error(f"優化資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail=f"優化資料庫失敗: {str(e)}")

@router.post("/clear-database")
async def clear_database():
    """清空資料庫（危險操作）"""
    try:
        async with AsyncSessionLocal() as db:
            async def table_exists(table: str) -> bool:
                result = await db.execute(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": f"public.{table}"},
                )
                return result.scalar() is not None

            tables = ["behavior_events", "detection_results", "analysis_tasks"]
            for table in tables:
                if await table_exists(table):
                    await db.execute(text(f'DELETE FROM {table}'))

            await db.commit()
            
        return {"message": "資料庫已清空"}
    except Exception as e:
        api_logger.error(f"清空資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail=f"清空資料庫失敗: {str(e)}")


@router.get("/database/backup/settings", response_model=DatabaseBackupInfo)
async def get_database_backup_settings():
    """取得資料庫備份設定與狀態"""
    return await _build_backup_info()


@router.put("/database/backup/settings", response_model=DatabaseBackupInfo)
async def update_database_backup_settings(payload: UpdateDatabaseBackupSettingsRequest):
    """更新資料庫備份設定"""
    config = _load_backup_config()
    config.backup_type = payload.backup_type
    config.backup_location = payload.backup_location
    config.auto_backup_enabled = payload.auto_backup_enabled
    config.backup_frequency = payload.backup_frequency
    config.retention_days = payload.retention_days
    _save_backup_config(config)
    return await _build_backup_info(config)


@router.post("/database/backup/run", response_model=ManualBackupResponse)
async def run_manual_database_backup():
    """立即執行資料庫備份"""
    config = _load_backup_config()
    backup_dir = _resolve_backup_location(config.backup_location)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"postgres_backup_{timestamp}.sql"
    backup_path = backup_dir / backup_filename

    try:
        _run_pg_dump_to_file(backup_path)
    except FileNotFoundError:
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="找不到 pg_dump 工具，且未設定 POSTGRES_CONTAINER_NAME 供 docker exec 使用")
    except subprocess.CalledProcessError as exc:
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"備份執行失敗: {exc.stderr or exc.stdout}")

    size = backup_path.stat().st_size if backup_path.exists() else 0
    config.last_backup_time = datetime.now()
    config.last_backup_file = backup_filename
    config.last_backup_size = size
    _save_backup_config(config)
    _cleanup_expired_backups(config.backup_location, config.retention_days)

    return ManualBackupResponse(
        message="資料庫備份完成",
        backup_file=backup_filename,
        backup_path=str(backup_path),
        size=size,
        download_url=f"/api/v1/frontend/database/backup/download/{backup_filename}",
        finished_at=config.last_backup_time,
    )


@router.post("/database/backup/restore", response_model=RestoreBackupResponse)
async def restore_database_backup(file: UploadFile = File(...)):
    """從上傳的 SQL 檔案還原資料庫"""
    if not file.filename.lower().endswith(".sql"):
        raise HTTPException(status_code=400, detail="僅支援 .sql 備份檔案")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
        temp_path = Path(tmp.name)
        content = await file.read()
        tmp.write(content)

    try:
        _run_psql_restore_from_file(temp_path)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="找不到 psql 工具，且未設定 POSTGRES_CONTAINER_NAME 供 docker exec 使用")
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"還原失敗: {exc.stderr or exc.stdout}")
    finally:
        temp_path.unlink(missing_ok=True)
    try:
        from app.services.camera_stream_manager import camera_stream_manager
        camera_stream_manager.stop_all_streams()
        api_logger.info("已在資料庫還原後重置所有攝影機串流")
    except Exception as exc:  # noqa: BLE001
        api_logger.warning(f"重置攝影機串流失敗: {exc}")

    return RestoreBackupResponse(message="資料庫還原完成", restored_at=datetime.now())


@router.get("/database/backup/download/{backup_name}")
async def download_database_backup(backup_name: str):
    """下載指定的備份檔案"""
    config = _load_backup_config()
    backup_dir = _resolve_backup_location(config.backup_location)
    if not backup_dir.exists():
        raise HTTPException(status_code=404, detail="備份目錄不存在")

    requested_path = (backup_dir / backup_name).resolve()
    try:
        requested_path.relative_to(backup_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="備份檔案名稱不合法")

    if not requested_path.exists():
        raise HTTPException(status_code=404, detail="找不到指定的備份檔案")

    return FileResponse(
        requested_path,
        filename=backup_name,
        media_type="application/sql",
    )

@router.get("/backup-database")
async def backup_database():
    """備份資料庫"""
    try:
        from fastapi.responses import FileResponse
        import subprocess
        import tempfile
        import os
        
        # 創建臨時文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as f:
            temp_file = f.name
        
        try:
            # 使用 pg_dump 備份（需要配置）
            # 這裡使用簡化的SQL導出
            async with AsyncSessionLocal() as db:
                backup_sql = "-- Database Backup\n"
                
                # 導出analysis_tasks
                result = await db.execute(text("SELECT * FROM analysis_tasks"))
                records = result.fetchall()
                if records:
                    backup_sql += "\n-- Analysis Tasks\n"
                    for record in records:
                        backup_sql += f"INSERT INTO analysis_tasks VALUES {tuple(record)};\n"
                
                # 寫入文件
                with open(temp_file, 'w', encoding='utf-8') as f:
                    f.write(backup_sql)
            
            return FileResponse(
                temp_file,
                filename=f"database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
                media_type="application/sql"
            )
        finally:
            # 清理臨時文件將在請求完成後進行
            pass
            
    except Exception as e:
        api_logger.error(f"備份資料庫失敗: {e}")
        raise HTTPException(status_code=500, detail=f"備份資料庫失敗: {str(e)}")

@router.post("/system/shutdown")
async def shutdown_system():
    """停止整個系統"""
    try:
        import os
        import signal
        import asyncio
        import psutil
        import threading
        import time
        
        api_logger.info("收到系統停止請求")
        
        def delayed_shutdown():
            """延遲執行停止，確保回應先發送"""
            time.sleep(1)
            
            try:
                # 獲取當前進程和父進程
                current_process = psutil.Process()
                parent_process = current_process.parent()
                
                api_logger.info("執行系統停止")
                
                # 如果是Windows，發送KeyboardInterrupt到父進程
                if os.name == 'nt':  # Windows
                    # 使用更溫和的方式，發送CTRL+C事件
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    # 發送 CTRL+C 到主控制台
                    kernel32.GenerateConsoleCtrlEvent(0, parent_process.pid)
                    
                    # 如果溫和方式失敗，再使用強制終止
                    time.sleep(3)
                    if parent_process.is_running():
                        os.system(f'taskkill /F /T /PID {parent_process.pid}')
                else:  # Unix/Linux
                    # 發送SIGINT到父進程（相當於Ctrl+C）
                    parent_process.send_signal(signal.SIGINT)
                
            except Exception as e:
                print(f"停止過程中發生錯誤: {e}")
                api_logger.error(f"停止過程中發生錯誤: {e}")
                # 最後的強制退出
                os._exit(0)
        
        # 在背景執行停止
        threading.Thread(target=delayed_shutdown, daemon=True).start()
        
        # 返回確認訊息給前端
        response = {
            "success": True,
            "message": "系統停止指令已發送",
            "timestamp": datetime.now().isoformat()
        }
        
        return JSONResponse(content=response)
        
    except Exception as e:
        api_logger.error(f"停止系統失敗: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"停止系統失敗: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
        )

@router.post("/system/restart")
async def restart_system():
    """重新啟動整個系統"""
    try:
        import sys
        import threading

        api_logger.info("收到系統重新啟動請求")

        project_root = get_base_dir()
        env_command = os.environ.get("SYSTEM_RESTART_COMMAND")
        restart_commands: List[Tuple[Union[str, List[str]], bool]] = []
        if env_command:
            restart_commands.append((env_command, True))

        # 預設改以 uv run poe dev-api 重新啟動；若環境沒有 uv 則退回 uvicorn
        restart_commands.append(("uv run poe dev-api", True))
        restart_commands.append(
            ([sys.executable, "-m", "uvicorn", "main:app", "--reload"], False)
        )

        def _spawn_command(command: Union[str, List[str]], use_shell: bool) -> None:
            """
            啟動新的後端進程，必要時在 Windows 上建立新的 process group，
            避免稍後終止父進程時連帶被中斷。
            """
            popen_kwargs: Dict[str, Any] = {
                "cwd": str(project_root),
                "close_fds": True,
            }
            if os.name == "nt":  # Windows
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                popen_kwargs["creationflags"] = creationflags
            else:  # Unix-like
                popen_kwargs["start_new_session"] = True

            if use_shell:
                subprocess.Popen(command, shell=True, **popen_kwargs)
            else:
                subprocess.Popen(command, **popen_kwargs)

        def _stop_parent_reloader() -> None:
            """
            當前 API 執行環境位於 uvicorn --reload 的子進程內時，
            需要連同父層重載進程一起終止，避免殘留多個 uvicorn 實例。
            """
            try:
                import psutil
            except Exception as exc:  # noqa: BLE001
                api_logger.warning(f"無法匯入 psutil，略過父進程清理: {exc}")
                return

            try:
                current = psutil.Process()
                parent = current.parent()
            except Exception as exc:  # noqa: BLE001
                api_logger.warning(f"無法取得進程資訊，略過清理: {exc}")
                return

            if not parent:
                return

            try:
                parent_name = (parent.name() or "").lower()
            except Exception:  # noqa: BLE001
                parent_name = ""
            try:
                parent_cmd = " ".join(parent.cmdline()).lower()
            except Exception:  # noqa: BLE001
                parent_cmd = ""

            if not parent_name.startswith("python") or "uvicorn" not in parent_cmd:
                api_logger.debug(
                    "父進程 %s 非 uvicorn 監控進程，略過終止", parent.pid
                )
                return

            try:
                api_logger.info(f"終止 uvicorn 重載父進程 (PID {parent.pid})")
                parent.terminate()
                try:
                    parent.wait(timeout=5)
                except psutil.TimeoutExpired:
                    api_logger.warning("父進程未於 5 秒內結束，改以 kill 強制終止")
                    parent.kill()
            except Exception as exc:  # noqa: BLE001
                api_logger.error(f"終止父進程失敗 (PID {parent.pid}): {exc}")

        def delayed_restart():
            time.sleep(1)
            launched = False
            for command, use_shell in restart_commands:
                if not command:
                    continue
                try:
                    api_logger.info(
                        f"嘗試以命令重新啟動系統: {command} (shell={use_shell})"
                    )
                    _spawn_command(command, use_shell)
                    launched = True
                    break
                except Exception as launch_error:  # noqa: BLE001
                    api_logger.error(
                        f"重新啟動命令失敗: {command} ({launch_error})"
                    )
                    continue

            if launched:
                api_logger.info("新系統進程啟動成功，開始清理既有進程")
                _stop_parent_reloader()
            else:
                api_logger.error("無法啟動新的系統進程，保留原進程運作")
                return

            os._exit(0)

        threading.Thread(target=delayed_restart, daemon=True).start()

        return JSONResponse(
            content={
                "success": True,
                "message": "系統重新啟動指令已發送",
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:  # noqa: BLE001
        api_logger.error(f"重新啟動系統失敗: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"重新啟動系統失敗: {str(e)}",
                "timestamp": datetime.now().isoformat(),
            },
        )

@router.get("/system/status")
async def get_system_status():
    """獲取系統狀態"""
    try:
        import psutil
        import sys
        
        # 獲取系統資源使用情況
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # 獲取Python進程信息
        process = psutil.Process()
        
        status = {
            "running": True,
            "uptime": time.time() - process.create_time(),
            "cpu_usage": cpu_percent,
            "memory_usage": memory.percent,
            "memory_total": memory.total,
            "memory_used": memory.used,
            "disk_usage": disk.percent,
            "python_version": sys.version,
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat()
        }
        
        return JSONResponse(content=status)
        
    except Exception as e:
        api_logger.error(f"獲取系統狀態失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取系統狀態失敗: {str(e)}")

@router.get("/video-file")
async def get_video_file(path: str):
    """讀取伺服器上的影片檔案"""
    try:
        import os
        from fastapi.responses import FileResponse
        
        # 檢查檔案是否存在
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="影片檔案不存在")
        
        # 檢查是否為影片檔案
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv']
        if not any(path.lower().endswith(ext) for ext in video_extensions):
            raise HTTPException(status_code=400, detail="不是有效的影片檔案")
        
        return FileResponse(
            path=path,
            media_type='video/mp4',
            filename=os.path.basename(path)
        )
        
    except Exception as e:
        api_logger.error(f"讀取影片檔案失敗: {e}")
        raise HTTPException(status_code=500, detail=f"讀取影片檔案失敗: {str(e)}")

# ===== 影片列表相關 =====

class VideoFileInfo(BaseModel):
    """影片檔案資訊"""
    id: str
    name: str
    file_path: str
    upload_time: str
    size: str
    duration: Optional[str] = None
    resolution: Optional[str] = None
    status: str  # 'ready', 'analyzing', 'completed'

@router.get("/video-list")
async def get_video_list():
    """
    獲取上傳影片資料夾中的影片列表
    """
    try:
        videos_dir_path = find_videos_directory()
        videos_dir = str(videos_dir_path) if videos_dir_path else "uploads/videos"
        
        if not os.path.exists(videos_dir):
            return JSONResponse(content={"videos": []})
        
        video_list = []
        supported_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm']
        
        for filename in os.listdir(videos_dir):
            if any(filename.lower().endswith(ext) for ext in supported_extensions):
                file_path = os.path.join(videos_dir, filename)
                
                if os.path.isfile(file_path):
                    # 獲取檔案資訊
                    stat_info = os.stat(file_path)
                    file_size = stat_info.st_size
                    upload_time = datetime.fromtimestamp(stat_info.st_mtime)
                    
                    # 格式化檔案大小
                    if file_size < 1024 * 1024:
                        size_str = f"{file_size / 1024:.1f}KB"
                    elif file_size < 1024 * 1024 * 1024:
                        size_str = f"{file_size / (1024 * 1024):.1f}MB"
                    else:
                        size_str = f"{file_size / (1024 * 1024 * 1024):.1f}GB"
                    
                    # 嘗試獲取影片資訊
                    duration = None
                    resolution = None
                    try:
                        import cv2
                        cap = cv2.VideoCapture(file_path)
                        if cap.isOpened():
                            # 獲取影片時長
                            fps = cap.get(cv2.CAP_PROP_FPS)
                            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                            if fps > 0:
                                duration_seconds = frame_count / fps
                                minutes = int(duration_seconds // 60)
                                seconds = int(duration_seconds % 60)
                                duration = f"{minutes}:{seconds:02d}"
                            
                            # 獲取解析度
                            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            if width > 0 and height > 0:
                                resolution = f"{width}x{height}"
                        
                        cap.release()
                    except:
                        # 如果無法讀取影片資訊，使用預設值
                        duration = "未知"
                        resolution = "未知"
                    
                    # 預設狀態為 ready
                    status = "ready"
                    
                    video_info = VideoFileInfo(
                        id=filename,  # 使用檔名作為 ID
                        name=filename,
                        file_path=file_path,
                        upload_time=upload_time.strftime("%Y-%m-%d %H:%M:%S"),
                        size=size_str,
                        duration=duration,
                        resolution=resolution,
                        status=status
                    )
                    
                    video_list.append(video_info.dict())
        
        # 按上傳時間降序排列
        video_list.sort(key=lambda x: x['upload_time'], reverse=True)
        
        return JSONResponse(content={
            "videos": video_list,
            "total": len(video_list)
        })
        
    except Exception as e:
        api_logger.error(f"獲取影片列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取影片列表失敗: {str(e)}")


@router.get("/videos")
async def get_videos_simple():
    """
    簡化版獲取影片列表 - 用於測試
    """
    try:
        videos_dir_path = find_videos_directory()
        videos_dir = str(videos_dir_path) if videos_dir_path else "uploads/videos"
        
        if not os.path.exists(videos_dir):
            return {"videos": [], "total": 0}
        
        video_list = []
        supported_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm']
        
        for filename in os.listdir(videos_dir):
            if any(filename.lower().endswith(ext) for ext in supported_extensions):
                file_path = os.path.join(videos_dir, filename)
                
                if os.path.isfile(file_path):
                    # 獲取檔案基本資訊
                    stat_info = os.stat(file_path)
                    file_size = stat_info.st_size
                    upload_time = datetime.fromtimestamp(stat_info.st_mtime)
                    
                    # 格式化檔案大小
                    if file_size < 1024 * 1024:
                        size_str = f"{file_size / 1024:.1f}KB"
                    elif file_size < 1024 * 1024 * 1024:
                        size_str = f"{file_size / (1024 * 1024):.1f}MB"
                    else:
                        size_str = f"{file_size / (1024 * 1024 * 1024):.1f}GB"
                    
                    video_info = {
                        "id": filename,
                        "name": filename,
                        "file_path": file_path,
                        "upload_time": upload_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "size": size_str,
                        "duration": "2:30",  # 預設值
                        "resolution": "1920x1080",  # 預設值  
                        "status": "ready"
                    }
                    
                    video_list.append(video_info)
        
        # 按上傳時間降序排列
        video_list.sort(key=lambda x: x['upload_time'], reverse=True)
        
        return {
            "videos": video_list,
            "total": len(video_list)
        }
        
    except Exception as e:
        api_logger.error(f"獲取影片列表失敗: {e}")
        raise HTTPException(status_code=500, detail=f"獲取影片列表失敗: {str(e)}")


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str):
    """
    刪除影片檔案
    """
    try:
        videos_dir_path = find_videos_directory()
        videos_dir = str(videos_dir_path) if videos_dir_path else "uploads/videos"
        video_path = os.path.join(videos_dir, video_id)
        
        # 檢查檔案是否存在
        if not os.path.exists(video_path):
            raise HTTPException(status_code=404, detail="影片檔案不存在")
        
        # 檢查是否為檔案
        if not os.path.isfile(video_path):
            raise HTTPException(status_code=400, detail="指定的路徑不是檔案")
        
        # 檢查檔案副檔名是否為支援的影片格式
        supported_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm']
        if not any(video_id.lower().endswith(ext) for ext in supported_extensions):
            raise HTTPException(status_code=400, detail="不是有效的影片檔案")
        
        # 刪除檔案
        os.remove(video_path)
        
        api_logger.info(f"成功刪除影片檔案: {video_id}")
        
        return {
            "success": True,
            "message": f"成功刪除影片: {video_id}",
            "deleted_file": video_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"刪除影片失敗: {e}")
        raise HTTPException(status_code=500, detail=f"刪除影片失敗: {str(e)}")

# ===== 即時檢測背景任務 =====

async def run_realtime_detection(
    task_id: int,
    camera_info: Dict[str, Any],
    model_info: Dict[str, Any],
    request: RealtimeAnalysisRequest,
    db_session_factory
):
    """執行即時檢測的背景任務 - 使用共享視訊流"""
    from app.services.realtime_detection_service import realtime_detection_service
    from app.services.new_database_service import DatabaseService
    
    api_logger.info(f"開始執行即時檢測任務: {task_id}")
    
    async def update_task_status(status: str, error_message: str = None):
        """更新任務狀態"""
        try:
            async with db_session_factory() as db:
                query = select(AnalysisTask).where(AnalysisTask.id == task_id)
                result = await db.execute(query)
                task = result.scalar_one_or_none()
                
                if task:
                    task.status = status
                    if status == "running" and not task.start_time:
                        task.start_time = datetime.now()
                    elif status in ["completed", "failed"]:
                        task.end_time = datetime.now()
                        
                    await db.commit()
                    api_logger.info(f"任務 {task_id} 狀態更新為: {status}")
        except Exception as e:
            api_logger.error(f"更新任務狀態失敗: {e}")
    
    try:
        # 1. 更新任務狀態為執行中
        await update_task_status("running")
        
        # 2. 準備參數
        camera_id = str(camera_info.get("id", "camera_0"))
        device_index = camera_info.get("device_index", 0)
        
        # 3. 建立資料庫服務實例
        db_service = DatabaseService()
        
        # 4. 使用共享視訊流或客戶端影像開始實時檢測
        if request.client_stream:
            api_logger.info(f"啟動實時檢測 (客戶端影像上傳): {camera_id}")
        else:
            api_logger.info(f"使用共享視訊流啟動實時檢測: {camera_id}, 裝置索引: {device_index}")
        
        success = await realtime_detection_service.start_realtime_detection(
            task_id=str(task_id),
            camera_id=camera_id,
            device_index=device_index,
            db_service=db_service,
            confidence_threshold=request.confidence,
            iou_threshold=request.iou_threshold,
            model_path=model_info.get("path"),
            external_source=request.client_stream,
        )

        if success:
            api_logger.info(f"即時檢測任務 {task_id} 啟動成功")
        else:
            raise Exception("實時檢測服務啟動失敗")
            
    except Exception as e:
        api_logger.error(f"即時檢測任務失敗: {e}")
        await update_task_status("failed", str(e))


@router.post(
    "/analysis/live-person-camera/{task_id}/preview",
    response_model=PreviewLaunchResponse,
)
async def launch_live_person_preview_gui(
    task_id: int,
    request: PreviewLaunchRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """啟動 PySide6 GUI 預覽視窗"""
    try:
        # 取得任務資訊
        query = select(AnalysisTask).where(AnalysisTask.id == int(task_id))
        result = await db.execute(query)
        task = result.scalar_one_or_none()

        if not task:
            raise HTTPException(status_code=404, detail="任務不存在")
        if task.task_type != "realtime_camera":
            raise HTTPException(status_code=400, detail="僅支援即時攝影機任務")
        if task.status not in {"running", "pending"}:
            raise HTTPException(status_code=400, detail=f"任務狀態為 {task.status}，無法開啟預覽")

        source_info = task.source_info or {}
        payload = request or PreviewLaunchRequest()

        if payload.source_override:
            source_value = payload.source_override
        else:
            source_value = None
            device_index = source_info.get("device_index")
            rtsp_url = (
                source_info.get("rtsp_url")
                or source_info.get("camera_url")
                or source_info.get("stream_url")
            )
            if device_index is not None:
                source_value = str(device_index)
            elif rtsp_url:
                source_value = str(rtsp_url)
            elif source_info.get("camera_type") == "USB":
                source_value = str(source_info.get("camera_id"))

        if not source_value and source_info.get("camera_id"):
            camera_service = CameraService()
            camera = await camera_service.get_camera_by_id(str(source_info["camera_id"]))
            if camera:
                if getattr(camera, "device_index", None) is not None:
                    source_value = str(camera.device_index)
                elif getattr(camera, "rtsp_url", None):
                    source_value = camera.rtsp_url

        if not source_value:
            raise HTTPException(status_code=400, detail="找不到可用的影像來源，請先確認攝影機設定")

        model_path = payload.model_override or source_info.get("model_path")
        if model_path and not os.path.exists(model_path):
            api_logger.warning(f"指定的模型檔不存在，改用預設值: {model_path}")
            model_path = None

        imgsz_value = payload.imgsz or source_info.get("imgsz")
        if imgsz_value is not None:
            try:
                imgsz_value = int(imgsz_value)
            except (TypeError, ValueError):
                imgsz_value = None
        confidence_value = (
            payload.confidence
            if payload.confidence is not None
            else task.confidence_threshold
        )
        window_name = (
            task.task_name
            or source_info.get("camera_name")
            or f"Live Task {task_id}"
        )

        if payload.alert_rules is not None:
            alert_rules = payload.alert_rules
            alert_rules_path = save_alert_runtime_rules(task.id, alert_rules)
        else:
            alert_rules = load_alert_runtime_rules(task.id)
            alert_rules_path = ensure_alert_runtime_file(task.id)
        fall_alert_enabled = _has_fall_detection_rule(alert_rules)
        try:
            detection_status = realtime_gui_manager.start_detection(
                task_id=str(task.id),
                source=source_value,
                model_path=model_path,
                window_name=window_name,
                confidence=confidence_value,
                imgsz=imgsz_value,
                device=payload.device or source_info.get("device"),
                start_hidden=False,
                fall_alert_enabled=fall_alert_enabled,
                alert_rules_path=str(alert_rules_path),
            )
        except Exception as exc:
            api_logger.error(f"啟動/確認偵測子行程失敗: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

        try:
            show_result = realtime_gui_manager.show_window(str(task.id))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        already_running = detection_status.get("already_running", False)
        message = (
            f"{window_name} 的 GUI 預覽已在執行 (PID {show_result['pid']})"
            if already_running
            else f"{window_name} 的 GUI 預覽已啟動 (PID {show_result['pid']})"
        )

        if fall_alert_enabled:
            _update_fall_detection_monitor(task, True, confidence=confidence_value)
        else:
            fall_detection_service.stop_monitoring(str(task.id))

        return PreviewLaunchResponse(
            task_id=task.id,
            pid=int(show_result["pid"]),
            already_running=already_running,
            message=message,
            log_path=show_result.get("log_path"),
        )
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        api_logger.error(f"GUI 腳本不存在: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        api_logger.error(f"啟動 GUI 預覽失敗: {exc}")
        raise HTTPException(status_code=500, detail=f"啟動 GUI 預覽失敗: {exc}")


@router.delete("/analysis/live-person-camera/{task_id}")
async def stop_live_person_camera(task_id: int, db: AsyncSession = Depends(get_db)):
    """停止指定即時偵測任務與其 PySide6 子行程"""
    try:
        stopped = realtime_gui_manager.stop_process(str(task_id))
        if not stopped:
            raise HTTPException(status_code=404, detail="找不到對應的偵測行程")

        fall_detection_service.stop_monitoring(str(task_id))

        await db.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == int(task_id))
            .values(status="stopped", end_time=datetime.now())
        )
        await db.commit()

        return {
            "task_id": task_id,
            "status": "stopped",
            "message": "偵測任務與 GUI 子行程已停止",
        }
    except HTTPException:
        raise
    except Exception as exc:
        api_logger.error(f"停止偵測任務失敗: {exc}")
        raise HTTPException(status_code=500, detail=f"停止偵測失敗: {exc}")



