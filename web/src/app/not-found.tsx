import Link from "next/link";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
export default function NotFound() {
  return (
    <div className="state-panel">
      <Compass className="size-10 text-primary" aria-hidden="true" />
      <span className="font-mono text-xs text-muted-foreground">404 / PAGE NOT FOUND</span>
      <h1 className="text-xl font-semibold text-foreground">这个页面不在工作空间里</h1>
      <p>地址可能已变更。返回首页，或通过导航找到你需要的功能。</p>
      <Button asChild>
        <Link href="/">返回工作空间</Link>
      </Button>
    </div>
  );
}
