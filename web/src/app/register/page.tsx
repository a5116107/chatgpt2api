"use client";

import { useEffect, useRef } from "react";
import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { SharedProxyStatus } from "@/components/shared-proxy-status";
import { useProxyPlatform } from "@/hooks/use-proxy-platform";

import webConfig from "@/constants/common-env";
import { useAuthGuard } from "@/lib/use-auth-guard";
import type { RegisterConfig } from "@/lib/api";
import { getStoredAuthKey } from "@/store/auth";

import { useSettingsStore } from "../settings/store";
import { RegisterCard } from "./components/register-card";

function RegisterDataController() {
  const didLoadRef = useRef(false);
  const loadRegister = useSettingsStore((state) => state.loadRegister);
  const setRegisterConfig = useSettingsStore((state) => state.setRegisterConfig);

  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    void loadRegister();
  }, [loadRegister]);

  useEffect(() => {
    let source: EventSource | null = null;
    let closed = false;
    void getStoredAuthKey().then((token) => {
      if (closed || !token) return;
      const baseUrl = webConfig.apiUrl.replace(/\/$/, "");
      source = new EventSource(`${baseUrl}/api/register/events?token=${encodeURIComponent(token)}`);
      source.onmessage = (event) => {
        setRegisterConfig(JSON.parse(event.data) as RegisterConfig);
      };
    });
    return () => {
      closed = true;
      source?.close();
    };
  }, [setRegisterConfig]);

  return null;
}

function RegisterPageContent() {
  const platform = useProxyPlatform();
  return (
    <>
      <RegisterDataController />
      <section className="workbench-toolbar">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
            账号管理
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">注册工作台</h1>
          <p className="text-sm text-muted-foreground">设置目标、确认准备情况，再开始任务。</p>
        </div>
      </section>
      <section>
        <div className="mb-5">
          <SharedProxyStatus state={platform} />
        </div>
        <RegisterCard platform={platform} />
        <div className="mt-5 rounded-2xl border border-border bg-card p-5">
          <h2 className="font-semibold">注册完成后</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            先在账号列表确认登录状态与额度，再开始对话或生成图片。注册成功不等于所有模型都可使用。
          </p>
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            <Link
              href="/accounts/"
              className="rounded-lg border border-border px-4 py-2 hover:bg-muted"
            >
              1. 查看账号与额度
            </Link>
            <Link
              href="/conversations/"
              className="rounded-lg border border-border px-4 py-2 hover:bg-muted"
            >
              2. 开始对话
            </Link>
            <Link
              href="/image/"
              className="rounded-lg border border-border px-4 py-2 hover:bg-muted"
            >
              3. 生成图片
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}

export default function RegisterPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return <RegisterPageContent />;
}
