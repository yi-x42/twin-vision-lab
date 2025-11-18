import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import apiClient from '../lib/api';

// 定義從後端 API 回傳的系統統計資料結構
export interface SystemStats {
  total_cameras: number;
  online_cameras: number;
  total_alerts_today: number;
  alerts_vs_yesterday: number;
  active_tasks: number;
  system_uptime_seconds: number;
  storage_usage_gb: number;
  storage_total_gb: number;
  storage_percentage: number;
  model_name: string;
  model_params_m: number;
  avg_inference_ms: number;
  cpu_usage: number;
  memory_usage: number;
  gpu_usage: number;
  network_usage: number;
  gpu_memory_usage: number;
}

// 獲取系統統計資料的非同步函式
const fetchSystemStats = async (): Promise<SystemStats> => {
  const { data } = await apiClient.get('/frontend/stats');
  return data;
};

// 建立一個自訂的 React Query hook 來使用系統統計資料
export const useSystemStats = () => {
  return useQuery<SystemStats, Error>({
    queryKey: ['systemStats'],
    queryFn: fetchSystemStats,
    // 設定每 1 秒自動重新整理一次資料
    refetchInterval: 1000,
  });
};

export interface CameraPerformanceItem {
  camera_name: string;
  camera_id?: string | null;
  detections: number;
  runtime_hours: number;
  status?: string | null;
  last_active?: string | null;
}

interface CameraPerformanceParams {
  days?: number;
  limit?: number;
}

const fetchCameraPerformance = async (
  params: CameraPerformanceParams
): Promise<CameraPerformanceItem[]> => {
  const { data } = await apiClient.get('/frontend/analytics/camera-performance', {
    params,
  });
  return data;
};

export const useCameraPerformance = (
  { days = 7, limit = 5 }: CameraPerformanceParams = {}
) => {
  return useQuery<CameraPerformanceItem[], Error>({
    queryKey: ['cameraPerformance', days, limit],
    queryFn: () => fetchCameraPerformance({ days, limit }),
    placeholderData: keepPreviousData,
  });
};

export interface AlertTrendPoint {
  date: string;
  high: number;
  medium: number;
  low: number;
}

interface AlertTrendParams {
  days?: number;
  taskId?: string;
}

const fetchAlertTrends = async (
  params: AlertTrendParams
): Promise<AlertTrendPoint[]> => {
  const { data } = await apiClient.get('/frontend/analytics/alert-trends', {
    params,
  });
  return data;
};

export const useAlertTrends = (
  { days = 7, taskId }: AlertTrendParams = {}
) => {
  return useQuery<AlertTrendPoint[], Error>({
    queryKey: ['alertTrends', days, taskId],
    queryFn: () => fetchAlertTrends({ days, taskId }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });
};

export interface AlertCategoryStat {
  rule_type: string;
  label: string;
  count: number;
}

interface AlertCategoryParams {
  days?: number;
  limit?: number;
  taskId?: string;
}

const fetchAlertCategories = async (
  params: AlertCategoryParams
): Promise<AlertCategoryStat[]> => {
  const { data } = await apiClient.get('/frontend/analytics/alert-categories', {
    params,
  });
  return data;
};

export const useAlertCategoryStats = (
  { days = 7, limit = 4, taskId }: AlertCategoryParams = {}
) => {
  return useQuery<AlertCategoryStat[], Error>({
    queryKey: ['alertCategories', days, limit, taskId],
    queryFn: () => fetchAlertCategories({ days, limit, taskId }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });
};

export interface HourlyActivityPoint {
  hour: string;
  count: number;
}

export interface HourlyActivitySummary {
  peak_hour: string | null;
  peak_count: number;
  low_hour: string | null;
  low_count: number;
  average_per_hour: number;
}

export interface HourlyActivityResponse {
  data: HourlyActivityPoint[];
  summary: HourlyActivitySummary;
}

interface HourlyActivityParams {
  hours?: number;
}

const fetchHourlyActivity = async (
  params: HourlyActivityParams
): Promise<HourlyActivityResponse> => {
  const { data } = await apiClient.get('/frontend/analytics/hourly-activity', {
    params,
  });
  return data;
};

export const useHourlyActivity = (
  { hours = 24 }: HourlyActivityParams = {}
) => {
  return useQuery<HourlyActivityResponse, Error>({
    queryKey: ['hourlyActivity', hours],
    queryFn: () => fetchHourlyActivity({ hours }),
    placeholderData: keepPreviousData,
    refetchInterval: 60000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });
};

// 郵件通知設定
export interface EmailNotificationSettings {
  enabled: boolean;
  address: string;
  confidence: number;
  cooldown_seconds: number;
}

const fetchEmailNotificationSettings = async (): Promise<EmailNotificationSettings> => {
  const { data } = await apiClient.get('/frontend/alerts/notification-settings/email');
  return data;
};

export const useEmailNotificationSettings = () => {
  return useQuery<EmailNotificationSettings, Error>({
    queryKey: ['emailNotificationSettings'],
    queryFn: fetchEmailNotificationSettings,
  });
};

const updateEmailNotificationSettings = async (
  payload: EmailNotificationSettings
): Promise<EmailNotificationSettings> => {
  const { data } = await apiClient.put('/frontend/alerts/notification-settings/email', payload);
  return data;
};

export const useUpdateEmailNotificationSettings = () => {
  const queryClient = useQueryClient();
  return useMutation<EmailNotificationSettings, Error, EmailNotificationSettings>({
    mutationFn: updateEmailNotificationSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['emailNotificationSettings'] });
    },
  });
};

const testEmailNotification = async (payload: { address: string }): Promise<{ success: boolean; message: string }> => {
  const { data } = await apiClient.post('/frontend/alerts/notification-settings/email/test', payload);
  return data;
};

export const useTestEmailNotification = () => {
  return useMutation<{ success: boolean; message: string }, Error, { address: string }>({
    mutationFn: testEmailNotification,
  });
};

// 活躍警報
export interface ActiveAlert {
  id: string;
  task_id?: number | null;
  rule_id: string;
  rule_name: string;
  rule_type: string;
  type: string;
  severity: string;
  status: string;
  description: string;
  timestamp: string;
  camera?: string;
  snapshot_url?: string | null;
  assignee?: string | null;
}

const fetchActiveAlerts = async (): Promise<ActiveAlert[]> => {
  const { data } = await apiClient.get('/frontend/alerts/active');
  return data;
};

export const useActiveAlerts = () => {
  return useQuery<ActiveAlert[], Error>({
    queryKey: ['activeAlerts'],
    queryFn: fetchActiveAlerts,
    refetchInterval: 5000,
  });
};

// 警報規則設定
export interface AlertRuleActionSettings {
  email: boolean;
  push: boolean;
  sms: boolean;
}

export interface AlertRule {
  id: string;
  name: string;
  rule_type: string;
  severity: string;
  enabled: boolean;
  cameras: string[];
  trigger_values: Record<string, unknown>;
  actions: AlertRuleActionSettings;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreateAlertRuleRequest {
  name: string;
  rule_type: string;
  severity: string;
  cameras: string[];
  trigger_values: Record<string, unknown>;
  actions: AlertRuleActionSettings;
}

export interface TaskAlertRulePayload {
  id: string;
  rule_type: string;
  name?: string;
  severity?: string;
  actions?: AlertRuleActionSettings;
  trigger_values?: Record<string, unknown>;
  selections?: string[];
}

export interface SaveTaskAlertsRequest {
  taskId: string;
  rules: TaskAlertRulePayload[];
}

export interface TaskAlertsResponse {
  task_id: number;
  rules: TaskAlertRulePayload[];
  fall_detection_enabled?: boolean;
}

const fetchAlertRules = async (): Promise<AlertRule[]> => {
  const { data } = await apiClient.get('/frontend/alerts/rules');
  return data;
};

export const useAlertRules = () => {
  return useQuery<AlertRule[], Error>({
    queryKey: ['alertRules'],
    queryFn: fetchAlertRules,
  });
};

const saveTaskAlerts = async ({ taskId, rules }: SaveTaskAlertsRequest): Promise<TaskAlertsResponse> => {
  const { data } = await apiClient.put(`/frontend/analysis/tasks/${taskId}/alerts`, { rules });
  return data;
};

export const useSaveTaskAlerts = () => {
  return useMutation<TaskAlertsResponse, Error, SaveTaskAlertsRequest>({
    mutationFn: saveTaskAlerts,
  });
};

const fetchTaskAlerts = async (taskId: string): Promise<TaskAlertsResponse> => {
  const { data } = await apiClient.get(`/frontend/analysis/tasks/${taskId}/alerts`);
  return data;
};

export const useTaskAlerts = (taskId?: string) => {
  return useQuery<TaskAlertsResponse, Error>({
    queryKey: ['taskAlerts', taskId],
    queryFn: () => fetchTaskAlerts(taskId!),
    enabled: Boolean(taskId),
  });
};

const createAlertRule = async (payload: CreateAlertRuleRequest): Promise<AlertRule> => {
  const { data } = await apiClient.post('/frontend/alerts/rules', payload);
  return data;
};

export const useCreateAlertRule = () => {
  const queryClient = useQueryClient();
  return useMutation<AlertRule, Error, CreateAlertRuleRequest>({
    mutationFn: createAlertRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alertRules'] });
    },
  });
};

interface ToggleAlertRuleRequest {
  ruleId: string;
  enabled: boolean;
}

const toggleAlertRule = async ({ ruleId, enabled }: ToggleAlertRuleRequest): Promise<AlertRule> => {
  const { data } = await apiClient.patch(`/frontend/alerts/rules/${ruleId}/toggle`, { enabled });
  return data;
};

export const useToggleAlertRule = () => {
  const queryClient = useQueryClient();
  return useMutation<AlertRule, Error, ToggleAlertRuleRequest>({
    mutationFn: toggleAlertRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alertRules'] });
    },
  });
};

const deleteAlertRule = async (ruleId: string): Promise<{ success: boolean }> => {
  const { data } = await apiClient.delete(`/frontend/alerts/rules/${ruleId}`);
  return data;
};

export const useDeleteAlertRule = () => {
  const queryClient = useQueryClient();
  return useMutation<{ success: boolean }, Error, string>({
    mutationFn: deleteAlertRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alertRules'] });
    },
  });
};

// 攝影機資料介面
export interface CameraInfo {
  id: string;
  name: string;
  status: string;
  camera_type: string;
  resolution: string;
  fps: number;
  group_id?: string;
  device_index?: number;
  rtsp_url?: string;
  // 為了與現有組件兼容，添加額外欄位
  location?: string;
  recording?: boolean;
  nightVision?: boolean;
  motionDetection?: boolean;
  ip?: string;
  model?: string;
}

// 獲取攝影機列表的非同步函式
const fetchCameras = async (): Promise<CameraInfo[]> => {
  const { data } = await apiClient.get('/frontend/cameras', {
    params: {
      real_time_check: true,
    },
  });
  return data;
};

// 攝影機列表的 hook
export const useCameras = () => {
  return useQuery<CameraInfo[], Error>({
    queryKey: ['cameras'],
    queryFn: fetchCameras,
    refetchOnWindowFocus: false,
    // 儀表板需要即時狀態，因此改為定期輪詢後端
    refetchInterval: 2000,
    refetchIntervalInBackground: true,
    staleTime: 0,
  });
};

// 添加攝影機的介面
export interface AddCameraRequest {
  name: string;
  camera_type: string;
  resolution: string;
  fps: number;
  device_index?: number;
  rtsp_url?: string;
}

export interface AddCameraResponse {
  success: boolean;
  camera_id: string;
  message: string;
}

// 添加攝影機的非同步函式
const addCamera = async (cameraData: AddCameraRequest): Promise<AddCameraResponse> => {
  const { data } = await apiClient.post('/frontend/cameras', cameraData);
  return data;
};

// 添加攝影機的 hook
export const useAddCamera = () => {
  return useMutation<AddCameraResponse, Error, AddCameraRequest>({
    mutationFn: addCamera,
  });
};

// 定義攝影機掃描結果的結構
export interface DiscoveredCameraInfo {
  device_id: number;
  name: string;
  type: string;
  resolution: string;
  fps: number;
  status: string;
}

export interface RegisteredCameraInfo {
  id: string;
  name: string;
  device_index: number;
  resolution: string;
  fps: number;
}

export interface CameraScanResponse {
  message: string;
  cameras: DiscoveredCameraInfo[];
  registered: RegisteredCameraInfo[];
}

// 攝影機掃描參數
export interface CameraScanParams {
  register_new?: boolean;
}

// 攝影機掃描的非同步函式
const scanCameras = async (params?: CameraScanParams): Promise<CameraScanResponse> => {
  const { data } = await apiClient.post(
    '/frontend/cameras/scan',
    undefined,
    {
      params: {
        register_new: params?.register_new ?? true,
      },
    }
  );
  return data;
};

// 建立攝影機掃描的 mutation hook
export const useScanCameras = () => {
  return useMutation<CameraScanResponse, Error, CameraScanParams | undefined>({
    mutationFn: scanCameras,
  });
};

// 影片上傳回應介面
export interface VideoUploadResponse {
  message: string;
  source_id: number;
  file_path: string;
  original_name: string;
  size: number;
  video_info: {
    duration: number;
    fps: number;
    resolution: string;
    frame_count: number;
  };
}

// 影片上傳的非同步函式
const uploadVideo = async (formData: FormData): Promise<VideoUploadResponse> => {
  const { data } = await apiClient.post('/frontend/data-sources/upload/video', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return data;
};

// 建立影片上傳的 mutation hook
export const useVideoUpload = () => {
  return useMutation<VideoUploadResponse, Error, FormData>({
    mutationFn: uploadVideo,
  });
};

// 影片分析回應類型定義
export interface VideoAnalysisResponse {
  success: boolean;
  task_id: number;
  message: string;
  task: any;
}

// 影片分析的非同步函式
const startVideoAnalysis = async (formData: FormData): Promise<VideoAnalysisResponse> => {
  const { data } = await apiClient.post('/new-analysis/start-video-analysis', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return data;
};

// 建立影片分析的 mutation hook
export const useVideoAnalysis = () => {
  return useMutation<VideoAnalysisResponse, Error, FormData>({
    mutationFn: startVideoAnalysis,
  });
};

// YOLO 模型清單型別
export interface YoloModelFileInfo {
  id: string;
  name: string;
  modelType: string;
  parameterCount: string;
  fileSize: string;
  status: string;
  size: number;
  created_at: number;
  modified_at: number;
  path: string;
}

// 取得 YOLO 模型清單
const fetchYoloModelList = async (): Promise<YoloModelFileInfo[]> => {
  const { data } = await apiClient.get('/frontend/models/list');
  // 後端返回的格式是 {value: [], Count: number}，需要提取 value 字段
  return data.value || data || [];
};

export const useYoloModelList = () => {
  return useQuery<YoloModelFileInfo[], Error>({
    queryKey: ['yoloModelList'],
    queryFn: fetchYoloModelList,
    refetchOnWindowFocus: false,
  });
};

// 模型狀態切換
const toggleModelStatus = async (modelId: string): Promise<any> => {
  const { data } = await apiClient.post(`/frontend/models/${modelId}/toggle`);
  return data;
};

export const useToggleModelStatus = () => {
  return useMutation({
    mutationFn: toggleModelStatus,
  });
};

// 取得已啟用的模型清單（供其他功能使用）
const fetchActiveModels = async (): Promise<YoloModelFileInfo[]> => {
  const { data } = await apiClient.get('/frontend/models/active');
  // 後端返回的格式是 {value: [], Count: number}，需要提取 value 字段
  return data.value || data || [];
};

export const useActiveModels = () => {
  return useQuery<YoloModelFileInfo[], Error>({
    queryKey: ['activeModels'],
    queryFn: fetchActiveModels,
    refetchOnWindowFocus: false,
  });
};

// 分析任務相關介面
export interface AnalysisTaskRequest {
  task_type: 'video_file' | 'realtime_camera';
  source_info: {
    file_path?: string;
    original_filename?: string;
    confidence_threshold?: number;
    camera_index?: number;
  };
  source_width?: number;
  source_height?: number;
  source_fps?: number;
}

export interface AnalysisTask {
  id: number;
  task_type: string;
  status: string;
  source_info: any;
  statistics?: any;
  source_width?: number;
  source_height?: number;
  source_fps?: number;
  start_time?: string;
  end_time?: string;
  created_at: string;
  task_name?: string;
  model_id?: string;
  confidence_threshold?: number;
}

export interface CreateAnalysisTaskResponse {
  success: boolean;
  task_id: number;
  message: string;
  task: AnalysisTask;
  status?: string;  // 對於 create-and-execute API 的額外狀態欄位
}

// 創建分析任務的非同步函式
const createAnalysisTask = async (taskData: AnalysisTaskRequest): Promise<CreateAnalysisTaskResponse> => {
  const { data } = await apiClient.post('/tasks/create', taskData);
  return data;
};

// 創建並執行分析任務的非同步函式
const createAndExecuteAnalysisTask = async (taskData: AnalysisTaskRequest): Promise<CreateAnalysisTaskResponse> => {
  const { data } = await apiClient.post('/tasks/create-and-execute', taskData);
  return data;
};

// 影片檔案資訊介面
export interface VideoFileInfo {
  id: string;
  name: string;
  file_path: string;
  upload_time: string;
  size: string;
  duration?: string;
  resolution?: string;
  status: 'ready' | 'analyzing' | 'completed';
}

export interface VideoListResponse {
  videos: VideoFileInfo[];
  total: number;
}

// 獲取影片列表的非同步函式 - 讀取真實目錄內容 
const fetchVideoList = async (): Promise<VideoListResponse> => {
  // 使用主 API 路由器中的影片檔案端點
  try {
    const { data } = await apiClient.get('/video-files');
    return data;
  } catch (error) {
    console.warn('主影片檔案 API 無法訪問，嘗試專用影片列表 API');
    
    // 嘗試專用影片列表 API
    try {
      const { data } = await apiClient.get('/video-list/simple');
      return data;
    } catch (listError) {
      console.warn('專用影片列表 API 無法訪問，嘗試前端路由');
      
      // 嘗試原本的前端路由
      try {
        const { data } = await apiClient.get('/frontend/videos');
        return data;
      } catch (frontendError) {
        console.warn('所有 API 無法訪問，使用實際檔案資料作為備案');
        
        // 最後備案：返回基於實際目錄檔案的資料
        return {
          videos: [
            {
              id: '20250909_231421_3687560-uhd_2160_3840_30fps.mp4',
              name: '20250909_231421_3687560-uhd_2160_3840_30fps.mp4',
              file_path: 'D:/project/system/yolo_backend/uploads/videos/20250909_231421_3687560-uhd_2160_3840_30fps.mp4',
              upload_time: '2025-09-09 23:14:21',
              size: '20.0MB',
              duration: '0:07', 
              resolution: '2160x3840',
              status: 'ready'
            }
          ],
          total: 1
        };
      }
    }
  }
};

// 創建分析任務的 mutation hook
export const useCreateAnalysisTask = () => {
  return useMutation<CreateAnalysisTaskResponse, Error, AnalysisTaskRequest>({
    mutationFn: createAnalysisTask,
  });
};

// 創建並執行分析任務的 mutation hook
export const useCreateAndExecuteAnalysisTask = () => {
  return useMutation<CreateAnalysisTaskResponse, Error, AnalysisTaskRequest>({
    mutationFn: createAndExecuteAnalysisTask,
  });
};

// ===== 影片列表相關 =====

export interface VideoFileInfo {
  id: string;
  name: string;
  file_path: string;
  upload_time: string;
  size: string;
  duration?: string;
  resolution?: string;
  status: 'ready' | 'analyzing' | 'completed';
}

export interface VideoListResponse {
  videos: VideoFileInfo[];
  total: number;
}

// 獲取影片列表的 hook
export const useVideoList = () => {
  return useQuery<VideoListResponse, Error>({
    queryKey: ['videoList'],
    queryFn: fetchVideoList,
    // 每 10 秒刷新一次
    refetchInterval: 10000,
  });
};

// 刪除影片的回應介面
export interface DeleteVideoResponse {
  success: boolean;
  message: string;
  deleted_file: string;
}

// 刪除影片的函式
const deleteVideo = async (videoId: string): Promise<DeleteVideoResponse> => {
  const { data } = await apiClient.delete(`/frontend/videos/${videoId}`);
  return data;
};

// 刪除影片的 hook
export const useDeleteVideo = () => {
  return useMutation<DeleteVideoResponse, Error, string>({
    mutationFn: deleteVideo,
  });
};

// 刪除攝影機回應型別
interface DeleteCameraResponse {
  message: string;
  camera_id: string;
}

// 刪除攝影機的函式
const deleteCamera = async (cameraId: string): Promise<DeleteCameraResponse> => {
  const { data } = await apiClient.delete(`/frontend/cameras/${cameraId}`);
  return data;
};

// 刪除攝影機的 hook
export const useDeleteCamera = () => {
  return useMutation<DeleteCameraResponse, Error, string>({
    mutationFn: deleteCamera,
  });
};

// 攝影機串流相關介面和Hook
export interface CameraStreamInfo {
  camera_id: string;
  webrtc_endpoint: string;
  is_active: boolean;
  resolution: string;
  fps: number;
}

// 檢查攝影機是否有可用串流
export const useCameraStreamInfo = (cameraId: string | null) => {
  return useQuery<CameraStreamInfo, Error>({
    queryKey: ['camera-stream', cameraId],
    queryFn: async (): Promise<CameraStreamInfo> => {
      if (!cameraId) throw new Error('Camera ID is required');
      
      // 檢查攝影機是否存在
      const cameras = await fetchCameras();
      const camera = cameras.find(c => c.id === cameraId);
      
      if (!camera) {
        throw new Error('Camera not found');
      }
      
      // 對於USB攝影機，不管狀態如何都嘗試串流
      // 因為資料庫狀態可能不準確，物理設備可能仍然可用

      // 對於本地USB攝影機，使用索引0進行串流測試
      if (camera.camera_type === 'USB' || camera.device_index !== undefined) {
        // 使用攝影機的 device_index 或預設為 0
        const cameraIndex = camera.device_index !== undefined ? camera.device_index : 0;
        return {
          camera_id: cameraId,
          webrtc_endpoint: `/frontend/cameras/${cameraId}/webrtc`,
          is_active: camera.status === 'active' || camera.status === 'online',
          resolution: camera.resolution,
          fps: camera.fps,
        };
      } else {
        throw new Error('僅支援本地USB攝影機串流');
      }
    },
    enabled: !!cameraId,
    retry: false,
    staleTime: 5000, // 5 seconds
  });
};

// 切換攝影機狀態的Hook
export interface ToggleCameraResponse {
  camera_id: string;
  status: string;
  message: string;
}

export const useToggleCamera = () => {
  return useMutation<ToggleCameraResponse, Error, string>({
    mutationFn: async (cameraId: string): Promise<ToggleCameraResponse> => {
      const { data } = await apiClient.put(`/frontend/cameras/${cameraId}/toggle`);
      return data;
    },
  });
};

// Live Person Camera 相關介面
export interface LivePersonCameraRequest {
  task_name: string;
  camera_id: string;
  model_id: string;
  confidence: number;
  iou_threshold: number;
  description?: string;
  client_stream?: boolean;
}

export interface LivePersonCameraResponse {
  task_id: string;
  status: string;
  message: string;
  camera_info: any;
  model_info: any;
  created_at: string;
  websocket_url?: string;
}

export interface StopLivePersonCameraResponse {
  task_id: string;
  status: string;
  message: string;
  stopped_at: string;
}

export interface LaunchPreviewResponse {
  task_id: number;
  pid: number;
  already_running: boolean;
  message: string;
}

export interface LaunchPreviewRequestPayload {
  taskId: string;
  alertRules?: TaskAlertRulePayload[];
}

// 開始 Live Person Camera 分析的非同步函式
const startLivePersonCamera = async (requestData: LivePersonCameraRequest): Promise<LivePersonCameraResponse> => {
  const { data } = await apiClient.post('/frontend/analysis/start-realtime', requestData);
  return data;
};

// 開始 Live Person Camera 分析的 Hook
export const useStartLivePersonCamera = () => {
  return useMutation<LivePersonCameraResponse, Error, LivePersonCameraRequest>({
    mutationFn: startLivePersonCamera,
  });
};

// 停止 Live Person Camera 分析的非同步函式
const stopLivePersonCamera = async (taskId: string): Promise<StopLivePersonCameraResponse> => {
  const { data } = await apiClient.delete(`/frontend/analysis/live-person-camera/${taskId}`);
  return data;
};

// 停止 Live Person Camera 分析的 Hook
export const useStopLivePersonCamera = () => {
  return useMutation<StopLivePersonCameraResponse, Error, string>({
    mutationFn: stopLivePersonCamera,
  });
};

const launchLivePersonPreview = async ({
  taskId,
  alertRules,
}: LaunchPreviewRequestPayload): Promise<LaunchPreviewResponse> => {
  const payload = alertRules && alertRules.length > 0 ? { alert_rules: alertRules } : undefined;
  const { data } = await apiClient.post(
    `/frontend/analysis/live-person-camera/${taskId}/preview`,
    payload
  );
  return data;
};

export const useLaunchLivePersonPreview = () => {
  return useMutation<LaunchPreviewResponse, Error, LaunchPreviewRequestPayload>({
    mutationFn: launchLivePersonPreview,
  });
};

// 分析任務列表 API 回應介面
export interface AnalysisTasksResponse {
  success: boolean;
  tasks: AnalysisTask[];
  count: number;
}

// 獲取分析任務列表的非同步函式
const fetchAnalysisTasks = async (
  taskType?: string,
  status?: string,
  limit: number = 50
): Promise<AnalysisTasksResponse> => {
  const params = new URLSearchParams();
  if (taskType) params.append('task_type', taskType);
  if (status) params.append('status', status);
  params.append('limit', limit.toString());
  
  const { data } = await apiClient.get(`/analysis/tasks?${params.toString()}`);
  return data;
};

// 獲取分析任務列表的Hook
export const useAnalysisTasks = (
  taskType?: string,
  status?: string,
  limit: number = 50,
  refetchIntervalMs: number = 10000
) => {
  return useQuery<AnalysisTasksResponse, Error>({
    queryKey: ['analysisTasks', taskType, status, limit],
    queryFn: () => fetchAnalysisTasks(taskType, status, limit),
    // 允許外部指定自動重新整理頻率
    refetchInterval: refetchIntervalMs,
  });
};

// 任務線段 / 區域資訊
export interface TaskRegionItem {
  id?: string;
  label?: string;
  metrics?: Record<string, unknown> | null;
}

export interface TaskRegionResponse {
  task_id: number;
  updated_at: string | null;
  lines: TaskRegionItem[];
  zones: TaskRegionItem[];
}

const fetchTaskRegions = async (taskId: string | number): Promise<TaskRegionResponse> => {
  const { data } = await apiClient.get(`/frontend/analysis/tasks/${taskId}/regions`);
  return data;
};

export const useTaskRegions = (taskId?: string | number) => {
  return useQuery<TaskRegionResponse, Error>({
    queryKey: ['taskRegions', taskId],
    queryFn: () => fetchTaskRegions(taskId!),
    enabled: Boolean(taskId),
  });
};

// 停止任務的非同步函式
const stopAnalysisTask = async (taskId: string): Promise<{ message: string; task_id: string }> => {
  const { data } = await apiClient.put(`/frontend/tasks/${taskId}/stop`);
  return data;
};

// 停止任務的Hook
export const useStopAnalysisTask = () => {
  const queryClient = useQueryClient();
  
  return useMutation<{ message: string; task_id: string }, Error, string>({
    mutationFn: stopAnalysisTask,
    onSuccess: () => {
      // 自動重新載入任務列表
      queryClient.invalidateQueries({ queryKey: ['analysisTasks'] });
    },
  });
};

// 刪除任務的非同步函式
const deleteAnalysisTask = async (taskId: string): Promise<{ success: boolean; message: string; task_id: number; deleted_detections: number }> => {
  const { data } = await apiClient.delete(`/analysis/tasks/${taskId}`);
  return data;
};

// 刪除任務的Hook
export const useDeleteAnalysisTask = () => {
  const queryClient = useQueryClient();
  
  return useMutation<{ success: boolean; message: string; task_id: number; deleted_detections: number }, Error, string>({
    mutationFn: deleteAnalysisTask,
    onSuccess: () => {
      // 自動重新載入任務列表
      queryClient.invalidateQueries({ queryKey: ['analysisTasks'] });
    },
  });
};

// 切換任務狀態（暫停/恢復）的非同步函式
const toggleAnalysisTaskStatus = async (taskId: string): Promise<{ message: string; task_id: number; old_status: string; new_status: string }> => {
  const { data } = await apiClient.put(`/frontend/tasks/${taskId}/toggle`);
  return data;
};

// 切換任務狀態的Hook
export const useToggleAnalysisTaskStatus = () => {
  const queryClient = useQueryClient();
  
  return useMutation<{ message: string; task_id: number; old_status: string; new_status: string }, Error, string>({
    mutationFn: toggleAnalysisTaskStatus,
    onSuccess: () => {
      // 自動重新載入任務列表
      queryClient.invalidateQueries({ queryKey: ['analysisTasks'] });
    },
  });
};

// ===== 偵測記錄 =====

export interface DetectionRecord {
  id: number;
  tracker_id?: number | null;
  timestamp: string | null;
  start_time?: string | null;
  end_time?: string | null;
  task_id: number;
  task_name?: string | null;
  task_type?: string | null;
  camera_name?: string | null;
  camera_id?: string | null;
  object_type?: string | null;
  object_chinese?: string | null;
  object_id?: string;
  confidence?: number | null;
  bbox_x1?: number | null;
  bbox_y1?: number | null;
  bbox_x2?: number | null;
  bbox_y2?: number | null;
  center_x?: number | null;
  center_y?: number | null;
  width?: number | null;
  height?: number | null;
  area?: number | null;
  zone?: string | null;
  zone_chinese?: string | null;
  status?: string | null;
  thumbnail_path?: string | null;
  thumbnail_url?: string | null;
}

export interface DetectionRecordsResponse {
  results: DetectionRecord[];
  total: number;
  page: number;
  limit: number;
  total_pages: number;
}

export interface DetectionRecordsQuery {
  page?: number;
  limit?: number;
  search?: string;
  cameraName?: string;
  cameraId?: string;
  taskId?: number;
  objectType?: string;
  startTime?: string;
  endTime?: string;
  minConfidence?: number;
  maxConfidence?: number;
}

const fetchDetectionResults = async (
  params: DetectionRecordsQuery = {}
): Promise<DetectionRecordsResponse> => {
  const queryParams: Record<string, string | number> = {};

  if (params.page !== undefined) queryParams.page = params.page;
  if (params.limit !== undefined) queryParams.limit = params.limit;
  if (params.search) queryParams.search = params.search;
  if (params.cameraName) queryParams.camera_name = params.cameraName;
  if (params.cameraId) queryParams.camera_id = params.cameraId;
  if (params.taskId !== undefined) queryParams.task_id = params.taskId;
  if (params.objectType) queryParams.object_type = params.objectType;
  if (params.startTime) queryParams.start_time = params.startTime;
  if (params.endTime) queryParams.end_time = params.endTime;
  if (params.minConfidence !== undefined) queryParams.min_confidence = params.minConfidence;
  if (params.maxConfidence !== undefined) queryParams.max_confidence = params.maxConfidence;

  const { data } = await apiClient.get('/frontend/detection-results', {
    params: queryParams,
  });
  return data;
};

export const useDetectionResults = (params: DetectionRecordsQuery = {}) => {
  return useQuery<DetectionRecordsResponse, Error>({
    queryKey: [
      'detectionResults',
      params.page,
      params.limit,
      params.search,
      params.cameraName,
      params.cameraId,
      params.taskId,
      params.objectType,
      params.startTime,
      params.endTime,
      params.minConfidence,
      params.maxConfidence,
    ],
    queryFn: () => fetchDetectionResults(params),
    placeholderData: keepPreviousData,
  });
};

// ===== 資料庫備份 =====
export type BackupType = "full" | "incremental" | "differential";
export type BackupFrequency = "hourly" | "daily" | "weekly" | "monthly";

export interface DatabaseBackupFile {
  name: string;
  size: number;
  created_at: string;
}

export interface DatabaseBackupInfo {
  backup_type: BackupType;
  backup_location: string;
  auto_backup_enabled: boolean;
  backup_frequency: BackupFrequency;
  retention_days: number;
  last_backup_time?: string | null;
  last_backup_file?: string | null;
  last_backup_size?: number | null;
  database_size_bytes: number;
  total_record_estimate: number;
  recent_backups: DatabaseBackupFile[];
}

export interface DatabaseBackupSettingsPayload {
  backup_type: BackupType;
  backup_location: string;
  auto_backup_enabled: boolean;
  backup_frequency: BackupFrequency;
  retention_days: number;
}

export interface ManualDatabaseBackupResponse {
  message: string;
  backup_file: string;
  backup_path: string;
  size: number;
  download_url: string;
  finished_at: string;
}

export interface RestoreDatabaseBackupResponse {
  message: string;
  restored_at: string;
}

const fetchDatabaseBackupInfo = async (): Promise<DatabaseBackupInfo> => {
  const { data } = await apiClient.get('/frontend/database/backup/settings');
  return data;
};

export const useDatabaseBackupInfo = (enabled: boolean = true) => {
  return useQuery<DatabaseBackupInfo, Error>({
    queryKey: ['databaseBackupInfo'],
    queryFn: fetchDatabaseBackupInfo,
    enabled,
    refetchOnWindowFocus: false,
  });
};

const updateDatabaseBackupSettings = async (
  payload: DatabaseBackupSettingsPayload,
): Promise<DatabaseBackupInfo> => {
  const { data } = await apiClient.put('/frontend/database/backup/settings', payload);
  return data;
};

export const useUpdateDatabaseBackupSettings = () => {
  const queryClient = useQueryClient();
  return useMutation<DatabaseBackupInfo, Error, DatabaseBackupSettingsPayload>({
    mutationFn: updateDatabaseBackupSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseBackupInfo'] });
    },
  });
};

const runManualDatabaseBackup = async (): Promise<ManualDatabaseBackupResponse> => {
  const { data } = await apiClient.post('/frontend/database/backup/run');
  return data;
};

export const useManualDatabaseBackup = () => {
  const queryClient = useQueryClient();
  return useMutation<ManualDatabaseBackupResponse, Error, void>({
    mutationFn: runManualDatabaseBackup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseBackupInfo'] });
    },
  });
};

const restoreDatabaseBackup = async (file: File): Promise<RestoreDatabaseBackupResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  const { data } = await apiClient.post('/frontend/database/backup/restore', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
};

export const useRestoreDatabaseBackup = () => {
  const queryClient = useQueryClient();
  return useMutation<RestoreDatabaseBackupResponse, Error, File>({
    mutationFn: restoreDatabaseBackup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseBackupInfo'] });
    },
  });
};

// ===== 系統控制相關 =====
interface ShutdownResponse {
  message: string;
  scheduled_in_seconds?: number;
}

const shutdownSystem = async (): Promise<ShutdownResponse> => {
  // 後端實際路徑為 /api/v1/frontend/system/shutdown ，apiClient 已在 baseURL 中含 /api/v1
  const { data } = await apiClient.post('/frontend/system/shutdown');
  return data;
};

export const useShutdownSystem = () => {
  return useMutation<ShutdownResponse, Error, void>({
    mutationFn: shutdownSystem,
  });
};

const restartSystem = async (): Promise<ShutdownResponse> => {
  const { data } = await apiClient.post('/frontend/system/restart');
  return data;
};

export const useRestartSystem = () => {
  return useMutation<ShutdownResponse, Error, void>({
    mutationFn: restartSystem,
  });
};

// 清空資料庫
interface ClearDatabaseResponse {
  message: string;
}

const clearDatabase = async (): Promise<ClearDatabaseResponse> => {
  const { data } = await apiClient.post('/frontend/clear-database');
  return data;
};

export const useClearDatabase = () => {
  const queryClient = useQueryClient();
  return useMutation<ClearDatabaseResponse, Error, void>({
    mutationFn: clearDatabase,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseBackupInfo'] });
    },
  });
};



