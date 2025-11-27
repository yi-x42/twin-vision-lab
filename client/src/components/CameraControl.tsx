import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import {
  useScanCameras,
  useDeleteCamera,
  useCameras,
  useToggleCamera,
  CameraInfo,
  RegisteredCameraInfo,
} from "../hooks/react-query-hooks";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Switch } from "./ui/switch";
import { Progress } from "./ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import {
  Camera,
  Settings,
  Play,
  Pause,
  RotateCw,
  ZoomIn,
  ZoomOut,
  Plus,
  Edit,
  Trash2,
  MonitorPlay,
  MapPin,
  Search,
  Loader2,
} from "lucide-react";

type ScanResultItem = {
  id: string;
  name: string;
  deviceIndex?: number;
  resolution: string;
  fps: number;
  details?: string;
  status?: "success" | "error";
};

type CameraWithMeta = CameraInfo & {
  device_id?: string;
  device_label?: string;
  list_index?: number;
};

interface VideoStreamProps {
  camera: CameraWithMeta | null;
  availableDevices: MediaDeviceInfo[];
  onDevicesUpdated?: (devices: MediaDeviceInfo[]) => void;
  active: boolean;
}

function VideoStream({ camera, availableDevices, onDevicesUpdated, active }: VideoStreamProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");

  useEffect(() => {
    let cancelled = false;

    const clearVideo = () => {
      const video = videoRef.current;
      if (video) {
        video.srcObject = null;
      }
    };

    const stopTracks = () => {
      const current = streamRef.current;
      if (current) {
        current.getTracks().forEach((track) => {
          try {
            track.stop();
          } catch {
            /* ignore */
          }
        });
        streamRef.current = null;
      }
      const video = videoRef.current;
      if (video && video.srcObject) {
        video.srcObject = null;
      }
    };

    if (!camera) {
      stopTracks();
      clearVideo();
      setStatusMessage("選擇攝影機以檢視即時影像");
      return () => {
        cancelled = true;
        stopTracks();
        clearVideo();
      };
    }

    if (!active) {
      stopTracks();
      clearVideo();
      setStatusMessage("預覽暫停");
      return () => {
        cancelled = true;
        stopTracks();
        clearVideo();
      };
    }

    const startPreview = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatusMessage("瀏覽器不支援攝影機存取");
        return;
      }

      setStatusMessage("開啟本機攝影機中…");

      try {
        let constraints: MediaStreamConstraints = {
          video: true,
          audio: false,
        };

        let devicesList = availableDevices;

        if (navigator.mediaDevices.enumerateDevices) {
          try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            devicesList = devices.filter((device) => device.kind === "videoinput");
            if (!cancelled) {
              onDevicesUpdated?.(devicesList);
            }
          } catch (err) {
            console.warn("enumerateDevices failed:", err);
          }
        }

        const numericIndex =
          typeof camera?.device_index === "number" && Number.isFinite(camera.device_index)
            ? Math.trunc(camera.device_index)
            : undefined;
        const listIndex =
          typeof camera?.list_index === "number" && Number.isFinite(camera.list_index)
            ? Math.trunc(camera.list_index)
            : undefined;

        let targetDeviceId = camera?.device_id;
        let targetDeviceLabel = camera?.device_label;

        if (devicesList.length) {
          let targetDevice =
            (targetDeviceId && devicesList.find((d) => d.deviceId === targetDeviceId)) ||
            undefined;

          const candidateIndices: number[] = [];
          if (numericIndex !== undefined) {
            candidateIndices.push(numericIndex);
          }
          if (listIndex !== undefined) {
            candidateIndices.push(listIndex);
          }
          if (!candidateIndices.length) {
            candidateIndices.push(0);
          }

          for (const candidate of candidateIndices) {
            if (targetDevice) {
              break;
            }
            const bounded =
              ((candidate % devicesList.length) + devicesList.length) %
              devicesList.length;
            targetDevice = devicesList[bounded];
          }

          if (targetDevice) {
            targetDeviceId = targetDevice.deviceId;
            targetDeviceLabel = targetDevice.label || targetDevice.deviceId;
          }
        }

        if (targetDeviceId) {
          constraints = {
            video: {
              deviceId: { exact: targetDeviceId },
            },
            audio: false,
          };
        }

        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        stopTracks();
        streamRef.current = stream;

        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          if (typeof video.play === "function") {
            video.play().catch(() => {
              /* autoplay blocked */
            });
          }
        }
        setStatusMessage("");

        if (navigator.mediaDevices.enumerateDevices) {
          try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const videos = devices.filter((device) => device.kind === "videoinput");
            if (!cancelled) {
              onDevicesUpdated?.(videos);
            }
          } catch {
            /* ignore */
          }
        }
      } catch (err) {
        console.error("取得本機攝影機影像失敗:", err);
        stopTracks();
        clearVideo();
        const message =
          err instanceof DOMException && err.name === "NotAllowedError"
            ? "瀏覽器未允許使用攝影機"
            : "無法開啟本機攝影機";
        setStatusMessage(message);
      }
    };

    startPreview();

    return () => {
      cancelled = true;
      stopTracks();
      clearVideo();
    };
  }, [
    camera?.id,
    camera?.device_index,
    camera?.device_id,
    camera?.list_index,
    availableDevices,
    onDevicesUpdated,
    active,
  ]);

  if (!camera) {
    return (
      <div className="text-center text-white h-full flex items-center justify-center">
        <div>
          <Camera className="h-12 w-12 mx-auto mb-2 opacity-50" />
          <p>選擇攝影機以檢視即時影像</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full">
      <video
        ref={videoRef}
        className="w-full h-full object-cover rounded-lg bg-black"
        autoPlay
        playsInline
        muted
      />

      {statusMessage && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/60 text-white">
          {/中|開啟|啟動/.test(statusMessage) ? (
            <Loader2 className="h-10 w-10 animate-spin opacity-70" />
          ) : (
            <Camera className="h-10 w-10 opacity-70" />
          )}
          <p className="text-sm">{statusMessage}</p>
          <p className="text-xs opacity-70">{camera.name}</p>
        </div>
      )}

      <div className="absolute top-2 left-2 bg-black/70 text-white px-3 py-1 rounded-md text-sm flex items-center gap-2">
        <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
        <span>{camera.name}</span>
        <Badge variant="secondary" className="text-xs">
          {camera.resolution} • {camera.fps} FPS
        </Badge>
      </div>

      <div className="absolute bottom-2 left-2 bg-black/60 text-white px-3 py-1 rounded-md text-xs space-y-1">
        <div>狀態: {(camera.status === "active" || camera.status === "online") ? "啟用" : "停用"}</div>
        <div>類型: {camera.camera_type}</div>
        {camera.ip && <div>IP: {camera.ip}</div>}
        {camera.model && <div>型號: {camera.model}</div>}
      </div>
    </div>
  );
}


export function CameraControl() {
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState(0);
  const [scanResults, setScanResults] = useState<ScanResultItem[]>([]);
  const [showScanResults, setShowScanResults] = useState(false);
  const [scanConfig, setScanConfig] = useState({
    ipRange: "192.168.1.1-192.168.1.254",
    ports: "80,554,8080",
    timeout: "10",
    onvifScan: true,
    rtspScan: true
  });
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [editingCamera, setEditingCamera] = useState<any>(null);
  const [editCameraName, setEditCameraName] = useState("");
  const [editCameraLocation, setEditCameraLocation] = useState("");
  const [activeTab, setActiveTab] = useState("device-list");
  const [videoDevices, setVideoDevices] = useState<MediaDeviceInfo[]>([]);
  const [pageVisible, setPageVisible] = useState(true);
  const previewContainerRef = useRef<HTMLDivElement | null>(null);
  const [previewInView, setPreviewInView] = useState(true);
  const [pageActive, setPageActive] = useState(true);
  const previewPauseMessage =
    !pageActive
      ? "目前不在攝影機控制中心，預覽已暫停"
      : activeTab !== "live-view"
        ? "回到「即時監控」頁面即可再次啟動預覽"
        : !pageVisible
          ? "頁面不在前景，預覽已暫停"
          : !previewInView
            ? "預覽區域不在視窗內，捲動回來即可恢復"
            : null;

  useEffect(() => {
    const handleVisibility = () => {
      setPageVisible(!document.hidden);
    };
    const handlePageHide = () => {
      setPageVisible(false);
    };
    window.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      window.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
    };
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.__APP_CURRENT_PAGE) {
      setPageActive(window.__APP_CURRENT_PAGE === "camera-control");
    }
    const handler = (event: CustomEvent<{ page: string }>) => {
      setPageActive(event.detail?.page === "camera-control");
    };
    window.addEventListener("app:page-change", handler);
    return () => {
      window.removeEventListener("app:page-change", handler);
    };
  }, []);

  useEffect(() => {
    const container = previewContainerRef.current;
    if (!container || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        setPreviewInView(entry?.isIntersecting ?? true);
      },
      { threshold: 0.1 }
    );
    observer.observe(container);
    return () => {
      observer.disconnect();
    };
  }, [previewContainerRef]);

  const handleDevicesUpdated = useCallback((devices: MediaDeviceInfo[]) => {
    const videoOnly = devices.filter((device) => device.kind === "videoinput");
    setVideoDevices((prev) => {
      const prevIds = prev.map((d) => d.deviceId);
      const nextIds = videoOnly.map((d) => d.deviceId);
      if (
        prevIds.length === nextIds.length &&
        prevIds.every((id, idx) => id === nextIds[idx])
      ) {
        return prev;
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

  // 使用真實的攝影機數據從後端API
  const { data: rawCameras = [], isLoading: camerasLoading, refetch: refetchCameras } = useCameras();
  const toggleCameraMutation = useToggleCamera();

  useEffect(() => {
    if (!rawCameras.length) {
      if (selectedCamera !== null) {
        setSelectedCamera(null);
      }
      return;
    }

    const normalizedSelected = selectedCamera ?? "";
    const hasSelected = rawCameras.some((camera) => camera.id?.toString() === normalizedSelected);

    if (!hasSelected) {
      const firstId = rawCameras[0].id?.toString();
      if (firstId) {
        setSelectedCamera(firstId);
      }
    }
  }, [selectedCamera, rawCameras]);

  const newlyRegisteredCount = scanResults.filter((item) => item.status !== "error").length;

  const cameras: CameraWithMeta[] = rawCameras.map((camera, index) => {
    const rawDeviceIndex = camera.device_index;
    let numericIndex: number | undefined;
    let explicitDeviceId: string | undefined;

    if (typeof rawDeviceIndex === "number" && Number.isFinite(rawDeviceIndex)) {
      numericIndex = Math.trunc(rawDeviceIndex);
    } else if (typeof rawDeviceIndex === "string") {
      const parsed = Number(rawDeviceIndex);
      if (Number.isFinite(parsed)) {
        numericIndex = Math.trunc(parsed);
      } else {
        explicitDeviceId = rawDeviceIndex;
      }
    }

    // 移除 fallback 到列表索引的邏輯，避免混淆
    // if (numericIndex === undefined) {
    //   numericIndex = index;
    // }

    let targetDevice: MediaDeviceInfo | undefined;
    if (videoDevices.length && numericIndex !== undefined) {
      // 使用嚴格匹配，不要用取模運算 (modulo)
      // 如果設備索引超出範圍，就不顯示影像，而不是顯示錯誤的影像
      if (numericIndex >= 0 && numericIndex < videoDevices.length) {
        targetDevice = videoDevices[numericIndex];
      }
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
    } as CameraWithMeta;
  });

  const getStatusColor = (status: string) => {
    switch (status) {
      case "online":
        return "default";
      case "offline":
        return "destructive";
      case "warning":
        return "secondary";
      default:
        return "outline";
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case "online":
      case "active":
        return "線上";
      case "offline":
      case "inactive":
        return "離線";
      case "warning":
        return "警告";
      default:
        return "未知";
    }
  };

  // 使用真實的攝影機掃描 API
  const scanCamerasMutation = useScanCameras();

  // 刪除攝影機的 mutation
  const deleteCameraMutation = useDeleteCamera();

  const startScan = async () => {
    setIsScanning(true);
    setScanProgress(0);
    setShowScanResults(false);
    setScanResults([]);

    // 模擬掃描進度動畫（保持 UI 效果）
    const progressAnimation = async () => {
      const steps = [0, 15, 30, 45, 60, 75, 90];
      for (const progress of steps) {
        setScanProgress(progress);
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    };

    // 同時執行進度動畫和真實掃描
    const progressPromise = progressAnimation();

    try {
      // 呼叫真實的攝影機掃描 API
      const scanResult = await scanCamerasMutation.mutateAsync({
        register_new: true,
      });

      // 將後端回傳的資料轉換為前端需要的格式
      const registeredDevices = scanResult.registered ?? [];
      const formattedResults = registeredDevices.map((device: RegisteredCameraInfo) => ({
        id: device.id,
        name: device.name || `攝影機 ${device.device_index}`,
        deviceIndex: device.device_index,
        resolution: device.resolution || "未知",
        fps: device.fps || 0,
        details: "已自動加入設備列表",
        status: "success",
      }));

      // 等待進度動畫完成
      await progressPromise;
      setScanProgress(100);

      // 顯示掃描結果
      setScanResults(formattedResults);
      await refetchCameras();
      setShowScanResults(true);

    } catch (error) {
      console.error('攝影機掃描失敗:', error);

      // 即使失敗也要完成進度動畫
      await progressPromise;
      setScanProgress(100);

      // 顯示錯誤訊息
      setScanResults([{
        id: "error",
        name: "掃描失敗",
        resolution: "未知",
        fps: 0,
        details: `錯誤訊息: ${error instanceof Error ? error.message : "未知錯誤"}`,
        status: "error",
      }]);
      setShowScanResults(true);
    } finally {
      setIsScanning(false);
    }
  };

  // 切換攝影機狀態
  const handleCameraToggle = async (cameraId: string) => {
    try {
      await toggleCameraMutation.mutateAsync(cameraId);
      // 重新取得攝影機列表以更新狀態
      refetchCameras();
      // 如果是當前選中的攝影機，也重新載入串流資訊
      if (cameraId === selectedCamera) {
        // 串流資訊會自動重新載入，因為我們有dependency在攝影機狀態上
      }
    } catch (error) {
      console.error('切換攝影機狀態失敗:', error);
      alert('切換攝影機狀態失敗，請稍後再試');
    }
  };

  // 開啟編輯對話框
  const openEditDialog = (camera: any) => {
    setEditingCamera(camera);
    setEditCameraName(camera.name);
    setEditCameraLocation(camera.location);
    setIsEditDialogOpen(true);
  };

  // 儲存攝影機編輯
  const saveEditCamera = async () => {
    if (editingCamera) {
      // TODO: 實現攝影機編輯API調用
      console.log("編輯攝影機功能待實現");
      setIsEditDialogOpen(false);
      setEditingCamera(null);
      setEditCameraName("");
      setEditCameraLocation("");
    }
  };

  // 開始即時預覽
  const startLivePreview = (cameraId: string | number | null | undefined) => {
    if (cameraId === null || cameraId === undefined) {
      setSelectedCamera(null);
    } else {
      setSelectedCamera(cameraId.toString());
    }
    setActiveTab("live-view");
  };

  // 刪除攝影機
  const handleDeleteCamera = async (cameraId: string) => {
    if (window.confirm('確定要刪除這個攝影機配置嗎？')) {
      try {
        await deleteCameraMutation.mutateAsync(cameraId);
        // 重新取得攝影機列表
        refetchCameras();
        // 如果刪除的是當前選中的攝影機，清除選擇
        if (selectedCamera === cameraId || selectedCamera === cameraId.toString()) {
          setSelectedCamera(null);
        }
      } catch (error) {
        console.error('刪除攝影機失敗:', error);
        alert('刪除攝影機失敗，請稍後再試');
      }
    }
  };

  // 取得選中的攝影機資料
  const selectedCameraData: CameraWithMeta | null =
    (selectedCamera ? cameras.find((cam) => cam.id?.toString() === selectedCamera) : null) ?? null;

  const liveStreamActive =
    pageActive && activeTab === "live-view" && pageVisible && previewInView;
  const liveViewStatus = selectedCameraData
    ? liveStreamActive
      ? "online"
      : selectedCameraData.status
    : undefined;

  // 取得攝影機串流資訊
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>攝影機控制中心</h1>
        <Button
          onClick={() => {
            setIsAddDialogOpen(true);
            startScan();
          }}
        >
          <Search className="h-4 w-4 mr-2" />
          自動掃描
        </Button>
        <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>自動掃描</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 max-w-4xl">
              {isScanning ? (
                <div className="space-y-4 text-center py-8">
                  <div className="flex items-center justify-center gap-2">
                    <Loader2 className="h-6 w-6 animate-spin" />
                    <h3>正在掃描網絡設備...</h3>
                  </div>
                  <Progress value={scanProgress} className="w-full max-w-md mx-auto" />
                  <p className="text-sm text-muted-foreground">掃描進度: {scanProgress}%</p>
                  <p className="text-xs text-muted-foreground">
                    掃描範圍: {scanConfig.ipRange} | 端口: {scanConfig.ports}
                  </p>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setIsScanning(false);
                      setScanProgress(0);
                      setShowScanResults(false);
                    }}
                    className="mt-4"
                  >
                    取消
                  </Button>
                </div>
              ) : showScanResults ? (
                <>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h3>掃描結果</h3>
                      <Badge variant="outline">{newlyRegisteredCount} 個新設備</Badge>
                    </div>

                    <div className="max-h-96 overflow-auto border rounded-lg">
                      <div className="space-y-3 p-4">
                        {scanResults.length === 0 ? (
                          <div className="text-center text-sm text-muted-foreground py-10">
                            沒有新的設備需要新增
                          </div>
                        ) : (
                          scanResults.map((device) => (
                            <div key={device.id} className="border rounded-lg p-4 space-y-3">
                              <div className="flex items-start justify-between">
                                <div className="space-y-1 flex-1">
                                  <div className="flex items-center gap-2">
                                    <h4 className="text-sm">{device.name}</h4>
                                    <Badge variant="secondary" className="text-xs">
                                      {device.deviceIndex !== undefined
                                        ? `本機攝影機 #${device.deviceIndex}`
                                        : "未知設備"}
                                    </Badge>
                                  </div>
                                  <p className="text-sm text-muted-foreground">
                                    解析度: {device.resolution} | FPS: {device.fps ? device.fps : "未知"}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {device.details ?? "已自動加入設備列表"}
                                  </p>
                                </div>
                                {device.status === "error" ? (
                                  <Badge variant="destructive" className="text-xs">
                                    失敗
                                  </Badge>
                                ) : (
                                  <Badge variant="default" className="text-xs">
                                    已新增
                                  </Badge>
                                )}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-end gap-2 pt-4">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setShowScanResults(false);
                        setScanResults([]);
                        startScan();
                      }}
                    >
                      重新掃描
                    </Button>
                    <Button onClick={() => setIsAddDialogOpen(false)}>
                      完成
                    </Button>
                  </div>
                </>
              ) : null}
            </div>
          </DialogContent>
        </Dialog>

        {/* 編輯攝影機對話框 */}
        <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>編輯攝影機</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="edit-camera-name">攝影機名稱</Label>
                <Input
                  id="edit-camera-name"
                  value={editCameraName}
                  onChange={(e) => setEditCameraName(e.target.value)}
                  placeholder="輸入攝影機名稱"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-camera-location">安裝位置</Label>
                <Input
                  id="edit-camera-location"
                  value={editCameraLocation}
                  onChange={(e) => setEditCameraLocation(e.target.value)}
                  placeholder="輸入安裝位置"
                />
              </div>
              <div className="flex justify-end gap-2 pt-4">
                <Button variant="outline" onClick={() => setIsEditDialogOpen(false)}>
                  取消
                </Button>
                <Button onClick={saveEditCamera}>
                  儲存
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="device-list">設備列表</TabsTrigger>
          <TabsTrigger value="live-view">即時監控</TabsTrigger>

        </TabsList>

        <TabsContent value="device-list">
          <div className="grid gap-4">
            {cameras.map((camera) => (
              <Card key={camera.id}>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Camera className="h-5 w-5" />
                      <div>
                        <CardTitle>{camera.name}</CardTitle>
                        <p className="text-sm text-muted-foreground flex items-center gap-1">
                          <MapPin className="h-3 w-3" />
                          {camera.location}
                        </p>
                      </div>
                    </div>
                    <Badge variant={getStatusColor(camera.status)}>
                      {getStatusText(camera.status)}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                    <div>
                      <p className="text-sm text-muted-foreground">解析度</p>
                      <p>{camera.resolution}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">幀率</p>
                      <p>{camera.fps} FPS</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">IP地址</p>
                      <p>{camera.ip}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">型號</p>
                      <p>{camera.model}</p>
                    </div>
                  </div>
                  <div className="flex items-center justify-between mt-4">
                    <div className="flex items-center gap-4">



                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" onClick={() => openEditDialog(camera)}>
                        <Edit className="h-4 w-4" />
                      </Button>

                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDeleteCamera(camera.id)}
                        disabled={deleteCameraMutation.isPending}
                        className="text-destructive hover:bg-destructive hover:text-destructive-foreground"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>

                      <Button variant="outline" size="sm" onClick={() => startLivePreview(camera.id)}>
                        <MonitorPlay className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="live-view">
          <div className="grid gap-6 lg:grid-cols-3">
            {/* 即時影像區域 */}
            <Card className="lg:col-span-2">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>即時影像串流</CardTitle>
                  {selectedCameraData && (
                    <div className="flex items-center gap-2">
                      <Badge variant={getStatusColor(liveViewStatus || "offline")}>
                        {getStatusText(liveViewStatus || "offline")}
                      </Badge>
                      <Badge variant="outline">{selectedCameraData.resolution}</Badge>
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <div
                  ref={previewContainerRef}
                  className="aspect-video bg-black rounded-lg overflow-hidden relative"
                >
                  <VideoStream
                    camera={selectedCameraData}
                    availableDevices={videoDevices}
                    onDevicesUpdated={handleDevicesUpdated}
                    active={liveStreamActive}
                  />
                  {previewPauseMessage && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center text-white gap-2 bg-black/70">
                      <MonitorPlay className="h-10 w-10 opacity-80" />
                      <p className="text-sm opacity-90">{previewPauseMessage}</p>
                    </div>
                  )}
                </div>

              </CardContent>
            </Card>

            {/* 控制面板 */}
            <div className="space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>攝影機選擇</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div>
                      <Label htmlFor="camera-select">選擇攝影機</Label>
                      <Select value={selectedCamera || ""} onValueChange={(value) => {
                        setSelectedCamera(value);
                      }}>
                        <SelectTrigger>
                          <SelectValue placeholder="選擇要串流的攝影機" />
                        </SelectTrigger>
                        <SelectContent>
                          {cameras.map((camera, index) => (
                            <SelectItem
                              key={camera.id?.toString() ?? `${camera.name}-${index}`}
                              value={camera.id?.toString() ?? `${camera.list_index ?? index}`}
                            >
                              <div className="flex items-center gap-2">
                                <div
                                  className={`w-2 h-2 rounded-full ${camera.status === 'active' ? 'bg-green-500' :
                                      camera.status === 'inactive' ? 'bg-gray-400' : 'bg-red-500'
                                    }`}
                                />
                                {camera.name}
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {selectedCameraData && (
                      <div className="pt-4 border-t space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">攝影機ID</span>
                          <span>{selectedCameraData.id}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">名稱</span>
                          <span>{selectedCameraData.name}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">狀態</span>
                          <Badge variant={getStatusColor(liveViewStatus || "offline")}>
                            {getStatusText(liveViewStatus || "offline")}
                          </Badge>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">攝影機類型</span>
                          <span>{selectedCameraData.camera_type}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">解析度</span>
                          <span>{selectedCameraData.resolution}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">幀率</span>
                          <span>{selectedCameraData.fps} FPS</span>
                        </div>
                        {selectedCameraData.ip && (
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">IP地址</span>
                            <span>{selectedCameraData.ip}</span>
                          </div>
                        )}
                        {selectedCameraData.model && (
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">型號</span>
                            <span>{selectedCameraData.model}</span>
                          </div>
                        )}
                        {selectedCameraData.location && (
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">位置</span>
                            <span>{selectedCameraData.location}</span>
                          </div>
                        )}
                        {selectedCameraData.rtsp_url && (
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">RTSP URL</span>
                            <span className="text-xs break-all">{selectedCameraData.rtsp_url}</span>
                          </div>
                        )}
                        {selectedCameraData.recording !== undefined && (
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">錄影狀態</span>
                            <Badge variant={selectedCameraData.recording ? "default" : "secondary"}>
                              {selectedCameraData.recording ? "錄影中" : "未錄影"}
                            </Badge>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>


            </div>
          </div>
        </TabsContent>

        <TabsContent value="settings">

        </TabsContent>
      </Tabs>
    </div>
  );
}

