"""
資料庫服務 - 向後相容適配器
支援即時攝影機分析和影片檔案分析兩種模式
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete, func, and_, or_
from sqlalchemy.orm import selectinload

from app.models.database import AnalysisTask, DetectionResult, DataSource, SystemConfig
from app.services.new_database_service import DatabaseService as NewDatabaseService
import logging

logger = logging.getLogger(__name__)

# 為了向後相容性，創建別名
AnalysisRecord = AnalysisTask

class DatabaseService:
    """資料庫服務類別 - 提供所有資料庫操作的封裝"""
    
    def __init__(self, db_session: AsyncSession = None):
        self.db = db_session
    
    async def create_analysis_record(
        self, 
        video_path: str,
        video_name: str,
        analysis_type: str = "detection",
        **kwargs
    ) -> AnalysisTask:
        """創建分析記錄 - 使用新的 AnalysisTask 模型"""
        try:
            analysis_data = {
                "video_path": video_path,
                "video_name": video_name,
                "analysis_type": analysis_type,
                "status": "processing",
                **kwargs
            }
            
            analysis_record = AnalysisRecord(**analysis_data)
            self.db.add(analysis_record)
            await self.db.commit()
            await self.db.refresh(analysis_record)
            
            logger.info(f"✅ 創建分析記錄: {analysis_record.id}")
            return analysis_record
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"❌ 創建分析記錄失敗: {e}")
            raise
    
    async def update_analysis_record(
        self, 
        analysis_id: int, 
        **update_data
    ) -> Optional[AnalysisRecord]:
        """更新分析記錄"""
        try:
            query = select(AnalysisRecord).where(AnalysisRecord.id == analysis_id)
            result = await self.db.execute(query)
            analysis_record = result.scalar_one_or_none()
            
            if not analysis_record:
                logger.warning(f"⚠️ 找不到分析記錄: {analysis_id}")
                return None
            
            for key, value in update_data.items():
                if hasattr(analysis_record, key):
                    setattr(analysis_record, key, value)
            
            analysis_record.updated_at = datetime.utcnow()
            await self.db.commit()
            await self.db.refresh(analysis_record)
            
            logger.info(f"✅ 更新分析記錄: {analysis_id}")
            return analysis_record
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"❌ 更新分析記錄失敗: {e}")
            raise
    
    async def save_detection_results(
        self,
        analysis_id: int,
        detections: List[Dict[str, Any]]
    ) -> int:
        """批量保存檢測結果"""
        try:
            detection_records = []
            
            for detection in detections:
                detection_data = {
                    "analysis_id": analysis_id,
                    "frame_timestamp": datetime.now(timezone.utc),
                    **detection
                }
                detection_records.append(DetectionResult(**detection_data))
            
            self.db.add_all(detection_records)
            await self.db.commit()
            
            logger.info(f"✅ 保存 {len(detection_records)} 個檢測結果")
            return len(detection_records)
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"❌ 保存檢測結果失敗: {e}")
            raise
    
    # async def save_behavior_event(
    #     self,
    #     analysis_id: int,
    #     event_type: str,
    #     **event_data
    # ) -> BehaviorEvent:
    #     """保存行為事件"""
    #     try:
    #         event_record = BehaviorEvent(
    #             analysis_id=analysis_id,
    #             timestamp=datetime.utcnow(),
    #             event_type=event_type,
    #             **event_data
    #         )
    #         
    #         self.db.add(event_record)
    #         await self.db.commit()
    #         await self.db.refresh(event_record)
    #         
    #         logger.info(f"✅ 保存行為事件: {event_type}")
    #         return event_record
    #         
    #     except Exception as e:
    #         await self.db.rollback()
    #         logger.error(f"❌ 保存行為事件失敗: {e}")
            raise
    
    async def get_analysis_record(self, analysis_id: int) -> Optional[AnalysisRecord]:
        """獲取分析記錄"""
        try:
            query = select(AnalysisRecord).where(AnalysisRecord.id == analysis_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
            
        except Exception as e:
            logger.error(f"❌ 獲取分析記錄失敗: {e}")
            return None
    
    async def get_analysis_records(
        self, 
        skip: int = 0, 
        limit: int = 100,
        status: Optional[str] = None
    ) -> List[AnalysisRecord]:
        """獲取分析記錄列表"""
        try:
            query = select(AnalysisRecord)
            
            if status:
                query = query.where(AnalysisRecord.status == status)
            
            query = query.offset(skip).limit(limit).order_by(AnalysisRecord.created_at.desc())
            result = await self.db.execute(query)
            return list(result.scalars().all())
            
        except Exception as e:
            logger.error(f"❌ 獲取分析記錄列表失敗: {e}")
            return []
    
    async def get_detection_results(
        self, 
        analysis_id: int,
        skip: int = 0,
        limit: int = 1000
    ) -> List[DetectionResult]:
        """獲取檢測結果"""
        try:
            query = select(DetectionResult).where(
                DetectionResult.analysis_id == analysis_id
            ).offset(skip).limit(limit).order_by(DetectionResult.frame_number)
            
            result = await self.db.execute(query)
            return list(result.scalars().all())
            
        except Exception as e:
            logger.error(f"❌ 獲取檢測結果失敗: {e}")
            return []
    
    # async def get_behavior_events(
    #     self,
    #     analysis_id: int,
    #     event_type: Optional[str] = None
    # ) -> List[BehaviorEvent]:
    #     """獲取行為事件"""
    #     try:
    #         query = select(BehaviorEvent).where(
    #             BehaviorEvent.analysis_id == analysis_id
    #         )
    #         
    #         if event_type:
    #             query = query.where(BehaviorEvent.event_type == event_type)
    #         
    #         query = query.order_by(BehaviorEvent.timestamp)
    #         result = await self.db.execute(query)
    #         return list(result.scalars().all())
    #         
    #     except Exception as e:
    #         logger.error(f"❌ 獲取行為事件失敗: {e}")
    #         return []
    
    async def get_analysis_statistics(self) -> Dict[str, Any]:
        """獲取分析統計資訊"""
        try:
            # 總分析數
            total_analyses = await self.db.execute(
                select(func.count(AnalysisRecord.id))
            )
            total_count = total_analyses.scalar()
            
            # 各狀態分析數
            status_query = await self.db.execute(
                select(AnalysisRecord.status, func.count(AnalysisRecord.id))
                .group_by(AnalysisRecord.status)
            )
            status_counts = dict(status_query.fetchall())
            
            # 總檢測數
            total_detections = await self.db.execute(
                select(func.count(DetectionResult.id))
            )
            detection_count = total_detections.scalar()
            
            # 總事件數 (暫時註解掉，因為 BehaviorEvent 模型不存在)
            # total_events = await self.db.execute(
            #     select(func.count(BehaviorEvent.id))
            # )
            # event_count = total_events.scalar()
            
            return {
                "total_analyses": total_count or 0,
                "status_breakdown": status_counts,
                "total_detections": detection_count or 0,
                "total_events": 0,  # event_count or 0,
                "last_updated": datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(f"❌ 獲取統計資訊失敗: {e}")
            return {}

# 依賴注入函數
def get_database_service(db: AsyncSession) -> DatabaseService:
    """獲取資料庫服務實例"""
    return DatabaseService(db)
