import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
} from "recharts";
import { Download, Filter } from "lucide-react";
import {
  useCameraPerformance,
  useAlertTrends,
  useAlertCategoryStats,
  useHourlyActivity,
} from "../hooks/react-query-hooks";

const RANGE_DAY_MAP = {
  "1day": 1,
  "7days": 7,
  "30days": 30,
  "90days": 90,
} as const;

type RangeOption = keyof typeof RANGE_DAY_MAP;

const STATUS_LABEL_MAP: Record<string, string> = {
  running: "正常運行",
  pending: "待啟動",
  completed: "已完成",
  failed: "異常",
};

const STATUS_VARIANT_MAP: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  running: "default",
  pending: "secondary",
  completed: "secondary",
  failed: "destructive",
};

const CATEGORY_COLOR_MAP: Record<string, string> = {
  linecrossing: "#f87171",
  zonedwell: "#fb923c",
  speedanomaly: "#facc15",
  crowdcount: "#60a5fa",
  falldetection: "#c026d3",
};

const DEFAULT_CATEGORY_COLOR = "#3b82f6";

const normalizeRuleTypeKey = (value?: string | null) => {
  if (!value) return "";
  return value.replace(/[^a-zA-Z]/g, "").toLowerCase();
};

const getCategoryColor = (ruleType?: string | null) => {
  const key = normalizeRuleTypeKey(ruleType);
  return CATEGORY_COLOR_MAP[key] ?? DEFAULT_CATEGORY_COLOR;
};

export function StatisticalAnalysis() {
  const [range, setRange] = useState<RangeOption>("7days");
  const selectedDays = RANGE_DAY_MAP[range];
  const {
    data: cameraPerformance = [],
    isLoading: cameraPerformanceLoading,
    isError: cameraPerformanceError,
  } = useCameraPerformance({ days: selectedDays, limit: 5 });
  const {
    data: alertTrends = [],
    isLoading: alertTrendsLoading,
    isError: alertTrendsError,
  } = useAlertTrends({ days: selectedDays });
  const {
    data: alertCategoryStats = [],
    isLoading: alertCategoryLoading,
    isError: alertCategoryError,
  } = useAlertCategoryStats({ days: selectedDays, limit: 4 });
  const {
    data: hourlyActivityData,
    isLoading: hourlyActivityLoading,
    isError: hourlyActivityError,
  } = useHourlyActivity({ hours: 24 });

  const formatChartDate = (value: string) => {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      const month = String(parsed.getMonth() + 1).padStart(2, "0");
      const day = String(parsed.getDate()).padStart(2, "0");
      return `${month}/${day}`;
    }
    return value;
  };

  const alertTrendChartData = alertTrends.map((item) => ({
    ...item,
    date: formatChartDate(item.date),
  }));

  const maxCategoryCount = alertCategoryStats.reduce(
    (max, item) => Math.max(max, item.count),
    0
  );
  const hourlyActivity = hourlyActivityData?.data ?? [];
  const hourlySummary = hourlyActivityData?.summary;

  const formatHourlyLabel = (hour?: string | null, count?: number) => {
    if (!hour) return "-";
    const countText = typeof count === "number" ? ` (${count} 次)` : "";
    return `${hour}${countText}`;
  };

  const formatAverageLabel = (value?: number) => {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    return `${value.toFixed(1)} 次`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>統計分析</h1>
        <div className="flex gap-2">
          <Select value={range} onValueChange={(value: RangeOption) => setRange(value)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1day">今日</SelectItem>
              <SelectItem value="7days">近7天</SelectItem>
              <SelectItem value="30days">近30天</SelectItem>
              <SelectItem value="90days">近90天</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline">
            <Filter className="h-4 w-4 mr-2" />
            篩選
          </Button>
          <Button variant="outline">
            <Download className="h-4 w-4 mr-2" />
            匯出報告
          </Button>
        </div>
      </div>

      {/* 關鍵指標概覽 */}
      

      <Tabs defaultValue="camera-performance">
        <TabsList>
          <TabsTrigger value="camera-performance">攝影機效能</TabsTrigger>
          <TabsTrigger value="alert-analysis">警報分析</TabsTrigger>
          <TabsTrigger value="time-analysis">時間分析</TabsTrigger>
        </TabsList>

        <TabsContent value="camera-performance">
          <Card>
            <CardHeader>
              <CardTitle>攝影機效能分析</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {cameraPerformanceLoading && (
                  <p className="text-sm text-muted-foreground">載入攝影機效能中...</p>
                )}
                {cameraPerformanceError && (
                  <p className="text-sm text-destructive">無法取得攝影機效能資料</p>
                )}
                {!cameraPerformanceLoading && !cameraPerformanceError && cameraPerformance.length === 0 && (
                  <p className="text-sm text-muted-foreground">選定期間內沒有攝影機偵測紀錄。</p>
                )}
                {!cameraPerformanceLoading &&
                  !cameraPerformanceError &&
                  cameraPerformance.map((camera) => {
                    const statusKey = camera.status ?? "unknown";
                    const statusLabel = STATUS_LABEL_MAP[statusKey] ?? "未知狀態";
                    const statusVariant = STATUS_VARIANT_MAP[statusKey] ?? "outline";
                    const runtimeLabel = `${(camera.runtime_hours ?? 0).toFixed(1)} 小時`;
                    const cameraName = camera.camera_name || camera.camera_id || "未命名攝影機";
                    const key = camera.camera_id ?? camera.camera_name ?? `${camera.detections}-${runtimeLabel}`;

                    return (
                      <div key={key} className="border rounded-lg p-4">
                        <div className="flex justify-between items-start mb-3">
                          <h4 className="font-medium">{cameraName}</h4>
                          <Badge variant="outline">{camera.detections} 次偵測</Badge>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                          <div>
                            <p className="text-sm text-muted-foreground">運行時間</p>
                            <p>{runtimeLabel}</p>
                          </div>
                          <div>
                            <p className="text-sm text-muted-foreground">狀態</p>
                            <Badge variant={statusVariant}>{statusLabel}</Badge>
                          </div>
                        </div>
                      </div>
                    );
                  })}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="alert-analysis">
          <div className="grid gap-6 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>警報趨勢分析</CardTitle>
              </CardHeader>
              <CardContent>
                {alertTrendsLoading && (
                  <p className="text-sm text-muted-foreground">載入警報趨勢中...</p>
                )}
                {alertTrendsError && (
                  <p className="text-sm text-destructive">無法取得警報趨勢資料</p>
                )}
                {!alertTrendsLoading && !alertTrendsError && alertTrends.length === 0 && (
                  <p className="text-sm text-muted-foreground">選定期間內沒有警報紀錄。</p>
                )}
                {!alertTrendsLoading && !alertTrendsError && alertTrends.length > 0 && (
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={alertTrendChartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="date" />
                      <YAxis />
                      <Tooltip />
                      <Area
                        type="monotone"
                        dataKey="high"
                        stackId="1"
                        stroke="#ff4d4f"
                        fill="#ff4d4f"
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                        name="高級別"
                      />
                      <Area
                        type="monotone"
                        dataKey="medium"
                        stackId="1"
                        stroke="#faad14"
                        fill="#faad14"
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                        name="中級別"
                      />
                      <Area
                        type="monotone"
                        dataKey="low"
                        stackId="1"
                        stroke="#52c41a"
                        fill="#52c41a"
                        dot={{ r: 3 }}
                        activeDot={{ r: 5 }}
                        name="低級別"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
                
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>警報分類統計</CardTitle>
              </CardHeader>
              <CardContent>
                {alertCategoryLoading && (
                  <p className="text-sm text-muted-foreground">載入警報分類中...</p>
                )}
                {alertCategoryError && (
                  <p className="text-sm text-destructive">無法取得警報分類資料</p>
                )}
                {!alertCategoryLoading && !alertCategoryError && alertCategoryStats.length === 0 && (
                  <p className="text-sm text-muted-foreground">選定期間內沒有警報分類資料。</p>
                )}
                {!alertCategoryLoading &&
                  !alertCategoryError &&
                  alertCategoryStats.length > 0 && (
                    <div className="space-y-4">
                      {alertCategoryStats.map((category) => {
                        const barColor = getCategoryColor(category.rule_type);
                        const widthPercentage = maxCategoryCount
                          ? `${Math.min(100, (category.count / maxCategoryCount) * 100)}%`
                          : "0%";
                        const key = category.rule_type || category.label;
                        return (
                          <div key={key} className="flex justify-between items-center">
                            <span>{category.label}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-32 bg-gray-200 rounded-full h-2">
                                <div
                                  className="h-2 rounded-full"
                                  style={{ width: widthPercentage, backgroundColor: barColor }}
                                ></div>
                              </div>
                              <span className="text-sm">{category.count}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="time-analysis">
          <Card>
            <CardHeader>
              <CardTitle>24小時活動分析</CardTitle>
            </CardHeader>
            <CardContent>
              {hourlyActivityLoading && (
                <p className="text-sm text-muted-foreground">載入活動分析中...</p>
              )}
              {hourlyActivityError && (
                <p className="text-sm text-destructive">無法取得活動分析資料</p>
              )}
              {!hourlyActivityLoading && !hourlyActivityError && hourlyActivity.length === 0 && (
                <p className="text-sm text-muted-foreground">過去24小時沒有偵測紀錄。</p>
              )}
              {!hourlyActivityLoading &&
                !hourlyActivityError &&
                hourlyActivity.length > 0 && (
                  <>
                    <ResponsiveContainer width="100%" height={400}>
                      <LineChart data={hourlyActivity}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="hour" />
                        <YAxis />
                        <Tooltip />
                        <Line
                          type="monotone"
                          dataKey="count"
                          stroke="#8884d8"
                          strokeWidth={2}
                          dot={{ fill: "#8884d8" }}
                          name="偵測次數"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                    <div className="mt-4 grid gap-4 md:grid-cols-3">
                      <div className="text-center">
                        <p className="text-sm text-muted-foreground">高峰時段</p>
                        <p className="text-lg">
                          {formatHourlyLabel(
                            hourlySummary?.peak_hour,
                            hourlySummary?.peak_count
                          )}
                        </p>
                      </div>
                      <div className="text-center">
                        <p className="text-sm text-muted-foreground">低峰時段</p>
                        <p className="text-lg">
                          {formatHourlyLabel(
                            hourlySummary?.low_hour,
                            hourlySummary?.low_count
                          )}
                        </p>
                      </div>
                      <div className="text-center">
                        <p className="text-sm text-muted-foreground">平均每小時</p>
                        <p className="text-lg">
                          {formatAverageLabel(hourlySummary?.average_per_hour)}
                        </p>
                      </div>
                    </div>
                  </>
                )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}