"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchProxyPlatform, type ProxyPlatformStatus } from "@/lib/api";

export function useProxyPlatform() {
  const [data, setData] = useState<ProxyPlatformStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const active = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    try {
      const value = await fetchProxyPlatform(controller.signal);
      if (!controller.signal.aborted) {
        setData(value);
        setError("");
        setNow(Date.now());
      }
    } catch (failure) {
      if (!controller.signal.aborted)
        setError(failure instanceof Error ? failure.message : "连接检查失败");
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0);
    const poll = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    const visible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      active.current?.abort();
      active.current = null;
      clearTimeout(initial);
      clearInterval(poll);
      clearInterval(clock);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  const expired = Boolean(data?.valid_until && new Date(data.valid_until).getTime() <= now);
  const directReady = Boolean(
    data?.direct_pool &&
    data.direct_pool.assignable > 0 &&
    new Date(data.direct_pool.valid_until).getTime() > now,
  );
  const ready = ((data?.status === "ready" && !expired) || directReady) && !error;
  return { data, loading, error, expired, ready, directReady, refresh };
}

export type ProxyPlatformState = ReturnType<typeof useProxyPlatform>;
