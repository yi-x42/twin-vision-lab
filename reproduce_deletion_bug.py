
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.services.camera_service import CameraService, Camera
from app.models.database import DataSource

class TestCameraDeletion(unittest.TestCase):
    def setUp(self):
        # Reset singleton
        CameraService._instance = None
        CameraService._initialized = False
        
        # Mock database session
        self.mock_db_session = MagicMock()
        self.mock_db = MagicMock()
        self.mock_db_session.__enter__.return_value = self.mock_db
        
        # Patch SyncSessionLocal
        self.patcher = patch('app.services.camera_service.SyncSessionLocal', return_value=self.mock_db_session)
        self.mock_session_local = self.patcher.start()
        
    def tearDown(self):
        self.patcher.stop()

    def test_remove_camera_correct_id(self):
        # Setup
        service = CameraService()
        
        # Add two cameras to memory
        cam1 = Camera(id="1", name="Cam 1", status="online", camera_type="USB", resolution="1080p", fps=30)
        cam2 = Camera(id="2", name="Cam 2", status="online", camera_type="USB", resolution="1080p", fps=30)
        service.cameras = {"1": cam1, "2": cam2}
        
        # Mock DB query result
        mock_source = MagicMock()
        mock_source.id = 1
        self.mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source
        
        # Execute
        import asyncio
        asyncio.run(service.remove_camera("1"))
        
        # Verify
        # 1. Check memory
        self.assertNotIn("1", service.cameras)
        self.assertIn("2", service.cameras)
        
        # 2. Check DB deletion
        self.mock_db.delete.assert_called_once_with(mock_source)
        
        # 3. Check filter arguments
        # We need to verify that filter_by was called with id=1 (int)
        self.mock_db.query.return_value.filter_by.assert_called_with(id=1, source_type='camera')

    def test_remove_camera_wrong_id_type(self):
        # Test what happens if we pass a non-integer string ID that exists in memory
        service = CameraService()
        
        # Add camera with non-int ID (e.g. from default init fallback)
        cam_default = Camera(id="cam_001", name="Default Cam", status="online", camera_type="USB", resolution="1080p", fps=30)
        service.cameras = {"cam_001": cam_default}
        
        # Execute
        import asyncio
        try:
            asyncio.run(service.remove_camera("cam_001"))
        except Exception as e:
            print(f"Caught expected exception: {e}")
            
        # Verify
        # It should fail at int("cam_001")
        # Memory should NOT be touched if DB fails (transactional logic?)
        # Actually the code does DB first, then memory.
        # If int() fails, it raises ValueError, so DB query is not executed, and memory is not touched.
        self.assertIn("cam_001", service.cameras)

if __name__ == '__main__':
    unittest.main()
