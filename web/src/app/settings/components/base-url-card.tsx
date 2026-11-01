"use client";

import { useId } from "react";

import { Globe, LoaderCircle, Save } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

import { useSettingsStore } from "../store";

export function BaseUrlCard() {
  const fieldId = useId();
  const config = useSettingsStore((state) => state.config);
  const isLoadingConfig = useSettingsStore((state) => state.isLoadingConfig);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setBaseUrl = useSettingsStore((state) => state.setBaseUrl);
  const saveConfig = useSettingsStore((state) => state.saveConfig);

  const baseUrl = String(config?.base_url || "");

  return (
    <Card className="rounded-2xl border-border bg-card shadow-sm">
      <CardContent className="space-y-6 p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-muted">
              <Globe className="size-5 text-foreground/80" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">基础地址</h2>
              <p className="text-sm text-muted-foreground">
                设置 `CHATGPT2API_BASE_URL` 的本地配置值，保存后立即生效。
              </p>
            </div>
          </div>
          <Badge
            variant={baseUrl.trim() ? "success" : "secondary"}
            className="w-fit rounded-md px-2.5 py-1"
          >
            {baseUrl.trim() ? "已配置" : "未配置"}
          </Badge>
        </div>

        {isLoadingConfig ? (
          <div className="flex items-center justify-center py-10">
            <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground/80" htmlFor={fieldId + "-1"}>
                Base URL
              </label>
              <Input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://example.com"
                className="h-11 rounded-xl border-border bg-card"
                id={fieldId + "-1"}
              />
              <p className="text-sm text-muted-foreground">
                留空则使用环境变量或默认值，保存时会自动去掉首尾空格。
              </p>
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
                保存配置
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
