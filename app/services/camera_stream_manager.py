"""
攝影機流管理器 - 解決攝影機資源衝突問題
實現共享視訊流，讓多個功能（即時分析、預覽等）可以同時使用同一個攝影機
"""

import asyncio
import cv2
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Callable, Optional, Tuple, Any, Set
from dataclasses import dataclass
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import weakref
from enum import Enum

from app.core.logger import detection_logger


class StreamStatus(Enum):
    """流狀態枚舉"""
    STOPPED = "stopped"
    STARTING = "starting" 
    RUNNING = "running"
    ERROR = "error"


@dataclass
class FrameData:
    """影像框架數據"""
    frame: np.ndarray
    timestamp: datetime
    frame_number: int
    camera_id: str


class StreamConsumer:
    """流消費者基類"""
    
    def __init__(self, consumer_id: str, callback: Callable[[FrameData], None]):
        self.consumer_id = consumer_id
        self.callback = callback
        self.active = True
        self.last_frame_time = None
    
    def consume_frame(self, frame_data: FrameData):
        """消費影像框架"""
        if self.active:
            try:
                self.callback(frame_data)
                self.last_frame_time = datetime.now()
            except Exception as e:
                detection_logger.error(f"消費者 {self.consumer_id} 處理影像失敗: {e}")
    
    def stop(self):
        """停止消費"""
        self.active = False


class CameraStream:
    """單個攝影機的流管理"""
    
    def __init__(self, camera_id: str, device_index: int):
        self.camera_id = camera_id
        self.device_index = device_index
        self.status = StreamStatus.STOPPED
        self.cap: Optional[cv2.VideoCapture] = None
        self.thread: Optional[threading.Thread] = None
        self.consumers: Dict[str, StreamConsumer] = {}
        self.current_frame: Optional[FrameData] = None
        self.frame_count = 0
        self.fps = 30.0
        self.resolution = (640, 480)
        self.last_error: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        
    def start(self) -> bool:
        """啟動攝影機流"""
        if self.status == StreamStatus.RUNNING:
            detection_logger.warning(f"攝影機 {self.camera_id} 已在運行中")
            return True
            
        self.status = StreamStatus.STARTING
        detection_logger.info(f"啟動攝影機流: {self.camera_id} (設備索引: {self.device_index})")
        
        try:
            # 優先使用 DirectShow 後端，更穩定
            detection_logger.info(f"嘗試使用 DirectShow 後端開啟攝影機 {self.device_index}")
            self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            
            if not self.cap.isOpened():
                # 如果 DirectShow 失敗，嘗試預設後端
                detection_logger.warning(f"DirectShow 後端失敗，嘗試預設後端")
                self.cap = cv2.VideoCapture(self.device_index)
                
            if not self.cap.isOpened():
                raise Exception(f"無法開啟攝影機設備 {self.device_index}")
            
            # 設置攝影機參數
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            
            # 獲取實際參數
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            
            self.resolution = (actual_width, actual_height)
            self.fps = actual_fps if actual_fps > 0 else 30.0
            
            detection_logger.info(f"攝影機初始化成功: {actual_width}x{actual_height}@{self.fps}fps")
            
            # 啟動讀取線程
            self._stop_event.clear()
            self.thread = threading.Thread(target=self._stream_loop, daemon=True)
            self.thread.start()
            
            self.status = StreamStatus.RUNNING
            self.start_time = datetime.now()
            return True
            
        except Exception as e:
            self.last_error = str(e)
            self.status = StreamStatus.ERROR
            detection_logger.error(f"啟動攝影機流失敗: {e}")
            self._cleanup()
            return False
    
    def stop(self):
        """停止攝影機流"""
        detection_logger.info(f"停止攝影機流: {self.camera_id}")
        
        self.status = StreamStatus.STOPPED
        self._stop_event.set()
        
        # 等待線程結束
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        # 停止所有消費者
        with self._lock:
            for consumer in self.consumers.values():
                consumer.stop()
            self.consumers.clear()
        
        self._cleanup()
    
    def _reinitialize_camera(self):
        """重新初始化攝影機"""
        try:
            detection_logger.info(f"重新初始化攝影機: {self.camera_id}")
            
            # 釋放現有攝影機
            if self.cap:
                self.cap.release()
                time.sleep(0.5)  # 等待資源釋放
            
            # 重新初始化攝影機 - 優先使用 DirectShow
            detection_logger.info(f"使用 DirectShow 後端重新初始化攝影機 {self.device_index}")
            self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            
            if not self.cap.isOpened():
                # 嘗試預設後端
                detection_logger.warning(f"DirectShow 失敗，嘗試預設後端")
                self.cap = cv2.VideoCapture(self.device_index)
                
            if self.cap.isOpened():
                # 重新設置參數
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap.set(cv2.CAP_PROP_FPS, 30)
                detection_logger.info(f"攝影機 {self.camera_id} 重新初始化成功")
            else:
                detection_logger.error(f"攝影機 {self.camera_id} 重新初始化失敗")
                
        except Exception as e:
            detection_logger.error(f"攝影機重新初始化失敗: {e}")
    
    def _cleanup(self):
        """清理資源"""
        if self.cap:
            self.cap.release()
            self.cap = None
        self.thread = None
        self.current_frame = None
    
    def _stream_loop(self):
        """主要的流讀取循環"""
        detection_logger.info(f"攝影機流循環開始: {self.camera_id}")
        consecutive_failures = 0
        max_consecutive_failures = 10
        
        while not self._stop_event.is_set() and self.cap and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
                if not ret:
                    consecutive_failures += 1
                    
                    # 只有在連續失敗較多次時才記錄警告，減少正常掃描時的噪音
                    if consecutive_failures == 1:
                        detection_logger.debug(f"攝影機 {self.camera_id} 無法讀取影像 (嘗試: {consecutive_failures})")
                    elif consecutive_failures <= 3:
                        detection_logger.info(f"攝影機 {self.camera_id} 無法讀取影像 (連續失敗: {consecutive_failures})")
                    else:
                        detection_logger.warning(f"攝影機 {self.camera_id} 無法讀取影像 (連續失敗: {consecutive_failures})")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        detection_logger.error(f"攝影機 {self.camera_id} 連續失敗次數過多，嘗試重新初始化")
                        self._reinitialize_camera()
                        consecutive_failures = 0
                    
                    time.sleep(0.1)
                    continue
                
                # 重置失敗計數
                consecutive_failures = 0
                self.frame_count += 1
                
                # 創建框架數據
                frame_data = FrameData(
                    frame=frame.copy(),
                    timestamp=datetime.now(timezone.utc),
                    frame_number=self.frame_count,
                    camera_id=self.camera_id
                )
                
                # 更新當前框架（線程安全）
                with self._lock:
                    self.current_frame = frame_data
                
                # 分發給所有消費者
                with self._lock:
                    active_consumers = []
                    for consumer in self.consumers.values():
                        if consumer.active:
                            try:
                                consumer.consume_frame(frame_data)
                                active_consumers.append(consumer)
                            except Exception as e:
                                detection_logger.error(f"消費者 {consumer.consumer_id} 處理幀失敗: {e}")
                                consumer.active = False  # 標記為非活躍
                        else:
                            detection_logger.debug(f"移除非活躍消費者: {consumer.consumer_id}")
                    
                    # 清理非活躍消費者
                    self.consumers = {cid: c for cid, c in self.consumers.items() if c.active}
                
                # 控制幀率
                time.sleep(1.0 / self.fps)
                
            except cv2.error as e:
                detection_logger.error(f"OpenCV 錯誤: {e}")
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    detection_logger.error(f"OpenCV 錯誤過多，嘗試重新初始化攝影機")
                    self._reinitialize_camera()
                    consecutive_failures = 0
                time.sleep(0.5)
            except RuntimeError as e:
                detection_logger.error(f"運行時錯誤: {e}")
                if "Unknown C++ exception" in str(e):
                    detection_logger.error("檢測到 OpenCV C++ 異常，嘗試重新初始化攝影機")
                    self._reinitialize_camera()
                    consecutive_failures = 0
                time.sleep(1.0)
            except Exception as e:
                detection_logger.error(f"流讀取錯誤: {e}")
                self.last_error = str(e)
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    detection_logger.error(f"一般錯誤過多，嘗試重新初始化攝影機")
                    self._reinitialize_camera()
                    consecutive_failures = 0
                time.sleep(0.5)
        
        detection_logger.info(f"攝影機流循環結束: {self.camera_id}")
    
    def get_latest_frame(self) -> Optional[FrameData]:
        """獲取最新的幀數據"""
        with self._lock:
            return self.current_frame
    
    def add_consumer(self, consumer: StreamConsumer) -> bool:
        """添加流消費者"""
        with self._lock:
            if consumer.consumer_id in self.consumers:
                detection_logger.warning(f"消費者 {consumer.consumer_id} 已存在")
                return False
            
            self.consumers[consumer.consumer_id] = consumer
            detection_logger.info(f"新增消費者: {consumer.consumer_id} 到攝影機 {self.camera_id}")
            
            # 如果有當前影像，立即發送
            if self.current_frame and consumer.active:
                consumer.consume_frame(self.current_frame)
            
            return True
    
    def remove_consumer(self, consumer_id: str) -> bool:
        """移除流消費者"""
        with self._lock:
            if consumer_id in self.consumers:
                consumer = self.consumers.pop(consumer_id)
                consumer.stop()
                detection_logger.info(f"移除消費者: {consumer_id} 從攝影機 {self.camera_id}")
                return True
            return False

    def consumer_count(self) -> int:
        """目前活躍消費者數量"""
        with self._lock:
            return len(self.consumers)
    
    def get_stats(self) -> Dict[str, Any]:
        """獲取流統計信息"""
        return {
            "camera_id": self.camera_id,
            "device_index": self.device_index,
            "status": self.status.value,
            "frame_count": self.frame_count,
            "fps": self.fps,
            "resolution": self.resolution,
            "consumers": list(self.consumers.keys()),
            "consumer_count": len(self.consumers),
            "last_error": self.last_error,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "uptime_seconds": (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        }


class CameraStreamManager:
    """攝影機流管理器 - 管理多個攝影機流"""
    
    _instance: Optional['CameraStreamManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.streams: Dict[str, CameraStream] = {}
        self.camera_aliases: Dict[str, str] = {}
        self.device_to_stream: Dict[int, str] = {}
        self.stream_cameras: Dict[str, Set[str]] = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="CameraStreamManager")
        self._initialized = True
        
        detection_logger.info("攝影機流管理器初始化完成")
    
    def _resolve_camera_id(self, camera_id: str) -> str:
        return self.camera_aliases.get(camera_id, camera_id)
    
    def start_stream(self, camera_id: str, device_index: int) -> bool:
        """啟動指定攝影機的流"""
        canonical_id = self.device_to_stream.get(device_index)
        if canonical_id and canonical_id in self.streams:
            self.camera_aliases[camera_id] = canonical_id
            self.stream_cameras.setdefault(canonical_id, set()).add(camera_id)
            detection_logger.info(
                f"攝影機 {camera_id} 共用既有流 {canonical_id} (device_index={device_index})"
            )
            return True

        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id in self.streams:
            stream = self.streams[canonical_id]
            if stream.status == StreamStatus.RUNNING:
                self.camera_aliases[camera_id] = canonical_id
                self.stream_cameras.setdefault(canonical_id, set()).add(camera_id)
                detection_logger.info(f"攝影機 {camera_id} 已共用流 {canonical_id}")
                return True
            else:
                stream.stop()
                del self.streams[canonical_id]
                self.stream_cameras.pop(canonical_id, None)

        stream = CameraStream(canonical_id, device_index)
        success = stream.start()
        
        if success:
            self.streams[canonical_id] = stream
            self.camera_aliases[camera_id] = canonical_id
            self.stream_cameras[canonical_id] = {camera_id}
            self.device_to_stream[device_index] = canonical_id
            detection_logger.info(f"攝影機流 {canonical_id} 啟動成功 (device_index={device_index})")
        else:
            detection_logger.error(f"攝影機流 {canonical_id} 啟動失敗")
            
        return success
    
    def stop_stream(self, camera_id: str) -> bool:
        """停止指定攝影機的流"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            detection_logger.warning(f"攝影機流 {camera_id} 不存在")
            return False
        
        refs = self.stream_cameras.get(canonical_id, set())
        if camera_id in refs and len(refs) > 1:
            refs.discard(camera_id)
            self.camera_aliases.pop(camera_id, None)
            detection_logger.info(f"攝影機 {camera_id} 已從共用流 {canonical_id} 移除")
            return True

        stream = self.streams.pop(canonical_id)
        stream.stop()
        alias_set = self.stream_cameras.pop(canonical_id, {camera_id})
        for alias in alias_set:
            self.camera_aliases.pop(alias, None)
        self.device_to_stream.pop(stream.device_index, None)
        detection_logger.info(f"攝影機流 {canonical_id} 已停止")
        return True
    
    def add_consumer(self, camera_id: str, consumer: StreamConsumer) -> bool:
        """為指定攝影機添加消費者"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            detection_logger.error(f"攝影機流 {camera_id} 不存在")
            return False
        
        return self.streams[canonical_id].add_consumer(consumer)
    
    def remove_consumer(self, camera_id: str, consumer_id: str) -> bool:
        """從指定攝影機移除消費者"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            return False
        
        stream = self.streams[canonical_id]
        removed = stream.remove_consumer(consumer_id)
        if removed and stream.consumer_count() == 0:
            detection_logger.info(f"攝影機 {canonical_id} 已無消費者，釋放攝影機資源")
            self.stop_stream(canonical_id)
        return removed
    
    def get_stream_stats(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """獲取指定攝影機流的統計信息"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            return None
        
        return self.streams[canonical_id].get_stats()
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """獲取所有攝影機流的統計信息"""
        return {cid: stream.get_stats() for cid, stream in self.streams.items()}
    
    def stop_all_streams(self):
        """停止所有攝影機流"""
        detection_logger.info("停止所有攝影機流")
        for camera_id in list(self.streams.keys()):
            stream = self.streams.pop(camera_id)
            stream.stop()
        self.camera_aliases.clear()
        self.device_to_stream.clear()
        self.stream_cameras.clear()
    
    def get_latest_frame(self, camera_id: str) -> Optional[FrameData]:
        """獲取攝影機的最新幀數據"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            return None
        return self.streams[canonical_id].get_latest_frame()
    
    def is_stream_running(self, camera_id: str) -> bool:
        """檢查指定攝影機流是否在運行"""
        canonical_id = self._resolve_camera_id(camera_id)
        if canonical_id not in self.streams:
            return False
        return self.streams[canonical_id].status == StreamStatus.RUNNING
    
    def get_camera_resolution(self, device_index: int) -> Optional[Dict[str, Any]]:
        """
        獲取攝影機解析度資訊
        優先使用現有流的資訊，如果沒有則暫時開啟獲取後立即關閉
        """
        try:
            camera_id = f"camera_{device_index}"
            
            # 如果攝影機流已經在運行，從現有流獲取資訊
            if camera_id in self.streams and self.streams[camera_id].status == StreamStatus.RUNNING:
                stats = self.streams[camera_id].get_stats()
                if stats and 'resolution' in stats:
                    width, height = stats['resolution']
                    detection_logger.info(f"從現有流獲取攝影機 {device_index} 解析度: {width}x{height}")
                    return {
                        "width": width,
                        "height": height,
                        "fps": stats.get('fps', 30.0)
                    }
            
            # 如果沒有現有流，暫時開啟獲取資訊 - 使用 DirectShow 後端
            import cv2
            
            # 優先使用 DirectShow 後端
            cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                # 如果 DirectShow 失敗，嘗試預設後端
                cap = cv2.VideoCapture(device_index)
                
            if cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                cap.release()
                detection_logger.info(f"暫時開啟獲取攝影機 {device_index} 解析度: {width}x{height}@{fps}fps")
                return {
                    "width": width,
                    "height": height,
                    "fps": fps
                }
            else:
                detection_logger.debug(f"攝影機 {device_index} 不可用或已被佔用")
                cap.release()
                return None
        except Exception as e:
            detection_logger.error(f"獲取攝影機 {device_index} 解析度時發生錯誤: {e}")
            return None
    
    def detect_available_cameras(self, max_cameras: int = 5) -> List[Dict[str, Any]]:
        """
        檢測可用攝影機（智能檢測，避免與現有流衝突）
        """
        available_cameras = []
        
        for i in range(max_cameras):
            camera_id = f"camera_{i}"
            
            # 如果攝影機流已經在運行，直接認為可用
            if camera_id in self.streams and self.streams[camera_id].status == StreamStatus.RUNNING:
                stats = self.streams[camera_id].get_stats()
                resolution = stats.get('resolution', (640, 480)) if stats else (640, 480)
                fps = stats.get('fps', 30.0) if stats else 30.0
                
                available_cameras.append({
                    "device_id": i,
                    "name": f"USB Camera {i}",
                    "type": "usb",
                    "resolution": f"{resolution[0]}x{resolution[1]}",
                    "fps": fps,
                    "status": "running"
                })
                detection_logger.info(f"攝影機 {i} 正在運行中，已添加到可用列表")
                continue
            
            # 嘗試獲取解析度來檢測攝影機
            resolution_info = self.get_camera_resolution(i)
            if resolution_info:
                available_cameras.append({
                    "device_id": i,
                    "name": f"USB Camera {i}",
                    "type": "usb",
                    "resolution": f"{resolution_info['width']}x{resolution_info['height']}",
                    "fps": resolution_info['fps'],
                    "status": "available"
                })
                detection_logger.info(f"檢測到可用攝影機 {i}")
        
        return available_cameras


# 全局攝影機流管理器實例
camera_stream_manager = CameraStreamManager()
