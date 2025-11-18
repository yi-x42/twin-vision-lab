import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Switch } from "./ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Alert, AlertDescription } from "./ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogDescription } from "./ui/dialog";
import {
  AlertTriangle,
  Bell,
  Settings,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  Edit,
  Trash2,
  Check,
  X,
  Clock,
  User,
} from "lucide-react";
import {
  useEmailNotificationSettings,
  useUpdateEmailNotificationSettings,
  useAlertRules,
  useCreateAlertRule,
  useToggleAlertRule,
  useDeleteAlertRule,
  useCameras,
  useTestEmailNotification,
  useActiveAlerts,
} from "../hooks/react-query-hooks";

const alertTimeFormatter = new Intl.DateTimeFormat("zh-TW", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "Asia/Taipei",
});

type AlertTypeKey =
  | "lineCrossing"
  | "zoneDwell"
  | "speedAnomaly"
  | "crowdCount"
  | "fallDetection";

type TriggerField = {
  key: string;
  label: string;
  type: "number" | "text" | "select";
  placeholder?: string;
  helpText?: string;
  defaultValue?: string;
  options?: { label: string; value: string }[];
};

const alertTypeConfig: Record<
  AlertTypeKey,
  {
    label: string;
    fields: TriggerField[];
  }
> = {
  lineCrossing: {
    label: "越線警報",
    fields: [
      {
        key: "crossingCount",
        label: "越線人數",
        type: "number",
        placeholder: "輸入觸發人數",
        helpText: "在指定時間窗內越線人數超過此值即觸發",
      },
    ],
  },
  zoneDwell: {
    label: "區域滯留警報",
    fields: [
      {
        key: "dwellSeconds",
        label: "滯留秒數",
        type: "number",
        placeholder: "輸入秒數",
        helpText: "物件在區域內停留超過此秒數觸發",
      },
      {
        key: "simultaneousCount",
        label: "同時人數",
        type: "number",
        placeholder: "輸入人數",
        helpText: "區域內同時人數達到此值觸發，可搭配滯留秒數",
      },
    ],
  },
  speedAnomaly: {
    label: "速度異常警報",
    fields: [
      {
        key: "avgSpeedThreshold",
        label: "平均速度門檻 (m/s)",
        type: "number",
        placeholder: "例如 1.5",
      },
      {
        key: "maxSpeedThreshold",
        label: "最大速度門檻 (m/s)",
        type: "number",
        placeholder: "例如 2.2",
      },
    ],
  },
  crowdCount: {
    label: "人數警報",
    fields: [
      {
        key: "peopleCount",
        label: "人數門檻",
        type: "number",
        placeholder: "輸入人數",
        helpText: "偵測到的人數大於等於此值時觸發",
      },
    ],
  },
  fallDetection: {
    label: "跌倒警報",
    fields: [
      {
        key: "fallConfidence",
        label: "跌倒信心門檻 (0-1)",
        type: "number",
        placeholder: "例如 0.7",
        helpText: "動作識別模型輸出的最低信心值",
      },
    ],
  },
};

export function AlertManagement() {
  type TriggerValuesState = Record<string, unknown>;

  const [isRuleDialogOpen, setIsRuleDialogOpen] = useState(false);
  const [selectedAlertType, setSelectedAlertType] = useState<AlertTypeKey | "">("");
  const [triggerValues, setTriggerValues] = useState<TriggerValuesState>({});
  const [ruleName, setRuleName] = useState("");
  const ALL_CAMERAS_VALUE = "ALL_CAMERAS";
  const [selectedCameraId, setSelectedCameraId] = useState(ALL_CAMERAS_VALUE);
  const [ruleSeverity, setRuleSeverity] = useState("中");
  const [notificationSelections, setNotificationSelections] = useState({
    email: true,
    push: false,
    sms: false,
  });
  const alertTypeKeys = Object.keys(alertTypeConfig) as AlertTypeKey[];
  const {
    data: emailSettings,
    isLoading: isEmailSettingsLoading,
  } = useEmailNotificationSettings();
  const updateEmailSettingsMutation = useUpdateEmailNotificationSettings();
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailAddress, setEmailAddress] = useState("");
  const [emailCooldown, setEmailCooldown] = useState("30");
  const [fallRuleConfidence, setFallRuleConfidence] = useState("0.5");
  const { data: alertRulesData, isLoading: isAlertRulesLoading } = useAlertRules();
  const createAlertRuleMutation = useCreateAlertRule();
  const toggleAlertRuleMutation = useToggleAlertRule();
  const deleteAlertRuleMutation = useDeleteAlertRule();
  const testEmailNotificationMutation = useTestEmailNotification();
  const alertRules = alertRulesData ?? [];
  const { data: camerasData } = useCameras();
  const cameraOptions = camerasData ?? [];
  const cameraNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    cameraOptions.forEach((camera) => {
      const key = camera.id?.toString() ?? camera.name ?? "";
      if (key) {
        map[key] = camera.name || key;
      }
    });
    return map;
  }, [cameraOptions]);

  useEffect(() => {
    if (!emailSettings) {
      return;
    }
    setEmailEnabled(emailSettings.enabled);
    setEmailAddress(emailSettings.address || "");
    setFallRuleConfidence(emailSettings.confidence?.toString() ?? "0.5");
    setEmailCooldown(emailSettings.cooldown_seconds?.toString() ?? "30");
  }, [emailSettings]);

  const {
    data: activeAlertsData,
    isLoading: isActiveAlertsLoading,
    isError: isActiveAlertsError,
    error: activeAlertsError,
  } = useActiveAlerts();
  const activeAlerts = activeAlertsData ?? [];
  const [alertStatuses, setAlertStatuses] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!activeAlerts || activeAlerts.length === 0) {
      setAlertStatuses({});
      return;
    }
    setAlertStatuses((prev) => {
      const next = { ...prev };
      activeAlerts.forEach((alert) => {
        if (!next[alert.id]) {
          next[alert.id] = alert.status || "未處理";
        }
      });
      return next;
    });
  }, [activeAlerts]);

  // 模擬警報規則數據
  // 模擬通知設定
  const notificationSettings = {
    email: {
      enabled: true,
      address: "admin@company.com",
      highSeverity: true,
      mediumSeverity: true,
      lowSeverity: false,
    },
    sms: {
      enabled: true,
      number: "+886-912-345-678",
      highSeverity: true,
      mediumSeverity: false,
      lowSeverity: false,
    },
    push: {
      enabled: true,
      highSeverity: true,
      mediumSeverity: true,
      lowSeverity: true,
    },
  };

  const getDefaultTriggerValues = (type: AlertTypeKey) => {
    const defaults: TriggerValuesState = {};
    alertTypeConfig[type].fields.forEach((field) => {
      defaults[field.key] = field.defaultValue ?? "";
    });
    return defaults;
  };

  const handleAlertTypeChange = (value: AlertTypeKey) => {
    setSelectedAlertType(value);
    const defaults = getDefaultTriggerValues(value);
    if (value === "fallDetection") {
      defaults["fallConfidence"] = fallRuleConfidence;
    }
    setTriggerValues(defaults);
  };

  const updateTriggerValue = (key: string, value: string) => {
    if (key === "fallConfidence") {
      setFallRuleConfidence(value);
    }
    setTriggerValues((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const renderTriggerFields = () => {
    if (!selectedAlertType) {
      return <p className="text-sm text-muted-foreground">請先選擇警報類型以設定條件。</p>;
    }

    return alertTypeConfig[selectedAlertType].fields.map((field) => {
      if (field.type === "number" || field.type === "text") {
        return (
          <div key={field.key} className="space-y-1">
            <Label>{field.label}</Label>
            <Input
              type={field.type}
              placeholder={field.placeholder}
              value={
                typeof triggerValues[field.key] === "string"
                  ? (triggerValues[field.key] as string)
                  : ""
              }
              onChange={(event) => updateTriggerValue(field.key, event.target.value)}
            />
            {field.helpText && <p className="text-xs text-muted-foreground">{field.helpText}</p>}
            {selectedAlertType === "fallDetection" && field.key === "fallConfidence" && (
              <p className="text-xs text-muted-foreground">
                這個值會作為全域跌倒偵測門檻套用至所有任務。
              </p>
            )}
          </div>
        );
      }

      if (field.type === "select") {
        return (
          <div key={field.key} className="space-y-1">
            <Label>{field.label}</Label>
            <Select
              value={
                typeof triggerValues[field.key] === "string"
                  ? (triggerValues[field.key] as string)
                  : ""
              }
              onValueChange={(value) => updateTriggerValue(field.key, value)}
            >
              <SelectTrigger>
                <SelectValue placeholder={field.placeholder} />
              </SelectTrigger>
              <SelectContent>
                {field.options?.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {field.helpText && <p className="text-xs text-muted-foreground">{field.helpText}</p>}
          </div>
        );
      }

      return null;
    });
  };

  const resetRuleForm = () => {
    setRuleName("");
    setSelectedCameraId(ALL_CAMERAS_VALUE);
    setRuleSeverity("中");
    setSelectedAlertType("");
    setTriggerValues({});
    setNotificationSelections({ email: true, push: false, sms: false });
  };

  const handleToggleRuleStatus = (ruleId: string, enabled: boolean) => {
    toggleAlertRuleMutation.mutate(
      { ruleId, enabled },
      {
        onError: (error) => {
          alert(`切換規則狀態失敗：${error.message}`);
        },
      }
    );
  };

  const handleDeleteRule = (ruleId: string) => {
    if (!window.confirm("確定要刪除此警報規則嗎？")) {
      return;
    }
    deleteAlertRuleMutation.mutate(ruleId, {
      onError: (error) => {
        alert(`刪除規則失敗：${error.message}`);
      },
    });
  };

  const getRuleTypeLabel = (ruleType: string) =>
    alertTypeConfig[ruleType as AlertTypeKey]?.label ?? ruleType;

  const renderTriggerSummary = (rule: (typeof alertRules)[number]) => {
    const config = alertTypeConfig[rule.rule_type as AlertTypeKey];
    if (!config) {
      return "未設定觸發條件";
    }
    const entries = config.fields
      .map((field) => {
        const value = rule.trigger_values?.[field.key];
        if (!value) {
          return null;
        }
        if (typeof value === "object") {
          return `${field.label}: ${JSON.stringify(value)}`;
        }
        return `${field.label}: ${value}`;
      })
      .filter(Boolean);
    if (Array.isArray(rule.trigger_values?.lines)) {
      entries.push(`線段 ${rule.trigger_values?.lines.length} 條`);
    }
    if (Array.isArray(rule.trigger_values?.zones)) {
      entries.push(`區域 ${rule.trigger_values?.zones.length} 個`);
    }
    return entries.length ? entries.join("；") : "未設定觸發條件";
  };

  const actionLabelMap: Record<string, string> = {
    email: "郵件通知",
    push: "推送通知",
    sms: "簡訊通知",
  };

  const renderActionBadges = (rule: (typeof alertRules)[number]) => {
    const actions = rule.actions || {};
    const entries = Object.entries(actions).filter(([, value]) => value);
    if (!entries.length) {
      return <span className="text-sm text-muted-foreground">未設定</span>;
    }
    return (
      <div className="flex flex-wrap gap-1 mt-1">
        {entries.map(([key]) => (
          <Badge key={key} variant="secondary">
            {actionLabelMap[key] ?? key}
          </Badge>
        ))}
      </div>
    );
  };

  const handleSaveEmailSettings = () => {
    const parsedConfidence = Math.min(
      1,
      Math.max(0, Number(fallRuleConfidence) || 0.5)
    );
    const parsedCooldown = Math.max(5, Number(emailCooldown) || 30);
    updateEmailSettingsMutation.mutate(
      {
        enabled: emailEnabled,
        address: emailAddress,
        confidence: parsedConfidence,
        cooldown_seconds: parsedCooldown,
      },
      {
        onSuccess: () => {
          alert("郵件通知設定已儲存。");
        },
        onError: (error) => {
          alert(`儲存郵件設定失敗：${error.message}`);
        },
      }
    );
  };

  const handleSendTestEmail = () => {
    if (!emailEnabled) {
      alert("請先啟用郵件通知再發送測試郵件。");
      return;
    }
    if (!emailAddress.trim()) {
      alert("請先輸入收件者郵件地址。");
      return;
    }
    testEmailNotificationMutation.mutate(
      { address: emailAddress.trim() },
      {
        onSuccess: (response) => {
          alert(response.message || "測試郵件已寄出");
        },
        onError: (error) => {
          alert(`測試郵件寄送失敗：${error.message}`);
        },
      }
    );
  };

  const handleConfirmRule = async () => {
    if (!ruleName.trim()) {
      alert("請輸入規則名稱");
      return;
    }
    if (!selectedAlertType) {
      alert("請選擇警報類型");
      return;
    }
    const cleanedTriggers: Record<string, unknown> = {};
    Object.entries(triggerValues).forEach(([key, value]) => {
      if (value === undefined || value === null) {
        return;
      }
      if (typeof value === "string" && value.trim() === "") {
        return;
      }
      cleanedTriggers[key] = value;
    });

    try {
      await createAlertRuleMutation.mutateAsync({
        name: ruleName.trim(),
        rule_type: selectedAlertType,
        severity: ruleSeverity || "中",
        cameras:
          selectedCameraId && selectedCameraId !== ALL_CAMERAS_VALUE
            ? [selectedCameraId]
            : [],
        trigger_values: cleanedTriggers,
        actions: notificationSelections,
      });

      if (selectedAlertType === "fallDetection") {
        const parsedConfidence = Math.min(
          1,
          Math.max(
            0,
            Number(cleanedTriggers["fallConfidence"] ?? fallRuleConfidence ?? "0.5")
          )
        );
        setFallRuleConfidence(parsedConfidence.toString());
        updateEmailSettingsMutation.mutate(
          {
            enabled: emailEnabled,
            address: emailAddress,
            confidence: parsedConfidence,
            cooldown_seconds: Math.max(5, Number(emailCooldown) || 30),
          },
          {
            onError: (error) => {
              console.error("更新跌倒門檻失敗", error);
            },
          }
        );
      }

      resetRuleForm();
      setIsRuleDialogOpen(false);
    } catch (error) {
      alert("新增警報規則失敗，請稍後再試。");
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

  const getStatusColor = (status: string) => {
    switch (status) {
      case "已處理":
        return "default";
      case "處理中":
        return "secondary";
      case "未處理":
        return "destructive";
      default:
        return "outline";
    }
  };

  const formatAlertTimestamp = (value?: string) => {
    if (!value) {
      return "未知時間";
    }
    const parsedDate = new Date(value);
    if (Number.isNaN(parsedDate.getTime())) {
      return value;
    }
    return alertTimeFormatter.format(parsedDate);
  };

  const handleMarkAlertResolved = (alertId: string) => {
    setAlertStatuses((prev) => ({
      ...prev,
      [alertId]: "已處理",
    }));
  };

  const handleMarkAlertUnresolved = (alertId: string) => {
    setAlertStatuses((prev) => ({
      ...prev,
      [alertId]: "未處理",
    }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1>警報管理</h1>
        <div className="flex gap-2">
          <Dialog
            open={isRuleDialogOpen}
            onOpenChange={(open) => {
              setIsRuleDialogOpen(open);
              if (!open) {
                resetRuleForm();
              }
            }}
          >
            <DialogTrigger asChild>
              <Button>
                <Plus className="h-4 w-4 mr-2" />
                新增規則
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>新增警報規則</DialogTitle>
                <DialogDescription>
                  設定新的警報規則以監控特定事件和條件
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <Label htmlFor="rule-name">規則名稱</Label>
                    <Input
                      id="rule-name"
                      placeholder="輸入規則名稱"
                      value={ruleName}
                      onChange={(event) => setRuleName(event.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor="rule-type">警報類型</Label>
                    <Select
                      value={selectedAlertType || undefined}
                      onValueChange={(value) => handleAlertTypeChange(value as AlertTypeKey)}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="選擇警報類型" />
                      </SelectTrigger>
                      <SelectContent>
                        {alertTypeKeys.map((key) => (
                          <SelectItem key={key} value={key}>
                            {alertTypeConfig[key].label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div>
                <Label htmlFor="rule-cameras">適用攝影機</Label>
                <Select
                    value={selectedCameraId}
                    onValueChange={(value) => setSelectedCameraId(value)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="選擇攝影機" />
                  </SelectTrigger>
                  <SelectContent>
                      <SelectItem value={ALL_CAMERAS_VALUE}>全部攝影機</SelectItem>
                      {cameraOptions.map((camera) => (
                        <SelectItem key={camera.id} value={camera.id?.toString() ?? camera.name ?? ""}>
                          {camera.name || camera.id || "未知攝影機"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label>觸發條件</Label>
                  <div className="mt-2 space-y-3 rounded-md border border-dashed border-muted-foreground/40 bg-muted/30 p-4">
                    {renderTriggerFields()}
                  </div>
                </div>

                <div>
                  <Label htmlFor="rule-severity">嚴重程度</Label>
                  <Select value={ruleSeverity} onValueChange={(value) => setRuleSeverity(value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="選擇嚴重程度" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="高">高</SelectItem>
                      <SelectItem value="中">中</SelectItem>
                      <SelectItem value="低">低</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label>通知方式</Label>
                  <div className="flex flex-wrap gap-4 mt-2">
                    <div className="flex items-center space-x-2">
                      <input
                        type="checkbox"
                        id="email-action"
                        checked={notificationSelections.email}
                        onChange={(event) =>
                          setNotificationSelections((prev) => ({
                            ...prev,
                            email: event.target.checked,
                          }))
                        }
                      />
                      <Label htmlFor="email-action">郵件通知</Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <input
                        type="checkbox"
                        id="push-action"
                        checked={notificationSelections.push}
                        onChange={(event) =>
                          setNotificationSelections((prev) => ({
                            ...prev,
                            push: event.target.checked,
                          }))
                        }
                      />
                      <Label htmlFor="push-action">推送通知</Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <input
                        type="checkbox"
                        id="sms-action"
                        checked={notificationSelections.sms}
                        onChange={(event) =>
                          setNotificationSelections((prev) => ({
                            ...prev,
                            sms: event.target.checked,
                          }))
                        }
                      />
                      <Label htmlFor="sms-action">簡訊通知</Label>
                    </div>
                  </div>
                </div>

                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => setIsRuleDialogOpen(false)}>
                    取消
                  </Button>
                  <Button onClick={handleConfirmRule} disabled={createAlertRuleMutation.isPending}>
                    {createAlertRuleMutation.isPending ? "處理中..." : "確認新增"}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Tabs defaultValue="active-alerts">
        <TabsList>
          <TabsTrigger value="active-alerts">活躍警報</TabsTrigger>
          <TabsTrigger value="alert-rules">警報規則</TabsTrigger>
          <TabsTrigger value="notification-settings">通知設定</TabsTrigger>
        </TabsList>

        <TabsContent value="active-alerts">
          {isActiveAlertsError && activeAlertsError ? (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                無法載入活躍警報：{activeAlertsError.message || "未知錯誤"}
              </AlertDescription>
            </Alert>
          ) : isActiveAlertsLoading ? (
            <Card>
              <CardContent className="text-center py-6 text-muted-foreground">
                載入活躍警報中...
              </CardContent>
            </Card>
          ) : activeAlerts.length === 0 ? (
            <Card>
              <CardContent className="text-center py-6 text-muted-foreground">
                目前沒有觸發中的警報。
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4">
              {activeAlerts.map((alert) => {
                const statusLabel = alertStatuses[alert.id] || alert.status || "未處理";
                const alertTypeLabel = alert.type || alert.rule_name || "警報";
                const cameraLabel =
                  alert.camera || (alert.task_id ? `任務 ${alert.task_id}` : "未知攝影機");
                const descriptionText =
                  alert.description || `${alertTypeLabel} 已觸發，請儘速確認。`;
                return (
                  <Alert key={alert.id} className="border-l-4 border-l-red-500">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                      <div className="flex justify-between items-start">
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <Badge variant={getSeverityColor(alert.severity)}>{alert.severity}</Badge>
                            <Badge variant="outline">{alertTypeLabel}</Badge>
                            <Badge variant={getStatusColor(statusLabel)}>{statusLabel}</Badge>
                          </div>
                          <div>
                            <p className="font-medium">{descriptionText}</p>
                            <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                              <span className="flex items-center gap-1">
                                <Clock className="h-3 w-3" />
                                {formatAlertTimestamp(alert.timestamp)}
                              </span>
                              <span>{cameraLabel}</span>
                              {alert.assignee && (
                                <span className="flex items-center gap-1">
                                  <User className="h-3 w-3" />
                                  {alert.assignee}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                        <div className="flex gap-2">
                          {statusLabel === "未處理" && (
                            <Button size="sm" variant="outline">
                              <User className="h-4 w-4 mr-1" />
                              指派
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleMarkAlertResolved(alert.id)}
                          >
                            <Check className="h-4 w-4" />
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => handleMarkAlertUnresolved(alert.id)}>
                            <X className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    </AlertDescription>
                  </Alert>
                );
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="alert-rules">
          <div className="space-y-4">
            {isAlertRulesLoading ? (
              <Card>
                <CardContent className="text-center py-6 text-muted-foreground">
                  載入警報規則中...
                </CardContent>
              </Card>
            ) : alertRules.length === 0 ? (
              <Card>
                <CardContent className="text-center py-6 text-muted-foreground">
                  尚未建立任何警報規則，請使用「新增規則」開始配置。
                </CardContent>
              </Card>
            ) : (
              alertRules.map((rule) => {
                const typeLabel = getRuleTypeLabel(rule.rule_type);
                const cameraLabels =
                  rule.cameras && rule.cameras.length
                    ? rule.cameras.map((cameraId) => cameraNameMap[cameraId] ?? cameraId)
                    : ["全部攝影機"];
                return (
                  <Card key={rule.id}>
                    <CardHeader>
                      <div className="flex items-center justify-between">
                        <div>
                          <CardTitle className="flex items-center gap-2">
                            {rule.name}
                            <Badge variant={rule.enabled ? "default" : "outline"}>
                              {rule.enabled ? "啟用" : "停用"}
                            </Badge>
                            <Badge variant="secondary">{typeLabel}</Badge>
                          </CardTitle>
                          <p className="text-sm text-muted-foreground">
                            嚴重程度：{rule.severity || "未設定"}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Switch
                            checked={rule.enabled}
                            onCheckedChange={(checked) => handleToggleRuleStatus(rule.id, checked)}
                            disabled={toggleAlertRuleMutation.isPending}
                          />
                          <Button variant="outline" size="sm" disabled title="暫未提供編輯功能">
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDeleteRule(rule.id)}
                            disabled={deleteAlertRuleMutation.isPending}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="space-y-3">
                        <div>
                          <p className="text-sm text-muted-foreground">適用攝影機</p>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {cameraLabels.map((label) => (
                              <Badge key={label} variant="outline">
                                {label}
                              </Badge>
                            ))}
                          </div>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">觸發條件</p>
                          <p className="text-sm">{renderTriggerSummary(rule)}</p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">通知方式</p>
                          {renderActionBadges(rule)}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })
            )}
          </div>
        </TabsContent>

        <TabsContent value="notification-settings">
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {/* 郵件通知設定 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Mail className="h-5 w-5" />
                  郵件通知
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <Label htmlFor="email-enabled">啟用郵件通知</Label>
                  <Switch
                    id="email-enabled"
                    checked={emailEnabled}
                    onCheckedChange={setEmailEnabled}
                    disabled={isEmailSettingsLoading}
                  />
                </div>

                <div>
                  <Label htmlFor="email-address">郵件地址</Label>
                  <Input
                    id="email-address"
                    type="email"
                    value={emailAddress}
                    onChange={(event) => setEmailAddress(event.target.value)}
                    placeholder="輸入郵件地址"
                    disabled={!emailEnabled}
                  />
                </div>

                <div>
                  <p className="text-xs text-muted-foreground mb-2">
                    跌倒偵測的信心門檻請於「新增警報規則」中的跌倒警報設定，這裡僅配置通知的冷卻秒數。
                  </p>
                  <Label htmlFor="fall-cooldown">通知冷卻秒數</Label>
                  <Input
                    id="fall-cooldown"
                    type="number"
                    min="5"
                    value={emailCooldown}
                    onChange={(event) => setEmailCooldown(event.target.value)}
                    disabled={!emailEnabled}
                  />
                </div>

                <div className="space-y-3">
                  <Label>通知等級</Label>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm">高級別警報</span>
                      <Switch checked={notificationSettings.email.highSeverity} disabled />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">中級別警報</span>
                      <Switch checked={notificationSettings.email.mediumSeverity} disabled />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">低級別警報</span>
                      <Switch checked={notificationSettings.email.lowSeverity} disabled />
                    </div>
                  </div>
                </div>

                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={handleSendTestEmail}
                    disabled={!emailEnabled || testEmailNotificationMutation.isPending}
                  >
                    {testEmailNotificationMutation.isPending ? "寄送中..." : "發送測試郵件"}
                  </Button>
                  <Button onClick={handleSaveEmailSettings} disabled={updateEmailSettingsMutation.isPending}>
                    {updateEmailSettingsMutation.isPending ? "儲存中..." : "儲存郵件設定"}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* 簡訊通知設定 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <MessageSquare className="h-5 w-5" />
                  簡訊通知
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <Label htmlFor="sms-enabled">啟用簡訊通知</Label>
                  <Switch id="sms-enabled" checked={notificationSettings.sms.enabled} />
                </div>

                <div>
                  <Label htmlFor="phone-number">手機號碼</Label>
                  <Input 
                    id="phone-number" 
                    type="tel" 
                    defaultValue={notificationSettings.sms.number}
                    placeholder="輸入手機號碼" 
                  />
                </div>

                <div className="space-y-3">
                  <Label>通知等級</Label>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm">高級別警報</span>
                      <Switch checked={notificationSettings.sms.highSeverity} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">中級別警報</span>
                      <Switch checked={notificationSettings.sms.mediumSeverity} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">低級別警報</span>
                      <Switch checked={notificationSettings.sms.lowSeverity} />
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* 推送通知設定 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Bell className="h-5 w-5" />
                  推送通知
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <Label htmlFor="push-enabled">啟用推送通知</Label>
                  <Switch id="push-enabled" checked={notificationSettings.push.enabled} />
                </div>

                <div className="space-y-3">
                  <Label>通知等級</Label>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm">高級別警報</span>
                      <Switch checked={notificationSettings.push.highSeverity} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">中級別警報</span>
                      <Switch checked={notificationSettings.push.mediumSeverity} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">低級別警報</span>
                      <Switch checked={notificationSettings.push.lowSeverity} />
                    </div>
                  </div>
                </div>

                <div>
                  <Label>通知聲音</Label>
                  <Select defaultValue="default">
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="default">預設</SelectItem>
                      <SelectItem value="urgent">緊急</SelectItem>
                      <SelectItem value="soft">柔和</SelectItem>
                      <SelectItem value="silent">靜音</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="outline">重置</Button>
            <Button>儲存設定</Button>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
