import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "./ui/pagination";
import { Skeleton } from "./ui/skeleton";
import {
  Search,
  Filter,
  Download,
  Eye,
  Clock,
  AlertTriangle,
  FileText,
  Database,
} from "lucide-react";
import {
  useCameras,
  useDetectionResults,
  type DetectionRecordsQuery,
} from "../hooks/react-query-hooks";

type FilterState = {
  startTime: string;
  endTime: string;
  cameraKey: string;
  cameraId?: string;
  cameraName?: string;
};

type PaginationIndicator = number | "ellipsis-left" | "ellipsis-right";

const DEFAULT_PAGE_SIZE = 5;

const createDefaultFilters = (): FilterState => ({
  startTime: "",
  endTime: "",
  cameraKey: "all",
  cameraId: undefined,
  cameraName: "",
});

const dateTimeFormatter = new Intl.DateTimeFormat("zh-TW", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const formatDateTime = (value?: string | null) => {
  if (!value) {
    return "--";
  }

  // 如果時間字串沒有時區資訊（沒有 Z 或 +），則視為 UTC
  let dateStr = value;
  if (!dateStr.endsWith("Z") && !dateStr.includes("+")) {
    dateStr += "Z";
  }

  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return dateTimeFormatter.format(date);
};

const formatConfidence = (value?: number | null) => {
  if (value === null || value === undefined) {
    return "--";
  }
  return `${(value * 100).toFixed(1)}%`;
};

const toIsoString = (value: string): string | undefined => {
  if (!value) {
    return undefined;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }
  return date.toISOString();
};

const buildPaginationRange = (
  current: number,
  total: number,
): PaginationIndicator[] => {
  const safeTotal = Math.max(total, 1);
  const pages: PaginationIndicator[] = [];
  const clampedCurrent = Math.min(Math.max(current, 1), safeTotal);

  if (safeTotal <= 5) {
    for (let i = 1; i <= safeTotal; i += 1) {
      pages.push(i);
    }
    return pages;
  }

  pages.push(1);

  if (clampedCurrent > 3) {
    pages.push("ellipsis-left");
  }

  const start = Math.max(2, clampedCurrent - 1);
  const end = Math.min(safeTotal - 1, clampedCurrent + 1);

  for (let i = start; i <= end; i += 1) {
    pages.push(i);
  }

  if (clampedCurrent < safeTotal - 2) {
    pages.push("ellipsis-right");
  }

  pages.push(safeTotal);

  return pages;
};

export function RecordQuery() {
  const pageSize = DEFAULT_PAGE_SIZE;
  const [currentPage, setCurrentPage] = useState(1);
  const [formFilters, setFormFilters] = useState<FilterState>(() =>
    createDefaultFilters(),
  );
  const [appliedFilters, setAppliedFilters] = useState<FilterState>(() =>
    createDefaultFilters(),
  );
  const { data: cameraData } = useCameras();
  const cameraOptions = cameraData ?? [];

  const handleCameraChange = (value: string) => {
    if (value === "all") {
      setFormFilters((prev) => ({
        ...prev,
        cameraKey: value,
        cameraId: undefined,
        cameraName: "",
      }));
      return;
    }

    const selectedCamera = cameraOptions.find(
      (camera) => camera.id === value || camera.name === value,
    );

    setFormFilters((prev) => ({
      ...prev,
      cameraKey: value,
      cameraId: selectedCamera?.id,
      cameraName: selectedCamera?.name ?? value,
    }));
  };

  const detectionQueryParams = useMemo<DetectionRecordsQuery>(() => {
    const payload: DetectionRecordsQuery = {
      page: currentPage,
      limit: pageSize,
    };

    const startIso = toIsoString(appliedFilters.startTime);
    const endIso = toIsoString(appliedFilters.endTime);
    if (startIso) {
      payload.startTime = startIso;
    }
    if (endIso) {
      payload.endTime = endIso;
    }

    if (appliedFilters.cameraId) {
      payload.cameraId = appliedFilters.cameraId;
    } else if (appliedFilters.cameraName) {
      payload.cameraName = appliedFilters.cameraName;
    }

    return payload;
  }, [appliedFilters, currentPage, pageSize]);

  const {
    data: detectionData,
    isLoading: isDetectionLoading,
    isError: isDetectionError,
    error: detectionError,
    refetch: refetchDetection,
  } = useDetectionResults(detectionQueryParams);

  const detectionRecords = detectionData?.results ?? [];
  const totalRecords = detectionData?.total ?? 0;
  const rawTotalPages = detectionData?.total_pages ?? 0;
  const effectiveTotalPages = Math.max(rawTotalPages, 1);
  const paginationIndicators = useMemo(
    () => buildPaginationRange(currentPage, effectiveTotalPages),
    [currentPage, effectiveTotalPages],
  );
  const limitFromResponse = detectionData?.limit ?? pageSize;
  const currentPageFromResponse = detectionData?.page ?? currentPage;
  const pageStart =
    totalRecords === 0 ? 0 : (currentPageFromResponse - 1) * limitFromResponse + 1;
  const pageEnd =
    totalRecords === 0
      ? 0
      : Math.min(pageStart + limitFromResponse - 1, totalRecords);

  useEffect(() => {
    if (!rawTotalPages) {
      return;
    }
    if (currentPage > effectiveTotalPages) {
      setCurrentPage(effectiveTotalPages);
    }
  }, [currentPage, effectiveTotalPages, rawTotalPages]);

  const summaryText = isDetectionLoading
    ? "資料載入中..."
    : totalRecords > 0
      ? `顯示第 ${pageStart}-${pageEnd} 筆，共 ${totalRecords} 筆記錄`
      : "目前沒有符合條件的偵測結果";

  const handleSearch = () => {
    setAppliedFilters({ ...formFilters });
    setCurrentPage(1);
  };

  const handleReset = () => {
    const defaults = createDefaultFilters();
    setFormFilters(defaults);
    setAppliedFilters(defaults);
    setCurrentPage(1);
  };

  const handlePageChange = (page: number) => {
    if (page < 1 || page === currentPage || page > effectiveTotalPages) {
      return;
    }
    setCurrentPage(page);
  };

  const canGoPrev = currentPage > 1;
  const canGoNext = currentPage < effectiveTotalPages && totalRecords > 0;

  const getStatusColor = (status: string) => {
    switch (status) {
      case "confirmed":
      case "已處理":
        return "default";
      case "pending":
      case "處理中":
        return "secondary";
      case "false_positive":
      case "未處理":
        return "outline";
      default:
        return "outline";
    }
  };

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case "高":
        return "destructive";
      case "中":
        return "secondary";
      case "低":
        return "outline";
      default:
        return "outline";
    }
  };

  const getLogLevelColor = (level: string) => {
    switch (level) {
      case "ERROR":
        return "destructive";
      case "WARNING":
        return "secondary";
      case "INFO":
        return "default";
      default:
        return "outline";
    }
  };

  const alertRecords = [
    {
      id: "ALERT_001",
      timestamp: "2024-01-15 14:30:25",
      camera: "大門入口",
      type: "入侵偵測",
      severity: "高",
      description: "未授權人員進入限制區域",
      status: "已處理",
      handler: "管理員A",
    },
    {
      id: "ALERT_002",
      timestamp: "2024-01-15 14:28:12",
      camera: "停車場",
      type: "異常行為",
      severity: "中",
      description: "可疑人員長時間逗留",
      status: "處理中",
      handler: "操作員B",
    },
    {
      id: "ALERT_003",
      timestamp: "2024-01-15 14:25:45",
      camera: "後門出口",
      type: "設備異常",
      severity: "低",
      description: "攝影機連線不穩定",
      status: "未處理",
      handler: "-",
    },
  ];

  const systemLogs = [
    {
      id: "LOG_001",
      timestamp: "2024-01-15 14:30:25",
      level: "INFO",
      module: "Detection Service",
      message: "YOLO模型初始化完成",
      user: "system",
    },
    {
      id: "LOG_002",
      timestamp: "2024-01-15 14:29:18",
      level: "WARNING",
      module: "Camera Service",
      message: "攝影機 CAM_003 連線逾時",
      user: "system",
    },
    {
      id: "LOG_003",
      timestamp: "2024-01-15 14:28:45",
      level: "ERROR",
      module: "Storage Service",
      message: "磁碟空間不足警告",
      user: "system",
    },
    {
      id: "LOG_004",
      timestamp: "2024-01-15 14:27:32",
      level: "INFO",
      module: "User Service",
      message: "用戶 admin 登入系統",
      user: "admin",
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>紀錄查詢</h1>
        <div className="flex gap-2">
          <Button variant="outline">
            <Filter className="h-4 w-4 mr-2" />
            進階篩選
          </Button>
          <Button variant="outline">
            <Download className="h-4 w-4 mr-2" />
            匯出資料
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-5 w-5" />
            搜尋條件
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <Label htmlFor="date-from">開始日期時間</Label>
              <Input
                id="date-from"
                type="datetime-local"
                value={formFilters.startTime}
                onChange={(event) =>
                  setFormFilters((prev) => ({
                    ...prev,
                    startTime: event.target.value,
                  }))
                }
              />
            </div>
            <div>
              <Label htmlFor="date-to">結束日期時間</Label>
              <Input
                id="date-to"
                type="datetime-local"
                value={formFilters.endTime}
                onChange={(event) =>
                  setFormFilters((prev) => ({
                    ...prev,
                    endTime: event.target.value,
                  }))
                }
              />
            </div>
            <div>
              <Label htmlFor="camera-filter">攝影機</Label>
              <Select
                value={formFilters.cameraKey}
                onValueChange={handleCameraChange}
              >
                <SelectTrigger id="camera-filter">
                  <SelectValue placeholder="選擇攝影機" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部攝影機</SelectItem>
                  {cameraOptions.map((camera) => {
                    const optionValue = camera.id || camera.name;
                    if (!optionValue) {
                      return null;
                    }
                    const valueString = String(optionValue);
                    return (
                      <SelectItem key={valueString} value={valueString}>
                        {camera.name || camera.id}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" type="button" onClick={handleReset}>
              重置
            </Button>
            <Button type="button" onClick={handleSearch}>
              <Search className="h-4 w-4 mr-2" />
              搜尋
            </Button>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="detection-records">
        <TabsList>
          <TabsTrigger value="detection-records">偵測記錄</TabsTrigger>
          <TabsTrigger value="alert-records">警報記錄</TabsTrigger>
          <TabsTrigger value="system-logs">系統日誌</TabsTrigger>
        </TabsList>

        <TabsContent value="detection-records">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-5 w-5" />
                偵測記錄查詢
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>縮圖</TableHead>
                    <TableHead>時間區間</TableHead>
                    <TableHead>偵測來源</TableHead>
                    <TableHead>信心度</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isDetectionLoading &&
                    Array.from({ length: pageSize }).map((_, index) => (
                      <TableRow key={`loading-${index}`}>
                        <TableCell>
                          <Skeleton className="h-16 w-20 rounded" />
                        </TableCell>
                        <TableCell>
                          <div className="space-y-1">
                            <Skeleton className="h-4 w-40" />
                            <Skeleton className="h-3 w-28" />
                          </div>
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-32" />
                          <Skeleton className="h-3 w-24 mt-1" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-16" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-8 w-16 rounded" />
                        </TableCell>
                      </TableRow>
                    ))}

                  {!isDetectionLoading && isDetectionError && (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="py-10 text-center text-sm text-destructive"
                      >
                        <div className="space-y-3">
                          <p>
                            載入偵測記錄時發生錯誤：
                            {detectionError?.message ?? "未知錯誤"}
                          </p>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => refetchDetection()}
                          >
                            重新整理
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}

                  {!isDetectionLoading &&
                    !isDetectionError &&
                    detectionRecords.length === 0 && (
                      <TableRow>
                        <TableCell
                          colSpan={5}
                          className="py-10 text-center text-sm text-muted-foreground"
                        >
                          找不到符合條件的偵測記錄
                        </TableCell>
                      </TableRow>
                    )}

                  {!isDetectionLoading &&
                    !isDetectionError &&
                    detectionRecords.map((record) => {
                      const thumbnailSrc = record.thumbnail_url;
                      const sourceLabel =
                        record.camera_name ||
                        record.task_name ||
                        `任務 #${record.task_id}`;
                      const startText = formatDateTime(
                        record.start_time ?? record.timestamp,
                      );
                      const endText = formatDateTime(
                        record.end_time ?? record.timestamp,
                      );

                      return (
                        <TableRow key={record.id}>
                          <TableCell>
                            {thumbnailSrc ? (
                              <img
                                src={thumbnailSrc}
                                alt="偵測縮圖"
                                className="w-20 h-16 object-cover rounded border"
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-20 h-16 rounded border bg-muted text-xs text-muted-foreground flex items-center justify-center">
                                無縮圖
                              </div>
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1">
                              <div className="flex items-center gap-1">
                                <Clock className="h-3 w-3 text-muted-foreground" />
                                <span className="text-sm">{startText}</span>
                              </div>
                              <div className="pl-4 text-xs text-muted-foreground">
                                ~ {endText}
                              </div>
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1">
                              <span>{sourceLabel}</span>
                              <span className="text-xs text-muted-foreground">
                                物件：{record.object_type ?? "未知"}
                              </span>
                            </div>
                          </TableCell>
                          <TableCell>{formatConfidence(record.confidence)}</TableCell>
                          <TableCell>
                            <Button
                              variant="outline"
                              size="sm"
                              disabled
                              title="影像預覽功能即將推出"
                            >
                              <Eye className="h-4 w-4" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                </TableBody>
              </Table>

              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mt-4">
                <p className="text-sm text-muted-foreground">{summaryText}</p>
                {totalRecords > 0 && (
                  <Pagination>
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious
                          href="#"
                          aria-disabled={!canGoPrev}
                          className={!canGoPrev ? "pointer-events-none opacity-50" : undefined}
                          onClick={(event) => {
                            event.preventDefault();
                            if (canGoPrev) {
                              handlePageChange(currentPage - 1);
                            }
                          }}
                        />
                      </PaginationItem>
                      {paginationIndicators.map((indicator, index) =>
                        indicator === "ellipsis-left" ||
                          indicator === "ellipsis-right" ? (
                          <PaginationItem key={`${indicator}-${index}`}>
                            <PaginationEllipsis />
                          </PaginationItem>
                        ) : (
                          <PaginationItem key={indicator}>
                            <PaginationLink
                              href="#"
                              isActive={indicator === currentPage}
                              onClick={(event) => {
                                event.preventDefault();
                                handlePageChange(indicator);
                              }}
                            >
                              {indicator}
                            </PaginationLink>
                          </PaginationItem>
                        )
                      )}
                      <PaginationItem>
                        <PaginationNext
                          href="#"
                          aria-disabled={!canGoNext}
                          className={!canGoNext ? "pointer-events-none opacity-50" : undefined}
                          onClick={(event) => {
                            event.preventDefault();
                            if (canGoNext) {
                              handlePageChange(currentPage + 1);
                            }
                          }}
                        />
                      </PaginationItem>
                    </PaginationContent>
                  </Pagination>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="alert-records">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="h-5 w-5" />
                警報記錄查詢
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>時間</TableHead>
                    <TableHead>攝影機</TableHead>
                    <TableHead>警報類型</TableHead>
                    <TableHead>嚴重程度</TableHead>
                    <TableHead>描述</TableHead>
                    <TableHead>狀態</TableHead>
                    <TableHead>處理人員</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {alertRecords.map((record) => (
                    <TableRow key={record.id}>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3 text-muted-foreground" />
                          <span className="text-sm">{record.timestamp}</span>
                        </div>
                      </TableCell>
                      <TableCell>{record.camera}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{record.type}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={getSeverityColor(record.severity)}>
                          {record.severity}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-xs truncate">
                        {record.description}
                      </TableCell>
                      <TableCell>
                        <Badge variant={getStatusColor(record.status)}>
                          {record.status}
                        </Badge>
                      </TableCell>
                      <TableCell>{record.handler}</TableCell>
                      <TableCell>
                        <div className="flex gap-2">
                          <Button variant="outline" size="sm">
                            <Eye className="h-4 w-4" />
                          </Button>
                          <Button variant="outline" size="sm">
                            <FileText className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <div className="flex justify-between items-center mt-4">
                <p className="text-sm text-muted-foreground">
                  顯示第 1-3 筆，共 89 筆記錄
                </p>
                <Pagination>
                  <PaginationContent>
                    <PaginationItem>
                      <PaginationPrevious href="#" />
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationLink href="#" isActive>
                        1
                      </PaginationLink>
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationLink href="#">2</PaginationLink>
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationNext href="#" />
                    </PaginationItem>
                  </PaginationContent>
                </Pagination>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="system-logs">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText className="h-5 w-5" />
                系統日誌查詢
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-4">
                <Select defaultValue="all">
                  <SelectTrigger className="w-48">
                    <SelectValue placeholder="選擇日誌等級" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部等級</SelectItem>
                    <SelectItem value="error">ERROR</SelectItem>
                    <SelectItem value="warning">WARNING</SelectItem>
                    <SelectItem value="info">INFO</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>時間</TableHead>
                    <TableHead>等級</TableHead>
                    <TableHead>模組</TableHead>
                    <TableHead>訊息</TableHead>
                    <TableHead>用戶</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {systemLogs.map((log) => (
                    <TableRow key={log.id}>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3 text-muted-foreground" />
                          <span className="text-sm">{log.timestamp}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={getLogLevelColor(log.level)}>
                          {log.level}
                        </Badge>
                      </TableCell>
                      <TableCell>{log.module}</TableCell>
                      <TableCell className="max-w-md truncate">
                        {log.message}
                      </TableCell>
                      <TableCell>{log.user}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <div className="flex justify-between items-center mt-4">
                <p className="text-sm text-muted-foreground">
                  顯示第 1-4 筆，共 234 筆記錄
                </p>
                <Pagination>
                  <PaginationContent>
                    <PaginationItem>
                      <PaginationPrevious href="#" />
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationLink href="#" isActive>
                        1
                      </PaginationLink>
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationLink href="#">2</PaginationLink>
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationLink href="#">3</PaginationLink>
                    </PaginationItem>
                    <PaginationItem>
                      <PaginationNext href="#" />
                    </PaginationItem>
                  </PaginationContent>
                </Pagination>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
