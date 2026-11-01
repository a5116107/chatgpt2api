"use client";

import { useId } from "react";

import { ExternalLink, LoaderCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { useSettingsStore } from "../store";

export function ThirdPartyAppsCard() {
  const fieldId = useId();
  const config = useSettingsStore((state) => state.config);
  const isLoadingConfig = useSettingsStore((state) => state.isLoadingConfig);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setInfiniteCanvasField = useSettingsStore((state) => state.setInfiniteCanvasField);
  const saveConfig = useSettingsStore((state) => state.saveConfig);

  if (isLoadingConfig || !config?.third_party_apps) {
    return (
      <Card className="rounded-2xl border-border bg-card shadow-sm">
        <CardContent className="flex items-center justify-center p-10">
          <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  const canvas = config.third_party_apps.infinite_canvas;

  return (
    <Card className="rounded-2xl border-border bg-card shadow-sm">
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-foreground">
              <ExternalLink className="size-5 text-muted-foreground" />
              无限画布入口
            </div>
            <p className="mt-1 text-xs leading-6 text-muted-foreground">
              开启后会在顶部导航显示入口，跳转时自动附带本项目地址和当前密钥。
            </p>
          </div>
          <span
            className={`rounded-full px-3 py-1 text-xs ${canvas.enabled ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
          >
            {canvas.enabled ? "已启用" : "未启用"}
          </span>
        </div>

        <div className="space-y-4 rounded-xl border border-border bg-card px-4 py-3">
          <label className="flex items-center gap-3 text-sm text-foreground/80">
            <Checkbox
              checked={Boolean(canvas.enabled)}
              onCheckedChange={(checked) => setInfiniteCanvasField("enabled", Boolean(checked))}
              aria-label="启用无限画布"
            />
            启用无限画布
          </label>
          <div className="space-y-2">
            <label className="text-sm text-foreground/80" htmlFor={fieldId + "-2"}>
              无限画布地址
            </label>
            <Input
              value={canvas.url}
              onChange={(event) => setInfiniteCanvasField("url", event.target.value)}
              placeholder="https://canvas.best"
              className="h-10 rounded-xl border-border bg-card"
              id={fieldId + "-2"}
            />
            <p className="text-xs leading-5 text-muted-foreground">
              顶部入口跳转时会追加 apiKey 和 baseUrl 参数；关闭后顶部导航不显示无限画布。
            </p>
            <p className="text-xs leading-5 text-warning">
              该入口仅供个人测试使用；长期使用建议自行本机部署无限画布。
            </p>
          </div>
        </div>

        <div className="flex justify-end">
          <Button
            className="h-10 rounded-xl bg-primary px-5 text-primary-foreground hover:bg-primary/90"
            onClick={() => void saveConfig()}
            disabled={isSavingConfig}
          >
            {isSavingConfig ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
