import { useCallback, useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Progress } from "./ui/progress";
import { Switch } from "./ui/switch";
import { Checkbox } from "./ui/checkbox";
import {
  useYoloModelList,
  useToggleModelStatus,
  useActiveModels,
  useVideoUpload,
  VideoUploadResponse,
  useCreateAnalysisTask,
  useCreateAndExecuteAnalysisTask,
  AnalysisTaskRequest,
  CreateAnalysisTaskResponse,
  useAnalysisTasks,
  AnalysisTask,
  useStopAnalysisTask,
  useDeleteAnalysisTask,
  useToggleAnalysisTaskStatus,
  useVideoList,
  VideoFileInfo,
  useDeleteVideo,
  useCameras,
  useStartLivePersonCamera,
  useStopLivePersonCamera,
  useLaunchLivePersonPreview,
  LivePersonCameraRequest,
  useVideoAnalysis,
  CameraInfo,
  useAlertRules,
  AlertRule,
  useTaskRegions,
  TaskAlertRulePayload,
  useSaveTaskAlerts,
  useTaskAlerts,
} from "../hooks/react-query-hooks";
import {
  Brain,
  Upload,
  Play,
  Eye,
  Activity,
  Zap,
  FileVideo,
  Camera,
  Plus,
  Trash2,
  Clock,
  Square,
  Settings,
  MousePointer,
  Minus,
  RotateCcw,
  Save,
  Users,
  ArrowLeftRight,
  Timer,
  Monitor,
  BellRing,
} from "lucide-react";

type CameraWithMeta = CameraInfo & {
  device_index?: number;
  device_id?: string;
  list_index?: number;
};

const formatDuration = (value: unknown): string => {
  if (value === undefined || value === null) {
    return "0s";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" && !Number.isNaN(value)) {
    return `${value.toFixed(1)}s`;
  }
  return "0s";
};

const ensureNumeric = (value: unknown, fallback = 0): number => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return fallback;
};

const toOptionalNumber = (value: unknown): number | undefined => {
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return undefined;
};

const formatUTCTime = (timeStr: string | null | undefined): string => {
  if (!timeStr) return "未開始";

  // 如果時間字串沒有時區資訊（沒有 Z 或 +），則視為 UTC
  let dateStr = timeStr;
  if (!dateStr.endsWith("Z") && !dateStr.includes("+")) {
    dateStr += "Z";
  }

  try {
    return new Date(dateStr).toLocaleString();
  } catch (e) {
    console.error("時間格式化錯誤:", e);
    return timeStr;
  }
};


export function DetectionAnalysisOriginal() {
  console.log("DetectionAnalysisOriginal 開始渲染");

  // 狀態管理
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [analysisProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadResult, setUploadResult] = useState<VideoUploadResponse | null>(null);
  const [confidenceThreshold, setConfidenceThreshold] = useState<number>(0.5);
  const [selectedVideo, setSelectedVideo] = useState<VideoFileInfo | null>(null);

  // 即時分析相關狀態
  const [selectedCamera, setSelectedCamera] = useState<string>("");
  const [realtimeModel, setRealtimeModel] = useState<string>("");
  const [isLivePersonCameraRunning, setIsLivePersonCameraRunning] = useState(false);
  const [livePersonTaskId, setLivePersonTaskId] = useState<string | null>(null);

  const [videoDevices, setVideoDevices] = useState<MediaDeviceInfo[]>([]);
  // 模型檔案選取狀態
  const [selectedModelFile, setSelectedModelFile] = useState<File | null>(null);
  const [selectedModelType, setSelectedModelType] = useState<string>("detection");
  // 模型上傳進度 / 錯誤狀態
  const [modelUploadPending, setModelUploadPending] = useState<boolean>(false);
  const [modelUploadError, setModelUploadError] = useState<string | null>(null);
  const [isAlertConfigOpen, setIsAlertConfigOpen] = useState(false);
  const [alertConfigTask, setAlertConfigTask] = useState<{ id: string; name: string; cameraId?: string | null } | null>(null);
  type TaskRuleSelection = {
    enabled: boolean;
    selections?: string[];
  };
  const [taskAlertBindings, setTaskAlertBindings] = useState<
    Record<string, Record<string, TaskRuleSelection>>
  >({});
  const [lineSelectionByTask, setLineSelectionByTask] = useState<Record<string, string>>({});
  const [zoneSelectionByTask, setZoneSelectionByTask] = useState<Record<string, string>>({});

  const alertRuleTypeMeta = useMemo(
    () => ({
      lineCrossing: { label: "越線警報", description: "監控指定線段的進出數量" },
      zoneDwell: { label: "區域滯留警報", description: "偵測停留時間過長的目標" },
      speedAnomaly: { label: "速度異常警報", description: "平均或最大速度超過門檻" },
      crowdCount: { label: "人數警報", description: "現場人數超過設定上限" },
      fallDetection: { label: "跌倒警報", description: "啟用跌倒動作模型並偵測異常" },
    }),
    []
  );
  const { data: alertRulesData } = useAlertRules();
  const alertRules = (alertRulesData ?? []) as AlertRule[];
  const {
    data: taskRegionData,
    isLoading: isTaskRegionsLoading,
  } = useTaskRegions(alertConfigTask?.id);
  const { data: savedTaskAlerts } = useTaskAlerts(alertConfigTask?.id);
  const alertRulesForDialog = useMemo(() => {
    if (!alertConfigTask) {
      return [];
    }
    const cameraId = alertConfigTask.cameraId;
    return alertRules
      .filter((rule) => {
        const cameras = rule.cameras || [];
        if (!cameraId) {
          return true;
        }
        return cameras.length === 0 || cameras.includes(cameraId);
      })
      .map((rule) => {
        const meta = alertRuleTypeMeta[rule.rule_type as AlertTypeKey];
        return {
          rule,
          displayName: rule.name || meta?.label || rule.rule_type,
          description: meta?.description || "",
        };
      });
  }, [alertConfigTask, alertRules, alertRuleTypeMeta]);
  useEffect(() => {
    if (!alertConfigTask?.id || !savedTaskAlerts) {
      return;
    }
    setTaskAlertBindings((previous) => {
      const next = { ...previous };
      const taskId = alertConfigTask.id;
      const bindings: Record<string, TaskRuleSelection> = {};
      (savedTaskAlerts.rules ?? []).forEach((rule) => {
        if (!rule?.id) {
          return;
        }
        const selections = Array.isArray(rule.selections)
          ? rule.selections.filter((item): item is string => typeof item === "string")
          : [];
        bindings[rule.id] = { enabled: true, selections };
      });
      next[taskId] = bindings;
      return next;
    });
  }, [alertConfigTask?.id, savedTaskAlerts]);

  const handleDevicesUpdated = useCallback((devices: MediaDeviceInfo[]) => {
    const videoOnly = devices.filter((device) => device.kind === "videoinput");
    setVideoDevices((previous) => {
      const prevIds = previous.map((device) => device.deviceId);
      const nextIds = videoOnly.map((device) => device.deviceId);
      if (
        prevIds.length === nextIds.length &&
        prevIds.every((id, index) => id === nextIds[index])
      ) {
        return previous;
      }
      return videoOnly;
    });
  }, []);

  useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      return;
    }

    navigator.mediaDevices
      .enumerateDevices()
      .then((devices) => handleDevicesUpdated(devices))
      .catch(() => {
        /* ignore */
      });
  }, [handleDevicesUpdated]);

  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener || !navigator.mediaDevices?.removeEventListener) {
      return;
    }

    const listener = () => {
      navigator.mediaDevices
        .enumerateDevices()
        .then((devices) => handleDevicesUpdated(devices))
        .catch(() => {
          /* ignore */
        });
    };

    navigator.mediaDevices.addEventListener("devicechange", listener);
    return () => {
      navigator.mediaDevices.removeEventListener("devicechange", listener);
    };
  }, [handleDevicesUpdated]);

  // 獲取所有分析任務（不限制狀態，包含所有任務）
  const {
    data: allTasksData,
    isLoading: isTasksLoading,
    error: tasksError,
    refetch: refetchTasks,
  } = useAnalysisTasks(undefined, undefined, 50, 2000);

  // 獲取運行中的任務
  const runningTasks = allTasksData?.tasks || [];

  const realtimeAnalysisTasks = useMemo(() => {
    return (runningTasks ?? [])
      .filter((task) => task.task_type === "realtime_camera")
      .map((task) => {
        const sourceInfo = task.source_info || {};
        const stats = task.statistics || sourceInfo.statistics || {};
        const lineStatsRecord: Record<string, any> =
          (stats.line_stats ?? stats.line ?? stats.crossing ?? {}) || {};
        const zoneStatsRecord: Record<string, any> =
          (stats.zone_stats ?? stats.zone ?? stats.dwell ?? {}) || {};
        const extraRecord: Record<string, any> =
          (stats.extra ?? stats.extras ?? stats.summary ?? {}) || {};

        const lineOptions = Object.entries(lineStatsRecord)
          .filter(
            ([, value]) => value && typeof value === "object" && !Array.isArray(value)
          )
          .map(([label, metric]) => {
            const metrics = metric as Record<string, any>;
            return {
              id: String(label),
              label: String(label),
              in: ensureNumeric(metrics.in ?? metrics.enter ?? metrics.inbound),
              out: ensureNumeric(metrics.out ?? metrics.leave ?? metrics.outbound),
            };
          });

        const zoneOptionsWithRaw = Object.entries(zoneStatsRecord)
          .filter(
            ([, value]) => value && typeof value === "object" && !Array.isArray(value)
          )
          .map(([label, metric]) => {
            const metrics = metric as Record<string, any>;
            const avgSeconds = ensureNumeric(
              metrics.avg ?? metrics.avg_duration ?? metrics.average ?? metrics.mean
            );
            const maxSeconds = ensureNumeric(
              metrics.max ?? metrics.max_duration ?? metrics.longest ?? metrics.peak
            );
            return {
              id: String(label),
              label: String(label),
              current: ensureNumeric(metrics.current ?? metrics.count),
              avgDuration: formatDuration(avgSeconds),
              maxDuration: formatDuration(maxSeconds),
              avgSeconds,
              maxSeconds,
            };
          });

        const totalLineIn =
          ensureNumeric(
            lineStatsRecord?.in ??
            lineStatsRecord?.enter ??
            stats.line_total_in ??
            extraRecord?.line_total_in
          ) || lineOptions.reduce((sum, option) => sum + option.in, 0);
        const totalLineOut =
          ensureNumeric(
            lineStatsRecord?.out ??
            lineStatsRecord?.leave ??
            stats.line_total_out ??
            extraRecord?.line_total_out
          ) || lineOptions.reduce((sum, option) => sum + option.out, 0);
        const totalZoneCurrent =
          ensureNumeric(
            zoneStatsRecord?.current ??
            zoneStatsRecord?.count ??
            extraRecord?.zone_total_current
          ) || zoneOptionsWithRaw.reduce((sum, option) => sum + option.current, 0);
        const avgSource =
          zoneStatsRecord?.avg ??
          zoneStatsRecord?.avg_duration ??
          zoneStatsRecord?.average ??
          extraRecord?.zone_avg_duration ??
          zoneOptionsWithRaw[0]?.avgSeconds ??
          0;
        const maxSource =
          zoneStatsRecord?.max ??
          zoneStatsRecord?.max_duration ??
          zoneStatsRecord?.longest ??
          extraRecord?.zone_max_duration ??
          zoneOptionsWithRaw[0]?.maxSeconds ??
          0;

        const cameraIdValue =
          sourceInfo.camera_id !== undefined && sourceInfo.camera_id !== null
            ? String(sourceInfo.camera_id)
            : undefined;
        const currentPeopleCount =
          toOptionalNumber(extraRecord?.current_person_count) ??
          toOptionalNumber(stats.current_person_count) ??
          toOptionalNumber(stats.detected_people) ??
          toOptionalNumber(stats.people_count) ??
          toOptionalNumber(sourceInfo.detected_people) ??
          toOptionalNumber(sourceInfo.people_count);
        const fallbackPeopleCount =
          toOptionalNumber(extraRecord?.last_person_count) ??
          toOptionalNumber(stats.person_count) ??
          toOptionalNumber(sourceInfo.person_count) ??
          0;
        const detectedPeople =
          currentPeopleCount !== undefined
            ? Math.max(0, currentPeopleCount)
            : Math.max(0, fallbackPeopleCount ?? 0);
        return {
          id: task.id,
          status: task.status ?? task.task_status ?? "unknown",
          camera:
            sourceInfo.camera_name ||
            sourceInfo.camera_id ||
            task.task_name ||
            `任務 ${task.id}`,
          cameraId: cameraIdValue,
          detectedPeople,
          crossingLine: {
            summary: {
              in: totalLineIn,
              out: totalLineOut,
            },
            options: lineOptions,
          },
          dwellArea: {
            summary: {
              current: totalZoneCurrent,
              avgDuration: formatDuration(avgSource),
              maxDuration: formatDuration(maxSource),
            },
            options: zoneOptionsWithRaw.map(({ id, label, current, avgDuration, maxDuration }) => ({
              id,
              label,
              current,
              avgDuration,
              maxDuration,
            })),
          },
        };
      });
  }, [runningTasks]);

  useEffect(() => {
    const activeTaskIds = new Set(realtimeAnalysisTasks.map((task) => String(task.id)));
    setLineSelectionByTask((previous) => {
      const keys = Object.keys(previous);
      const filtered = keys.filter((key) => activeTaskIds.has(key));
      if (filtered.length === keys.length) {
        return previous;
      }
      const next: Record<string, string> = {};
      filtered.forEach((key) => {
        next[key] = previous[key];
      });
      return next;
    });
    setZoneSelectionByTask((previous) => {
      const keys = Object.keys(previous);
      const filtered = keys.filter((key) => activeTaskIds.has(key));
      if (filtered.length === keys.length) {
        return previous;
      }
      const next: Record<string, string> = {};
      filtered.forEach((key) => {
        next[key] = previous[key];
      });
      return next;
    });
  }, [realtimeAnalysisTasks]);

  const handleOpenAlertConfig = (taskId: string, name: string, cameraId?: string | null) => {
    setAlertConfigTask({ id: taskId, name, cameraId });
    setIsAlertConfigOpen(true);
  };

  const handleToggleAlertRule = (
    taskId: string,
    rule: AlertRule,
    enabled: boolean,
    availableItems: Array<{ id?: string; label?: string }> = []
  ) => {
    setTaskAlertBindings((previous) => {
      const taskBindings = { ...(previous[taskId] ?? {}) };
      if (!enabled) {
        taskBindings[rule.id] = { enabled: false, selections: [] };
      } else {
        const prefix = rule.rule_type === "zoneDwell" ? "zone" : "line";
        const selections = availableItems.length
          ? availableItems.map(
            (item, index) => item.id || `${rule.id}-${prefix}-${index + 1}`
          )
          : [];
        taskBindings[rule.id] = {
          enabled: true,
          selections,
        };
      }
      return { ...previous, [taskId]: taskBindings };
    });
  };

  const handleToggleAlertItem = (
    taskId: string,
    ruleId: string,
    itemId: string,
    checked: boolean
  ) => {
    setTaskAlertBindings((previous) => {
      const taskBindings = { ...(previous[taskId] ?? {}) };
      const ruleState = taskBindings[ruleId] ?? { enabled: true, selections: [] };
      const currentSelections = new Set(ruleState.selections ?? []);
      if (checked) {
        currentSelections.add(itemId);
      } else {
        currentSelections.delete(itemId);
      }
      taskBindings[ruleId] = {
        ...ruleState,
        selections: Array.from(currentSelections),
      };
      return { ...previous, [taskId]: taskBindings };
    });
  };
  const handleSaveAlertConfig = async () => {
    if (!alertConfigTask) {
      return;
    }
    try {
      const payload = buildTaskAlertPayload(alertConfigTask.id);
      await saveTaskAlertsMutation.mutateAsync({
        taskId: alertConfigTask.id,
        rules: payload,
      });
      alert("警報設定已儲存並會立即生效。");
      setIsAlertConfigOpen(false);
    } catch (error) {
      console.error("儲存警報設定失敗:", error);
      alert("儲存警報設定失敗，請稍後再試。");
    }
  };


  // 真實數據獲取
  const { data: yoloModels, isLoading: modelsLoading, error: modelsError, refetch: refetchModels } = useYoloModelList();
  const { data: activeModels, refetch: refetchActiveModels } = useActiveModels();
  const { data: videoListData, isLoading: videoListLoading, refetch: refetchVideoList } = useVideoList();
  const { data: cameras, isLoading: isCamerasLoading, isError: isCamerasError } = useCameras();
  const toggleModelMutation = useToggleModelStatus();
  const uploadVideoMutation = useVideoUpload();
  const createTaskMutation = useCreateAnalysisTask();
  const createAndExecuteTaskMutation = useCreateAndExecuteAnalysisTask();
  const deleteVideoMutation = useDeleteVideo();
  const startLivePersonCameraMutation = useStartLivePersonCamera();
  const stopLivePersonCameraMutation = useStopLivePersonCamera();
  const launchLivePersonPreviewMutation = useLaunchLivePersonPreview();
  const saveTaskAlertsMutation = useSaveTaskAlerts();
  const videoAnalysisMutation = useVideoAnalysis();
  const stopTaskMutation = useStopAnalysisTask();
  const deleteTaskMutation = useDeleteAnalysisTask();
  const toggleTaskStatusMutation = useToggleAnalysisTaskStatus();

  console.log("YOLO 模型數據:", yoloModels);
  console.log("啟用的模型:", activeModels);
  console.log("攝影機數據:", cameras);
  console.log("本機可用攝影機裝置:", videoDevices);

  const cameraOptions: CameraWithMeta[] = useMemo(() => {
    return (cameras ?? []).map((camera, index) => {
      const rawDeviceIndex = camera.device_index;
      let numericIndex: number | undefined;
      let explicitDeviceId: string | undefined;

      if (typeof rawDeviceIndex === "number" && Number.isFinite(rawDeviceIndex)) {
        numericIndex = Math.trunc(rawDeviceIndex);
      } else if (typeof rawDeviceIndex === "string") {
        const parsedIndex = Number(rawDeviceIndex);
        if (Number.isFinite(parsedIndex)) {
          numericIndex = Math.trunc(parsedIndex);
        } else {
          explicitDeviceId = rawDeviceIndex;
        }
      }

      if (numericIndex === undefined) {
        numericIndex = index;
      }

      let targetDevice: MediaDeviceInfo | undefined;
      if (videoDevices.length > 0) {
        const boundedIndex =
          ((numericIndex % videoDevices.length) + videoDevices.length) % videoDevices.length;
        targetDevice = videoDevices[boundedIndex];
      }

      const finalDeviceId = targetDevice?.deviceId || explicitDeviceId;
      const label =
        targetDevice?.label ||
        explicitDeviceId ||
        (camera.camera_type === "USB"
          ? `本機攝影機 ${numericIndex ?? ""}`.trim()
          : camera.camera_type);

      return {
        ...camera,
        list_index: index,
        device_index: numericIndex,
        device_id: finalDeviceId,
        device_label: label,
        location: camera.group_id || "未指定位置",
        ip:
          targetDevice?.label ||
          (numericIndex !== undefined
            ? `本機設備 #${numericIndex}`
            : camera.rtsp_url || "未知"),
        model: label,
        recording: false,
        nightVision: false,
        motionDetection: false,
      };
    });
  }, [cameras, videoDevices]);

  useEffect(() => {
    if (!cameraOptions.length) {
      if (selectedCamera !== "") {
        setSelectedCamera("");
      }
      return;
    }

    const hasSelected = cameraOptions.some(
      (camera) => camera.id?.toString() === selectedCamera
    );

    if (!hasSelected) {
      const firstId = cameraOptions[0].id?.toString();
      if (firstId) {
        setSelectedCamera(firstId);
      }
    }
  }, [cameraOptions, selectedCamera]);

  const selectedCameraInfo = cameraOptions.find(
    (camera) => camera.id?.toString() === selectedCamera
  );

  // 模擬分析結果數據
  const analysisResults = [
    {
      id: "1",
      videoName: "測試影片.mp4",
      camera: "攝影機01",
      timestamp: "2024-01-15 14:30:25",
      duration: "2:15",
      fileSize: "15.2MB",
      resolution: "1920x1080",
      detections: [
        { class: "person", count: 3, confidence: 0.92 },
        { class: "car", count: 1, confidence: 0.87 }
      ]
    }
  ];

  // 輔助函式
  const getModelStatusColor = (isActive: boolean) => {
    return isActive ? "default" : "outline";
  };

  const getModelStatusText = (isActive: boolean) => {
    return isActive ? "已啟用" : "未啟用";
  };

  const handleToggleModelStatus = async (modelId: string) => {
    console.log("切換模型狀態:", modelId);
    try {
      await toggleModelMutation.mutateAsync(modelId);
      // 重新獲取數據
      refetchModels();
      refetchActiveModels();
      console.log(`模型 ${modelId} 狀態切換成功`);
    } catch (error) {
      console.error('切換模型狀態失敗:', error);
    }
  };

  // 檢查模型是否已啟用
  const isModelActive = (modelId: string) => {
    return activeModels?.some(activeModel => activeModel.id === modelId) || false;
  };

  // 任務管理相關輔助函數
  const getTaskStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'default';
      case 'paused': return 'secondary';
      case 'completed': return 'outline';
      case 'failed': return 'destructive';
      default: return 'outline';
    }
  };

  const getTaskStatusText = (status: string) => {
    switch (status) {
      case 'pending': return '待處理';
      case 'running': return '運行中';
      case 'paused': return '已暫停';
      case 'completed': return '已完成';
      case 'failed': return '失敗';
      default: return '未知';
    }
  };

  const toggleTaskStatus = async (taskId: string) => {
    try {
      await toggleTaskStatusMutation.mutateAsync(taskId);
      console.log('任務狀態已切換:', taskId);
      // React Query 會自動重新載入任務列表數據
    } catch (error) {
      console.error('切換任務狀態失敗:', error);
      alert('切換任務狀態失敗，請稍後再試');
    }
  };

  const stopTask = async (taskId: string) => {
    try {
      await stopTaskMutation.mutateAsync(taskId);
      console.log('任務已停止:', taskId);
      // React Query 會自動重新載入任務列表數據
    } catch (error) {
      console.error('停止任務失敗:', error);
      alert('停止任務失敗，請稍後再試');
    }
  };

  const deleteTask = async (taskId: string, taskName: string) => {
    // 確認對話框
    const confirmed = window.confirm(
      `確定要刪除任務「${taskName}」嗎？\n\n此操作將會：\n- 刪除任務記錄\n- 刪除所有相關的檢測結果\n- 此操作無法復原`
    );

    if (!confirmed) {
      return;
    }

    try {
      const result = await deleteTaskMutation.mutateAsync(taskId);
      console.log('任務已刪除:', result);
      alert(`任務已成功刪除！\n- 任務ID: ${result.task_id}\n- 同時刪除了 ${result.deleted_detections} 筆檢測結果`);
      // React Query 會自動重新載入任務列表數據
    } catch (error) {
      console.error('刪除任務失敗:', error);
      alert('刪除任務失敗，請稍後再試');
    }
  };

  // 文件處理函式
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type.startsWith('video/')) {
      setSelectedFile(file);
    } else {
      alert('請選擇影片檔案');
    }
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);

    const files = event.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (file.type.startsWith('video/')) {
        setSelectedFile(file);
      } else {
        alert('請選擇影片檔案');
      }
    }
  };

  const handleDragOver = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
  };

  // 模型檔案選取 (開啟檔案總管並儲存選擇)
  const handleModelFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    if (file) {
      setSelectedModelFile(file);
    }
  };

  // 確認對話框
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<() => Promise<void> | null>(null);

  const handleConfirmOpen = (taskId: string, action: () => Promise<void>) => {
    setPendingTaskId(taskId);
    setPendingAction(() => action);
    setIsConfirmOpen(true);
  };

  const handleConfirmClose = () => {
    setPendingTaskId(null);
    setPendingAction(null);
    setIsConfirmOpen(false);
  };

  const handleConfirm = async () => {
    if (pendingAction) {
      try {
        await pendingAction();
      } catch (error) {
        console.error('確認操作失敗:', error);
        alert('操作失敗，請稍後重試');
      } finally {
        handleConfirmClose();
      }
    }
  };

  // 影片上傳（被 UI 按鈕呼叫）
  const handleUpload = async () => {
    if (!selectedFile) {
      alert('請選擇影片檔案');
      return;
    }

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      const result = await uploadVideoMutation.mutateAsync(formData);
      setUploadResult(result);
      console.log('影片上傳成功:', result);
      refetchVideoList();
    } catch (error) {
      console.error('影片上傳失敗:', error);
      alert('影片上傳失敗，請稍後重試');
    }
  };

  // 模型上傳（會開啟檔案總管選擇檔案後由此函式上傳到後端）
  const handleModelUpload = async () => {
    if (!selectedModelFile) {
      alert('請選擇模型檔案');
      return;
    }

    setModelUploadPending(true);
    setModelUploadError(null);
    try {
      const formData = new FormData();
      formData.append('file', selectedModelFile);
      formData.append('model_type', selectedModelType);

      const resp = await fetch('/api/models/upload', {
        method: 'POST',
        body: formData,
      });

      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new Error(`上傳失敗: ${resp.status} ${text}`);
      }

      await resp.json().catch(() => null);
      alert('模型上傳成功');
      setSelectedModelFile(null);
      refetchModels();
    } catch (err: unknown) {
      console.error('模型上傳失敗:', err);
      setModelUploadError(String(err));
      alert('模型上傳失敗，請稍後重試');
    } finally {
      setModelUploadPending(false);
    }
  };

  const handleStartAnalysis = async (video?: VideoFileInfo) => {
    // 優先使用傳入的video參數，其次使用selectedVideo，最後使用uploadResult
    const targetVideo = video || selectedVideo;

    if (!targetVideo && !uploadResult) {
      alert('請先選擇或上傳影片檔案');
      return;
    }

    if (!selectedModel) {
      alert('請選擇要使用的模型');
      return;
    }

    try {
      // 使用新的影片分析 API，透過 FormData 傳送參數
      const formData = new FormData();

      if (targetVideo) {
        // 從影片列表中選擇的影片
        formData.append('file_path', targetVideo.file_path);
      } else if (uploadResult) {
        // 新上傳的影片
        formData.append('file_path', uploadResult.file_path);
      }

      // 新增三個必要欄位
      formData.append('task_name', `影片分析_${new Date().toLocaleString()}`);
      formData.append('model_id', selectedModel);
      formData.append('confidence_threshold', confidenceThreshold.toString());

      console.log('開始影片分析，參數:', {
        task_name: `影片分析_${new Date().toLocaleString()}`,
        model_id: selectedModel,
        confidence_threshold: confidenceThreshold,
        target_video: targetVideo?.name || uploadResult?.original_name
      });

      // 使用新的影片分析 API
      const result = await videoAnalysisMutation.mutateAsync(formData);
      console.log('影片分析已開始:', result);

      alert(`影片分析已開始執行！\n任務 ID: ${result.task_id}\n狀態: ${result.success ? '成功' : '失敗'}\n${result.message}`);

      // 重新載入影片列表以更新狀態
      refetchVideoList();

    } catch (error) {
      console.error('創建並執行分析任務失敗:', error);
      alert('創建並執行分析任務失敗，請稍後重試');
    }
  };

  // 刪除影片的處理函式
  const handleDeleteVideo = async (video: VideoFileInfo) => {
    // 確認刪除
    const confirmDelete = window.confirm(`確定要刪除影片「${video.name}」嗎？此操作無法復原。`);
    if (!confirmDelete) {
      return;
    }

    try {
      console.log('刪除影片:', video.id);

      const result = await deleteVideoMutation.mutateAsync(video.id);
      console.log('影片刪除成功:', result);

      alert(`影片「${video.name}」已成功刪除`);

      // 重新載入影片列表
      refetchVideoList();

    } catch (error) {
      console.error('刪除影片失敗:', error);
      alert('刪除影片失敗，請稍後重試');
    }
  };

  // 開始 Live Person Camera 的處理函式
  const handleStartLivePersonCamera = async () => {
    if (!selectedCamera) {
      alert('請選擇攝影機');
      return;
    }

    if (!realtimeModel) {
      alert('請選擇YOLO模型');
      return;
    }

    const hasSelectedModel = activeModels?.some((model) => model.id === realtimeModel);
    if (!hasSelectedModel) {
      alert('無法取得模型設定，請確認模型狀態');
      return;
    }

    try {
      setIsLivePersonCameraRunning(false);
      setLivePersonTaskId(null);

      const requestData: LivePersonCameraRequest = {
        task_name: `LivePerson_${new Date().toLocaleString()}`,
        camera_id: selectedCamera,
        model_id: realtimeModel,
        confidence: confidenceThreshold,
        iou_threshold: 0.45,
        description: "前端發起的 Live Person Camera 任務",
        client_stream: true,
      };

      console.log('開始 Live Person Camera 分析:', requestData);

      const result = await startLivePersonCameraMutation.mutateAsync(requestData);
      console.log('Live Person Camera 分析已開始:', result);
      setIsLivePersonCameraRunning(true);
      setLivePersonTaskId(result.task_id ?? null);
      refetchTasks();
    } catch (error) {
      console.error('開始 Live Person Camera 失敗:', error);
      setIsLivePersonCameraRunning(false);
      setLivePersonTaskId(null);
      alert('開始即時分析失敗，請稍後重試');
    }
  };

  const buildTaskAlertPayload = useCallback(
    (taskId: string): TaskAlertRulePayload[] => {
      const bindings = taskAlertBindings[taskId] ?? {};
      return Object.entries(bindings)
        .filter(([, state]) => state.enabled)
        .map(([ruleId, state]) => {
          const matchedRule = alertRules.find((rule) => rule.id === ruleId);
          if (!matchedRule) {
            return null;
          }
          return {
            id: matchedRule.id,
            rule_type: matchedRule.rule_type,
            name: matchedRule.name,
            severity: matchedRule.severity,
            actions: matchedRule.actions,
            trigger_values: matchedRule.trigger_values,
            selections: state.selections ?? [],
          };
        })
        .filter((payload): payload is TaskAlertRulePayload => Boolean(payload));
    },
    [alertRules, taskAlertBindings]
  );

  const handleOpenPreviewWindow = async (taskId: number) => {
    try {
      const taskKey = String(taskId);
      const selectedRules = buildTaskAlertPayload(taskKey);
      await saveTaskAlertsMutation.mutateAsync({ taskId: taskKey, rules: selectedRules });
      const response = await launchLivePersonPreviewMutation.mutateAsync({
        taskId: taskKey,
        alertRules: selectedRules,
      });
      const message =
        response?.message ||
        (response?.already_running
          ? "GUI 預覽已在執行。"
          : "已嘗試開啟 GUI 預覽視窗，請稍候。");
      alert(message);
    } catch (error) {
      console.error("啟動 GUI 預覽失敗:", error);
      alert("開啟 GUI 預覽失敗，請確認後端服務可顯示視窗後再試一次。");
    }
  };

  const handleStopLivePersonCamera = async () => {
    if (!livePersonTaskId) {
      alert('目前沒有運行中的即時分析');
      return;
    }

    try {
      await stopLivePersonCameraMutation.mutateAsync(livePersonTaskId);
      refetchTasks();
    } catch (error) {
      console.error('停止 Live Person Camera 失敗:', error);
      alert('停止即時分析失敗，請稍後重試');
    } finally {
      setIsLivePersonCameraRunning(false);
      setLivePersonTaskId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>檢測分析</h1>
        <div className="flex gap-2">


        </div>
      </div>

      <Tabs defaultValue="video-analysis">
        <TabsList>
          <TabsTrigger value="video-analysis">影片分析</TabsTrigger>
          <TabsTrigger value="camera-analysis">攝影機配置</TabsTrigger>
          <TabsTrigger value="task-management">任務管理</TabsTrigger>
          <TabsTrigger value="yolo-models">YOLO 模型管理</TabsTrigger>
        </TabsList>

        <TabsContent value="video-analysis">
          <div className="grid gap-6 md:grid-cols-2">
            {/* 左邊：影片上傳卡片 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Upload className="h-5 w-5" />
                  影片上傳
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label htmlFor="video-file">選擇影片檔案</Label>
                  <div
                    className={`border-2 border-dashed rounded-lg p-8 text-center mt-2 transition-colors ${isDragging ? 'border-primary bg-primary/10' : 'border-border'
                      }`}
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                  >
                    <Upload className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
                    {selectedFile ? (
                      <div className="space-y-2">
                        <p className="text-sm font-medium text-foreground">
                          已選擇: {selectedFile.name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          檔案大小: {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB
                        </p>
                        {uploadResult && (
                          <div className="mt-2 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
                            <div className="flex items-center gap-2">
                              <div className="h-2 w-2 bg-green-500 rounded-full"></div>
                              上傳成功！影片已加入列表
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div>
                        <p className="text-sm text-muted-foreground mb-2">
                          拖拽影片檔案到此處或點擊選擇
                        </p>
                        <p className="text-xs text-muted-foreground">
                          支援格式: MP4, AVI, MOV, MKV (最大 500MB)
                        </p>
                      </div>
                    )}
                    <input
                      type="file"
                      id="video-file"
                      accept="video/*"
                      onChange={handleFileSelect}
                      className="hidden"
                    />
                    <Button
                      variant="outline"
                      className="mt-4"
                      onClick={() => document.getElementById('video-file')?.click()}
                    >
                      <FileVideo className="h-4 w-4 mr-2" />
                      選擇影片檔案
                    </Button>
                  </div>
                </div>

                {/* 上傳按鈕 */}
                {selectedFile && !uploadResult ? (
                  <Button
                    onClick={handleUpload}
                    disabled={uploadVideoMutation.isPending}
                    className="w-full"
                  >
                    <Upload className="h-4 w-4 mr-2" />
                    {uploadVideoMutation.isPending ? '上傳中...' : '上傳影片'}
                  </Button>
                ) : uploadResult ? (
                  <div className="space-y-3">
                    <Button
                      className="w-full"
                      variant="outline"
                      onClick={() => {
                        setSelectedFile(null);
                        setUploadResult(null);
                        // 重置 mutation 狀態
                        uploadVideoMutation.reset();
                        // 重置檔案輸入元素
                        const fileInput = document.getElementById('video-file') as HTMLInputElement | null;
                        if (fileInput) {
                          fileInput.value = '';
                        }
                      }}
                    >
                      <Plus className="h-4 w-4 mr-2" />
                      繼續上傳更多影片
                    </Button>
                  </div>
                ) : (
                  <Button
                    className="w-full"
                    disabled={true}
                    variant="outline"
                  >
                    <Upload className="h-4 w-4 mr-2" />
                    請先選擇影片檔案
                  </Button>
                )}
              </CardContent>
            </Card>

            {/* 右邊：影片列表與分析卡片 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileVideo className="h-5 w-5" />
                    影片列表與分析
                  </div>
                  <Badge variant="secondary">
                    {videoListData?.total || 0} 個影片
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* 分析參數設置 */}
                <div className="space-y-3 p-3 bg-muted/50 rounded-lg">
                  <div>
                    <Label htmlFor="detection-model">偵測模型</Label>
                    <Select
                      value={selectedModel}
                      onValueChange={(value: string) => {
                        if (value !== "no-models") {
                          setSelectedModel(value);
                        }
                      }}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="選擇偵測模型" />
                      </SelectTrigger>
                      <SelectContent>
                        {activeModels && activeModels.length > 0 ? (
                          activeModels.map((model) => (
                            <SelectItem key={model.id} value={model.id}>
                              {model.name}
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="no-models" disabled>無可用模型</SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="confidence-threshold">信心度閾值</Label>
                      <span className="text-sm text-muted-foreground">{confidenceThreshold.toFixed(1)}</span>
                    </div>
                    <input
                      id="confidence-threshold"
                      type="range"
                      min="0.1"
                      max="1"
                      step="0.1"
                      value={confidenceThreshold}
                      onChange={(e) => setConfidenceThreshold(parseFloat(e.target.value))}
                      className="w-full"
                    />
                  </div>
                </div>

                {/* 影片列表 */}
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {videoListLoading ? (
                    <div className="text-center py-8 text-muted-foreground">
                      載入影片列表中...
                    </div>
                  ) : videoListData && videoListData.videos.length > 0 ? (
                    videoListData.videos.map((video: VideoFileInfo) => (
                      <div
                        key={video.id}
                        className="border rounded-lg p-3 hover:bg-muted/30 transition-colors cursor-pointer"
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-sm truncate">{video.name}</p>
                            <p className="text-xs text-muted-foreground">{video.upload_time}</p>
                          </div>
                          <div className="flex items-center gap-2 ml-2">
                            {video.status === 'ready' && (
                              <Badge variant="outline" className="text-xs">
                                待分析
                              </Badge>
                            )}
                            {video.status === 'analyzing' && (
                              <Badge variant="default" className="text-xs">
                                分析中
                              </Badge>
                            )}
                            {video.status === 'completed' && (
                              <Badge variant="secondary" className="text-xs">
                                已完成
                              </Badge>
                            )}
                          </div>
                        </div>

                        <div className="flex items-center justify-between text-xs text-muted-foreground mb-3">
                          <div className="flex gap-3">
                            <span>時長: {video.duration || '未知'}</span>
                            <span>大小: {video.size}</span>
                            <span>解析度: {video.resolution || '未知'}</span>
                          </div>
                        </div>

                        {video.status === 'ready' && (
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              className="flex-1"
                              disabled={!selectedModel}
                              onClick={() => handleStartAnalysis(video)}
                            >
                              <Play className="h-4 w-4 mr-2" />
                              開始分析此影片
                            </Button>
                            <Button
                              size="sm"
                              variant="destructive"
                              disabled={deleteVideoMutation.isPending}
                              onClick={() => handleDeleteVideo(video)
                              }
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        )}

                        {video.status === 'analyzing' && (
                          <div className="space-y-2">
                            <div className="flex justify-between text-xs">
                              <span>分析進度</span>
                              <span>65%</span>
                            </div>
                            <Progress value={65} className="h-2" />
                            <div className="flex justify-end">
                              <Button
                                size="sm"
                                variant="destructive"
                                disabled={deleteVideoMutation.isPending}
                                onClick={() => handleDeleteVideo(video)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </div>
                        )}

                        {video.status === 'completed' && (
                          <div className="space-y-2">
                            <div className="text-center py-2">
                              <Badge variant="secondary" className="text-xs">
                                分析已完成
                              </Badge>
                            </div>
                            <div className="flex justify-end">
                              <Button
                                size="sm"
                                variant="destructive"
                                disabled={deleteVideoMutation.isPending}
                                onClick={() => handleDeleteVideo(video)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-8 text-muted-foreground">
                      <FileVideo className="h-12 w-12 mx-auto mb-3 opacity-50" />
                      <p>暫無影片檔案</p>
                      <p className="text-xs mt-1">請先上傳影片檔案</p>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="camera-analysis">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Camera className="h-5 w-5" />
                攝影機配置
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-6 md:grid-cols-2">
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="camera-select">選擇攝影機</Label>
                    <Select value={selectedCamera} onValueChange={setSelectedCamera}>
                      <SelectTrigger>
                        <SelectValue placeholder={isCamerasLoading ? "載入攝影機列表中..." : "選擇要分析的攝影機"} />
                      </SelectTrigger>
                      <SelectContent>
                        {isCamerasLoading ? (
                          <SelectItem value="loading" disabled>
                            載入中...
                          </SelectItem>
                        ) : isCamerasError ? (
                          <SelectItem value="error" disabled>
                            載入攝影機列表失敗
                          </SelectItem>
                        ) : cameraOptions.length > 0 ? (
                          cameraOptions.map((camera, index) => (
                            <SelectItem
                              key={camera.id?.toString() ?? `${camera.name}-${index}`}
                              value={camera.id?.toString() ?? `${camera.list_index ?? index}`}
                            >
                              <div className="flex flex-col gap-0.5">
                                <span>{camera.name}</span>
                                <span className="text-xs text-muted-foreground">
                                  {camera.camera_type} • {camera.resolution} • {camera.status}
                                </span>
                              </div>
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="no-cameras" disabled>
                            沒有可用的攝影機設備
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                  </div>

                  <div>
                    <Label htmlFor="analysis-model">分析模型</Label>
                    <Select value={realtimeModel} onValueChange={setRealtimeModel}>
                      <SelectTrigger>
                        <SelectValue placeholder="選擇分析模型" />
                      </SelectTrigger>
                      <SelectContent>
                        {activeModels && activeModels.length > 0 ? (
                          activeModels.map((model) => (
                            <SelectItem key={model.id} value={model.id}>
                              {model.name}
                            </SelectItem>
                          ))
                        ) : (
                          <SelectItem value="no-models" disabled>
                            無可用模型
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-3">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="confidence-threshold-realtime">信心度閾值</Label>
                        <span className="text-sm text-muted-foreground">
                          {confidenceThreshold.toFixed(1)}
                        </span>
                      </div>
                      <input
                        id="confidence-threshold-realtime"
                        type="range"
                        min="0.1"
                        max="1"
                        step="0.1"
                        value={confidenceThreshold}
                        onChange={(e) => setConfidenceThreshold(parseFloat(e.target.value))}
                        className="w-full"
                      />
                    </div>

                    <div className="flex items-center justify-between">
                      <Label htmlFor="auto-alert">自動警報</Label>
                      <Switch id="auto-alert" />
                    </div>
                  </div>

                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Button
                      className="w-full sm:w-auto"
                      onClick={handleStartLivePersonCamera}
                      disabled={isLivePersonCameraRunning || startLivePersonCameraMutation.isPending}
                    >
                      <Activity className="h-4 w-4 mr-2" />
                      {isLivePersonCameraRunning || startLivePersonCameraMutation.isPending
                        ? "分析中..."
                        : "開始即時分析"}
                    </Button>
                  </div>
                </div>

                <div className="space-y-6">
                  <section className="space-y-3">
                    <h4 className="font-medium">即時分析任務</h4>
                    {realtimeAnalysisTasks.length === 0 ? (
                      <div className="rounded-lg border border-dashed py-10 text-center text-muted-foreground">
                        <Activity className="h-10 w-10 mx-auto mb-3 opacity-60" />
                        目前沒有運行中的即時分析任務
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {realtimeAnalysisTasks.map((task) => {
                          const taskKey = String(task.id);
                          const lineOptions = task.crossingLine?.options ?? [];
                          const zoneOptions = task.dwellArea?.options ?? [];
                          const storedLineSelection = lineSelectionByTask[taskKey];
                          const hasLineOptions = lineOptions.length > 0;
                          const activeLineSelection =
                            hasLineOptions &&
                              storedLineSelection &&
                              lineOptions.some((option) => option.id === storedLineSelection)
                              ? storedLineSelection
                              : hasLineOptions
                                ? lineOptions[0].id
                                : undefined;
                          const displayedLine =
                            hasLineOptions && activeLineSelection
                              ? lineOptions.find((option) => option.id === activeLineSelection) ??
                              task.crossingLine.summary
                              : task.crossingLine.summary;
                          const storedZoneSelection = zoneSelectionByTask[taskKey];
                          const hasZoneOptions = zoneOptions.length > 0;
                          const activeZoneSelection =
                            hasZoneOptions &&
                              storedZoneSelection &&
                              zoneOptions.some((option) => option.id === storedZoneSelection)
                              ? storedZoneSelection
                              : hasZoneOptions
                                ? zoneOptions[0].id
                                : undefined;
                          const displayedZone =
                            hasZoneOptions && activeZoneSelection
                              ? zoneOptions.find((option) => option.id === activeZoneSelection) ??
                              task.dwellArea.summary
                              : task.dwellArea.summary;

                          return (
                            <div key={task.id} className="border rounded-lg p-4 space-y-4">
                              <div className="flex justify-between items-start">
                                <div className="flex items-center gap-2">
                                  <Camera className="h-5 w-5 text-primary" />
                                  <div>
                                    <p className="font-medium">{task.camera}</p>
                                    <Badge variant={getTaskStatusColor(task.status)} className="mt-1">
                                      <Activity className="h-3 w-3 mr-1" />
                                      {getTaskStatusText(task.status)}
                                    </Badge>
                                  </div>
                                </div>
                                {task.status === "running" && (
                                  <div className="flex gap-2">
                                    <Button
                                      variant="secondary"
                                      size="sm"
                                      onClick={() => handleOpenAlertConfig(String(task.id), task.camera, task.cameraId)}
                                    >
                                      <BellRing className="h-4 w-4 mr-1" />
                                      配置警報
                                    </Button>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      disabled={
                                        launchLivePersonPreviewMutation.isPending &&
                                        launchLivePersonPreviewMutation.variables?.taskId === String(task.id)
                                      }
                                      onClick={() => {
                                        void handleOpenPreviewWindow(task.id);
                                      }}
                                    >
                                      <Monitor className="h-4 w-4 mr-2" />
                                      打開預覽
                                    </Button>
                                  </div>
                                )}
                              </div>

                              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                                <div className="space-y-1">
                                  <div className="flex items-center gap-1 text-sm text-muted-foreground">
                                    <Users className="h-4 w-4" />
                                    偵測人數
                                  </div>
                                  <p className="text-2xl">{task.detectedPeople}</p>
                                </div>

                                <div className="space-y-2">
                                  <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-1 text-sm text-muted-foreground">
                                      <ArrowLeftRight className="h-4 w-4" />
                                      穿越線
                                    </div>
                                    {lineOptions.length > 1 ? (
                                      <Select
                                        value={activeLineSelection}
                                        onValueChange={(value) =>
                                          setLineSelectionByTask((previous) => ({
                                            ...previous,
                                            [taskKey]: value,
                                          }))
                                        }
                                      >
                                        <SelectTrigger className="h-8 w-[150px] text-xs">
                                          <SelectValue placeholder="選擇線段" />
                                        </SelectTrigger>
                                        <SelectContent>
                                          {lineOptions.map((option) => (
                                            <SelectItem key={option.id} value={option.id}>
                                              {option.label}
                                            </SelectItem>
                                          ))}
                                        </SelectContent>
                                      </Select>
                                    ) : lineOptions.length === 1 ? (
                                      <Badge variant="outline" className="text-xs font-normal">
                                        {lineOptions[0].label}
                                      </Badge>
                                    ) : null}
                                  </div>
                                  <div className="flex items-baseline gap-2">
                                    <span className="text-sm text-muted-foreground">進:</span>
                                    <span className="text-lg">{displayedLine.in}</span>
                                    <span className="text-sm text-muted-foreground">出:</span>
                                    <span className="text-lg">{displayedLine.out}</span>
                                  </div>
                                </div>

                                <div className="space-y-2">
                                  <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-1 text-sm text-muted-foreground">
                                      <Timer className="h-4 w-4" />
                                      停留區
                                    </div>
                                    {zoneOptions.length > 1 ? (
                                      <Select
                                        value={activeZoneSelection}
                                        onValueChange={(value) =>
                                          setZoneSelectionByTask((previous) => ({
                                            ...previous,
                                            [taskKey]: value,
                                          }))
                                        }
                                      >
                                        <SelectTrigger className="h-8 w-[150px] text-xs">
                                          <SelectValue placeholder="選擇區域" />
                                        </SelectTrigger>
                                        <SelectContent>
                                          {zoneOptions.map((option) => (
                                            <SelectItem key={option.id} value={option.id}>
                                              {option.label}
                                            </SelectItem>
                                          ))}
                                        </SelectContent>
                                      </Select>
                                    ) : zoneOptions.length === 1 ? (
                                      <Badge variant="outline" className="text-xs font-normal">
                                        {zoneOptions[0].label}
                                      </Badge>
                                    ) : null}
                                  </div>
                                  <div className="space-y-0.5">
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">目前:</span>
                                      <span>{displayedZone.current}人</span>
                                    </div>
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">平均:</span>
                                      <span>{displayedZone.avgDuration}</span>
                                    </div>
                                    <div className="flex justify-between text-xs">
                                      <span className="text-muted-foreground">最長:</span>
                                      <span>{displayedZone.maxDuration}</span>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </section>

                  <Dialog open={isAlertConfigOpen} onOpenChange={setIsAlertConfigOpen}>
                    <DialogContent className="max-w-2xl">
                      <DialogHeader>
                        <DialogTitle>配置警報</DialogTitle>
                        {alertConfigTask && (
                          <p className="text-sm text-muted-foreground">套用對象：{alertConfigTask.name}</p>
                        )}
                      </DialogHeader>
                      <div className="space-y-4">
                        {alertRulesForDialog.length === 0 ? (
                          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                            尚未建立符合此攝影機的警報規則，請先到「警報管理」新增規則。
                          </div>
                        ) : (
                          alertRulesForDialog.map(({ rule, displayName, description }) => {
                            const taskId = alertConfigTask?.id ?? "";
                            const taskBindings = taskAlertBindings[taskId] ?? {};
                            const ruleState = taskBindings[rule.id];
                            const selectionItems =
                              rule.rule_type === "lineCrossing"
                                ? taskRegionData?.lines ?? []
                                : rule.rule_type === "zoneDwell"
                                  ? taskRegionData?.zones ?? []
                                  : [];
                            const requiresGeometry =
                              rule.rule_type === "lineCrossing" || rule.rule_type === "zoneDwell";
                            const selectionLabel =
                              rule.rule_type === "lineCrossing" ? "選擇線段" : "選擇區域";
                            const geometryLoading = requiresGeometry && isTaskRegionsLoading;
                            const selectionUnavailable =
                              requiresGeometry && !geometryLoading && selectionItems.length === 0;
                            const disableSwitch = requiresGeometry && (geometryLoading || selectionUnavailable);
                            return (
                              <div key={rule.id} className="rounded-lg border p-4 space-y-3">
                                <div className="flex items-start justify-between">
                                  <div>
                                    <p className="font-medium">{displayName}</p>
                                    {description && (
                                      <p className="text-sm text-muted-foreground">{description}</p>
                                    )}
                                  </div>
                                  <Switch
                                    checked={ruleState?.enabled ?? false}
                                    disabled={disableSwitch}
                                    onCheckedChange={(checked) => {
                                      if (!alertConfigTask) {
                                        return;
                                      }
                                      handleToggleAlertRule(alertConfigTask.id, rule, checked, selectionItems);
                                    }}
                                  />
                                </div>
                                {geometryLoading && (
                                  <p className="text-xs text-muted-foreground">載入線段/區域資訊中...</p>
                                )}
                                {selectionUnavailable && (
                                  <p className="text-xs text-yellow-600">此任務尚未設定對應的線段或區域，請先於即時分析介面繪製。</p>
                                )}
                                {ruleState?.enabled && selectionItems.length > 0 && (
                                  <div className="rounded-md border bg-muted/30 p-3 space-y-2">
                                    <p className="text-xs text-muted-foreground">{selectionLabel}</p>
                                    {selectionItems.map((item, index) => {
                                      const itemId =
                                        item.id ||
                                        `${rule.id}-${rule.rule_type === "lineCrossing" ? "line" : "zone"}-${index + 1
                                        }`;
                                      const label =
                                        item.label ||
                                        (rule.rule_type === "lineCrossing" ? `Line ${index + 1}` : `Zone ${index + 1}`);
                                      const checked = ruleState.selections?.includes(itemId) ?? true;
                                      return (
                                        <label key={itemId} className="flex items-center gap-2 text-sm">
                                          <Checkbox
                                            checked={checked}
                                            onCheckedChange={(checkedValue) => {
                                              if (!alertConfigTask) {
                                                return;
                                              }
                                              handleToggleAlertItem(
                                                alertConfigTask.id,
                                                rule.id,
                                                itemId,
                                                Boolean(checkedValue)
                                              );
                                            }}
                                          />
                                          <span>{label}</span>
                                        </label>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            );
                          })
                        )}
                      </div>
                      <div className="flex justify-end gap-2 pt-4">
                        <Button variant="outline" onClick={() => setIsAlertConfigOpen(false)}>
                          取消
                        </Button>
                        <Button onClick={handleSaveAlertConfig}>儲存設定</Button>
                      </div>
                    </DialogContent>
                  </Dialog>

                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task-management">
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h3>進行中的分析任務</h3>
              <Badge variant="outline">
                {runningTasks.length} 個任務運行中
              </Badge>
            </div>

            {runningTasks.length === 0 ? (
              <Card>
                <CardContent className="text-center py-8">
                  <Activity className="h-12 w-12 mx-auto mb-4 text-muted-foreground opacity-50" />
                  <p className="text-muted-foreground">目前沒有進行中的分析任務</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    前往「攝影機配置」或「影片分析」標籤頁開始新的分析任務
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-4">
                {runningTasks.map((task) => (
                  <Card key={task.id}>
                    <CardHeader>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-2">
                            {task.task_type === 'realtime_camera' ? (
                              <Camera className="h-5 w-5" />
                            ) : (
                              <FileVideo className="h-5 w-5" />
                            )}
                            <div>
                              <CardTitle className="text-base">
                                {task.task_name || `任務 #${task.id}`}
                              </CardTitle>
                              <p className="text-sm text-muted-foreground">
                                模型: {task.model_id || 'yolo11n.pt'}
                              </p>
                            </div>
                          </div>
                        </div>
                        <Badge variant={getTaskStatusColor(task.status)}>
                          {getTaskStatusText(task.status)}
                        </Badge>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="grid gap-4 md:grid-cols-4 mb-4">
                        <div>
                          <p className="text-sm text-muted-foreground">開始時間</p>
                          <div className="flex items-center gap-1 mt-1">
                            <Clock className="h-3 w-3 text-muted-foreground" />
                            <span className="text-sm">
                              {task.start_time ? formatUTCTime(task.start_time) : '未開始'}
                            </span>
                          </div>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">任務類型</p>
                          <p className="text-sm font-medium">
                            {task.task_type === 'realtime_camera' ? '即時攝影機' : '影片分析'}
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">來源解析度</p>
                          <p className="text-lg font-medium">
                            {task.source_width && task.source_height ?
                              `${task.source_width}x${task.source_height}` :
                              '未知'
                            }
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">信心度閾值</p>
                          <p className="text-lg font-medium">
                            {task.confidence_threshold ?
                              `${Math.round(task.confidence_threshold * 100)}%` :
                              '50%'
                            }
                          </p>
                        </div>
                      </div>

                      <div className="flex justify-end gap-2">
                        {/* 暫停/恢復按鈕 - 只對運行中或暫停的任務顯示 */}
                        {(task.status === 'running' || task.status === 'paused') && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => toggleTaskStatus(task.id.toString())}
                          >
                            {task.status === 'running' ? (
                              <>
                                <Square className="h-4 w-4 mr-2" />
                                暫停
                              </>
                            ) : (
                              <>
                                <Play className="h-4 w-4 mr-2" />
                                恢復
                              </>
                            )}
                          </Button>
                        )}

                        {/* 停止按鈕 - 只對運行中或暫停的任務顯示 */}
                        {(task.status === 'running' || task.status === 'paused') && (
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => stopTask(task.id.toString())}
                            disabled={stopTaskMutation.isPending}
                          >
                            <Square className="h-4 w-4 mr-2" />
                            {stopTaskMutation.isPending ? '停止中...' : '停止'}
                          </Button>
                        )}

                        {/* 刪除按鈕 - 只對非運行狀態的任務顯示 */}
                        {task.status !== 'running' && (
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => deleteTask(task.id.toString(), task.task_name || `任務 #${task.id}`)}
                            disabled={deleteTaskMutation.isPending}
                          >
                            <Trash2 className="h-4 w-4 mr-2" />
                            {deleteTaskMutation.isPending ? '刪除中...' : '刪除'}
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}

            <Card>
              <CardHeader>
                <CardTitle>任務統計</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-4">
                  <div className="text-center">
                    <p className="text-2xl font-bold text-green-600">
                      {runningTasks.filter(t => t.status === 'running').length}
                    </p>
                    <p className="text-sm text-muted-foreground">運行中</p>
                  </div>
                  <div className="text-center">
                    <p className="text-2xl font-bold text-yellow-600">
                      {runningTasks.filter(t => t.status === 'paused').length}
                    </p>
                    <p className="text-sm text-muted-foreground">已暫停</p>
                  </div>
                  <div className="text-center">
                    <p className="text-2xl font-bold text-blue-600">
                      {runningTasks.filter(t => t.status === 'completed').length}
                    </p>
                    <p className="text-sm text-muted-foreground">已完成</p>
                  </div>
                  <div className="text-center">
                    <p className="text-2xl font-bold text-purple-600">
                      {runningTasks.length > 0
                        ? Math.round(runningTasks.reduce((sum, task) => sum + (task.source_fps || 30), 0) / runningTasks.length)
                        : 0}
                    </p>
                    <p className="text-sm text-muted-foreground">平均來源 FPS</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>


        <TabsContent value="yolo-models">
          <div className="space-y-6">
            {modelsLoading && (
              <div className="text-center py-8">
                <p>載入模型中...</p>
              </div>
            )}

            {modelsError && (
              <div className="text-center py-8 text-red-500">
                <p>載入錯誤: {modelsError.message}</p>
                <Button onClick={() => refetchModels()} className="mt-2">
                  重新載入
                </Button>
              </div>
            )}

            {yoloModels && (
              <div className="grid gap-4">
                {yoloModels.map((model) => {
                  const isActive = isModelActive(model.id);
                  return (
                    <Card key={model.id}>
                      <CardHeader>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <Brain className="h-5 w-5" />
                            <div>
                              <CardTitle>{model.name}</CardTitle>
                            </div>
                          </div>
                          <Badge variant={getModelStatusColor(isActive)}>
                            {getModelStatusText(isActive)}
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent>
                        <div className="grid gap-4 md:grid-cols-4">
                          <div>
                            <p className="text-sm text-muted-foreground">模型類型</p>
                            <div className="flex items-center gap-2 mt-1">
                              <Badge variant="outline">{model.modelType}</Badge>
                            </div>
                          </div>
                          <div>
                            <p className="text-sm text-muted-foreground">參數量</p>
                            <p className="text-xl">{model.parameterCount}</p>
                          </div>
                          <div>
                            <p className="text-sm text-muted-foreground">文件大小</p>
                            <p className="text-xl">{model.fileSize}</p>
                          </div>
                          <div>
                            <p className="text-sm text-muted-foreground">啟用狀態</p>
                            <div className="flex items-center gap-2 mt-1">
                              {isActive && (
                                <Zap className="h-4 w-4 text-green-500" />
                              )}
                              <span className="text-sm">{getModelStatusText(isActive)}</span>
                            </div>
                          </div>
                        </div>

                        <div className="flex justify-end gap-2 mt-6">
                          <Button
                            size="sm"
                            variant={isActive ? "outline" : "default"}
                            onClick={() => handleToggleModelStatus(model.id)}
                            disabled={toggleModelMutation.isPending}
                          >
                            {toggleModelMutation.isPending ? "處理中..." : (isActive ? "停用" : "啟用")}
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}

            {/* 模型上傳卡片 */}
            <Card>
              <CardHeader>
                <div className="flex items-center gap-3">
                  <Upload className="h-5 w-5" />
                  <div>
                    <CardTitle>模型上傳</CardTitle>
                    <p className="text-sm text-muted-foreground">
                      上傳自定義 YOLO 模型文件
                    </p>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">


                  <div>
                    <Label>模型文件</Label>
                    <div className="border-2 border-dashed border-border rounded-lg p-6 text-center mt-2 hover:border-primary/50 transition-colors">
                      <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
                      <p className="text-sm text-muted-foreground mb-2">拖拽或選擇模型檔案</p>
                      <p className="text-xs text-muted-foreground mb-3">支援格式: .pt, .onnx, .engine (最大 500MB)</p>

                      {/* 隱藏 input，按鈕觸發開啟檔案總管 */}
                      <input
                        id="model-file"
                        type="file"
                        accept=".pt,.onnx,.engine"
                        onChange={handleModelFileSelect}
                        className="hidden"
                      />
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => document.getElementById("model-file")?.click()}
                        >
                          選擇文件
                        </Button>
                        <div>
                          <Select value={selectedModelType} onValueChange={setSelectedModelType}>
                            <SelectTrigger className="h-8">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="detection">物體偵測</SelectItem>
                              <SelectItem value="classification">分類</SelectItem>
                              <SelectItem value="segmentation">語義分割</SelectItem>
                              <SelectItem value="pose">姿態估計</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>

                      {selectedModelFile && (
                        <div className="mt-3 text-sm">
                          已選擇: {selectedModelFile.name} ({(selectedModelFile.size / (1024 * 1024)).toFixed(2)} MB)
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex justify-end gap-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSelectedModelFile(null);
                        setModelUploadError(null);
                      }}
                      disabled={modelUploadPending}
                    >
                      取消
                    </Button>
                    <Button onClick={handleModelUpload} disabled={modelUploadPending || !selectedModelFile}>
                      <Upload className="h-4 w-4 mr-2" />
                      {modelUploadPending ? '上傳中...' : '上傳模型'}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>


          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}



