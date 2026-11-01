"use client";
import Link from "next/link";
import { CircleAlert, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="state-panel" role="alert">
      <CircleAlert className="size-8 text-destructive" aria-hidden="true" />
      <h1 className="text-lg font-semibold text-foreground">页面暂时未能加载</h1>
      <p>你的配置不会因此改变。请重新加载页面，或回到首页打开其他功能。</p>
      <div className="mt-2 flex flex-wrap justify-center gap-3">
        <Button onClick={reset}>
          <RefreshCw className="size-4" />
          重新加载
        </Button>
        <Button variant="outline" asChild>
          <Link href="/">返回首页</Link>
        </Button>
      </div>
    </div>
  );
}
