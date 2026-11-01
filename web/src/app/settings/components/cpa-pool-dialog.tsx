"use client";

import { useId } from "react";

import { Eye, EyeOff, Link2, LoaderCircle, Save, Unplug } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import { useSettingsStore } from "../store";

export function CPAPoolDialog() {
  const fieldId = useId();
  const dialogOpen = useSettingsStore((state) => state.dialogOpen);
  const editingPool = useSettingsStore((state) => state.editingPool);
  const formName = useSettingsStore((state) => state.formName);
  const formBaseUrl = useSettingsStore((state) => state.formBaseUrl);
  const formSecretKey = useSettingsStore((state) => state.formSecretKey);
  const showSecret = useSettingsStore((state) => state.showSecret);
  const isSavingPool = useSettingsStore((state) => state.isSavingPool);
  const setDialogOpen = useSettingsStore((state) => state.setDialogOpen);
  const setFormName = useSettingsStore((state) => state.setFormName);
  const setFormBaseUrl = useSettingsStore((state) => state.setFormBaseUrl);
  const setFormSecretKey = useSettingsStore((state) => state.setFormSecretKey);
  const setShowSecret = useSettingsStore((state) => state.setShowSecret);
  const savePool = useSettingsStore((state) => state.savePool);

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      <DialogContent showCloseButton={false} className="rounded-2xl p-6">
        <DialogHeader className="gap-2">
          <DialogTitle>{editingPool ? "编辑连接" : "添加连接"}</DialogTitle>
          <DialogDescription className="text-sm leading-6">
            {editingPool ? "修改 CPA 连接信息" : "添加一个新的 CLIProxyAPI 连接"}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground/80" htmlFor={fieldId + "-1"}>
              名称（可选）
            </label>
            <Input
              value={formName}
              onChange={(event) => setFormName(event.target.value)}
              placeholder="例如：主号池、备用池"
              className="h-11 rounded-xl border-border bg-card"
              id={fieldId + "-1"}
            />
          </div>
          <div className="space-y-2">
            <label
              className="flex items-center gap-1.5 text-sm font-medium text-foreground/80"
              htmlFor={fieldId + "-2"}
            >
              <Link2 className="size-3.5" />
              CPA 地址
            </label>
            <Input
              value={formBaseUrl}
              onChange={(event) => setFormBaseUrl(event.target.value)}
              placeholder="http://your-cpa-host:8317"
              className="h-11 rounded-xl border-border bg-card"
              id={fieldId + "-2"}
            />
          </div>
          <div className="space-y-2">
            <label
              className="flex items-center gap-1.5 text-sm font-medium text-foreground/80"
              htmlFor={fieldId + "-3"}
            >
              <Unplug className="size-3.5" />
              Management Secret Key
            </label>
            <div className="relative">
              <Input
                type={showSecret ? "text" : "password"}
                value={formSecretKey}
                onChange={(event) => setFormSecretKey(event.target.value)}
                placeholder={editingPool ? "留空则不修改密钥" : "CPA 管理密钥"}
                className="h-11 rounded-xl border-border bg-card pr-10"
                id={fieldId + "-3"}
              />
              <button
                type="button"
                className="absolute top-1/2 right-3 -translate-y-1/2 text-muted-foreground transition hover:text-foreground"
                onClick={() => setShowSecret(!showSecret)}
                aria-label="查看详情"
              >
                {showSecret ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          </div>
        </div>
        <DialogFooter className="pt-2">
          <Button
            variant="secondary"
            className="h-10 rounded-xl bg-muted px-5 text-foreground/80 hover:bg-secondary"
            onClick={() => setDialogOpen(false)}
            disabled={isSavingPool}
          >
            取消
          </Button>
          <Button
            className="h-10 rounded-xl bg-primary px-5 text-primary-foreground hover:bg-primary/90"
            onClick={() => void savePool()}
            disabled={isSavingPool}
          >
            {isSavingPool ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            {editingPool ? "保存修改" : "添加"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
