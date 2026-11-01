"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}
const getTheme = () => document.documentElement.classList.contains("dark");

export function ThemeToggle() {
  const isDark = useSyncExternalStore(subscribe, getTheme, () => false);
  const toggle = () => {
    const apply = () => {
      const next = document.documentElement.classList.toggle("dark");
      document.documentElement.style.colorScheme = next ? "dark" : "light";
      try {
        localStorage.setItem("chatgpt2api-theme", next ? "dark" : "light");
      } catch {
        /* Theme remains usable when browser storage is restricted. */
      }
    };
    if (
      document.startViewTransition &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      const transition = document.startViewTransition(apply);
      void transition.finished.catch(() => undefined);
    } else apply();
  };
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-9 text-muted-foreground"
      aria-label={isDark ? "切换为浅色主题" : "切换为深色主题"}
      title={isDark ? "切换为浅色主题" : "切换为深色主题"}
      onClick={toggle}
    >
      {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
