"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  ChevronRight,
  Command,
  Fingerprint,
  GalleryVerticalEnd,
  ImageIcon,
  ListTodo,
  LoaderCircle,
  Logs,
  LogOut,
  Menu,
  MessagesSquare,
  Network,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  UsersRound,
  Video,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { HeaderActions } from "@/components/header-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import webConfig from "@/constants/common-env";
import { fetchThirdPartyApps, type ThirdPartyAppsSettings } from "@/lib/api";
import { getValidatedAuthSession } from "@/lib/auth-session";
import {
  buildThirdPartyHref,
  findNavigationItem,
  getNavigationItems,
  searchNavigation,
  type NavigationItem,
} from "@/lib/navigation";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const icons: Record<string, LucideIcon> = {
  messages: MessagesSquare,
  image: ImageIcon,
  video: Video,
  gallery: GalleryVerticalEnd,
  users: UsersRound,
  fingerprint: Fingerprint,
  network: Network,
  workflow: Workflow,
  tasks: ListTodo,
  shield: ShieldCheck,
  logs: Logs,
  terminal: TerminalSquare,
  settings: Settings2,
};

function Brand() {
  return (
    <Link href="/" className="workspace-brand" aria-label="ChatGPT2API 首页">
      <span className="brand-mark" aria-hidden="true">
        <Sparkles size={22} strokeWidth={1.7} />
      </span>
      <span>
        <strong>
          chatgpt2api<span className="brand-dot">.</span>
        </strong>
        <small>YOUR CREATIVE CONTROL ROOM</small>
      </span>
    </Link>
  );
}

function NavigationLinks({
  items,
  pathname,
  onNavigate,
}: {
  items: NavigationItem[];
  pathname: string;
  onNavigate?: () => void;
}) {
  const groups = [...new Set(items.map((item) => item.group))];
  return (
    <nav aria-label="主导航" className="workspace-navigation">
      {groups.map((group) => (
        <div className="navigation-group" key={group}>
          <p className="navigation-group-label">{group}</p>
          {items
            .filter((item) => item.group === group)
            .map((item) => {
              const Icon = icons[item.icon];
              const active = findNavigationItem(pathname)?.href === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={item.label}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn("navigation-link", active && "is-active")}
                >
                  <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
                  <span>{item.label}</span>
                  {active && <span className="navigation-active-dot" aria-hidden="true" />}
                </Link>
              );
            })}
        </div>
      ))}
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname().replace(/\/+$/, "") || "/";
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>();
  const [authError, setAuthError] = useState(false);
  const [authRetry, setAuthRetry] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [apps, setApps] = useState<ThirdPartyAppsSettings | null>(null);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);
  const page = findNavigationItem(pathname);
  const isCreative = pathname === "/conversations" || pathname === "/image";
  const isWorkspace = pathname !== "/login";
  useEffect(() => {
    document.documentElement.classList.toggle("studio-mode", isWorkspace);
    const viewport = window.visualViewport;
    const resize = () => {
      if (isWorkspace && (!viewport || viewport.scale === 1)) {
        document.documentElement.style.setProperty(
          "--studio-viewport-height",
          (viewport?.height || window.innerHeight) + "px",
        );
      }
    };
    resize();
    viewport?.addEventListener("resize", resize);
    window.addEventListener("resize", resize);
    return () => {
      document.documentElement.classList.remove("studio-mode");
      document.documentElement.style.removeProperty("--studio-viewport-height");
      viewport?.removeEventListener("resize", resize);
      window.removeEventListener("resize", resize);
    };
  }, [isWorkspace]);
  useEffect(() => {
    document.title =
      (pathname === "/login" ? "登录" : page?.label || "工作空间") + " · ChatGPT2API";
  }, [page?.label, pathname]);

  useEffect(() => {
    let active = true;
    if (pathname === "/login") return;
    void getValidatedAuthSession()
      .then((value) => {
        if (active) {
          setSession(value);
          setAuthError(false);
          if (!value) router.replace("/login");
        }
      })
      .catch(() => {
        if (active) setAuthError(true);
      });
    return () => {
      active = false;
    };
  }, [pathname, authRetry, router]);

  const sessionKey = session?.key;
  useEffect(() => {
    if (!sessionKey) return;
    let active = true;
    const load = () => {
      void fetchThirdPartyApps()
        .then((data) => {
          if (active) setApps(data.third_party_apps);
        })
        .catch(() => {
          if (active) setApps(null);
        });
    };
    load();
    window.addEventListener("third-party-apps-updated", load);
    return () => {
      active = false;
      window.removeEventListener("third-party-apps-updated", load);
    };
  }, [sessionKey]);

  useEffect(() => {
    if (!session || pathname === "/login") return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setQuery("");
        setSelected(0);
        setSearchOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session, pathname]);

  const results = useMemo(
    () => searchNavigation(session?.role || "user", query),
    [session?.role, query],
  );
  useEffect(() => {
    resultsRef.current
      ?.querySelector("[data-result-index='" + selected + "']")
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  if (pathname === "/login") return <main id="main-content">{children}</main>;
  if (authError)
    return (
      <main id="main-content" className="workspace-auth-loading">
        <div className="state-panel">
          <strong>暂时未能连接到服务</strong>
          <p>登录信息已保留，请检查网络后重试。</p>
          <Button
            onClick={() => {
              setAuthError(false);
              setAuthRetry((value) => value + 1);
            }}
          >
            重新连接
          </Button>
        </div>
      </main>
    );
  if (!session)
    return (
      <main id="main-content" className="workspace-auth-loading">
        <LoaderCircle className="size-6 animate-spin text-primary" aria-hidden="true" />
        <span role="status">正在打开工作空间…</span>
      </main>
    );

  const items = getNavigationItems(session.role);
  const name = session.name.trim() || (session.role === "admin" ? "管理员" : "创作者");
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const canvas = apps?.infinite_canvas;
  const canvasHref = canvas?.enabled
    ? buildThirdPartyHref(
        canvas.url,
        webConfig.apiUrl.replace(/\/$/, "") || window.location.origin,
        session.key,
      )
    : "";
  const canvasPreview = canvasHref
    ? (() => {
        const url = new URL(canvasHref);
        url.searchParams.set("apiKey", "••••••••");
        return url.toString();
      })()
    : "";
  const logout = async () => {
    await clearStoredAuthSession();
    setSession(null);
    router.replace("/login");
  };
  const openSearch = () => {
    setQuery("");
    setSelected(0);
    setSearchOpen(true);
  };
  const navigate = (href: string) => {
    setSearchOpen(false);
    setMobileOpen(false);
    router.push(href);
  };

  return (
    <div className={cn("workspace", isCreative && "workspace-creative")}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="workspace-sidebar">
        <Brand />
        <NavigationLinks items={items} pathname={pathname} />
        {canvasHref && (
          <button type="button" className="canvas-link" onClick={() => setCanvasOpen(true)}>
            <Sparkles size={16} />
            <span>无限画布</span>
            <ArrowUpRight size={15} />
          </button>
        )}
        <div className="sidebar-account">
          <span className="account-avatar" aria-hidden="true">
            {name.slice(0, 1).toLocaleUpperCase()}
          </span>
          <span className="min-w-0 flex-1">
            <strong title={name}>{name}</strong>
            <small>{roleLabel}</small>
          </span>
          <button
            type="button"
            onClick={() => void logout()}
            className="sidebar-logout"
            aria-label="退出登录"
            title="退出登录"
          >
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="workspace-stage">
        <header className="workspace-topbar">
          <div className="flex min-w-0 items-center gap-3">
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="" aria-label="打开导航">
                  <Menu />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="mobile-navigation w-[min(86vw,290px)] gap-0 p-0">
                <SheetHeader className="px-6 pt-7 pb-4">
                  <SheetTitle className="sr-only">工作空间导航</SheetTitle>
                  <SheetDescription className="sr-only">
                    选择创作、资源或系统管理页面
                  </SheetDescription>
                  <Brand />
                </SheetHeader>
                <NavigationLinks
                  items={items}
                  pathname={pathname}
                  onNavigate={() => setMobileOpen(false)}
                />
                {canvasHref && (
                  <button
                    type="button"
                    className="canvas-link"
                    onClick={() => {
                      setMobileOpen(false);
                      setCanvasOpen(true);
                    }}
                  >
                    <Sparkles size={16} />
                    无限画布
                    <ArrowUpRight size={15} />
                  </button>
                )}
                <div className="sidebar-account">
                  <span className="min-w-0 flex-1">
                    <strong>{name}</strong>
                    <small>{roleLabel}</small>
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="退出登录"
                    onClick={() => void logout()}
                  >
                    <LogOut />
                  </Button>
                </div>
              </SheetContent>
            </Sheet>
            <span className="hidden text-xs text-muted-foreground sm:inline">工作空间</span>
            <ChevronRight className="hidden size-3.5 text-muted-foreground sm:block" />
            <span className="truncate text-sm font-medium">{page?.label || "工作空间"}</span>
          </div>
          <div className="flex items-center gap-2 sm:gap-4">
            <button
              type="button"
              className="workspace-search"
              onClick={openSearch}
              aria-label="搜索页面和功能"
            >
              <Search size={15} />
              <span className="hidden sm:inline">搜索功能…</span>
              <kbd className="hidden sm:flex">
                <Command size={10} />K
              </kbd>
            </button>
            <span className="topbar-divider" />
            <HeaderActions showGithubText={false} />
          </div>
        </header>
        <div className="workspace-body">
          <main id="main-content" tabIndex={-1} className="workspace-content" key={pathname}>
            {isCreative ? children : <div className="workbench-page">{children}</div>}
          </main>
        </div>
      </div>
      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent className="command-dialog gap-0 overflow-hidden p-0" showCloseButton={false}>
          <DialogHeader className="sr-only">
            <DialogTitle>搜索功能</DialogTitle>
            <DialogDescription>
              输入页面名称，按上下方向键选择，回车打开，Esc 关闭。
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-3 border-b px-5 py-2">
            <Search className="size-5 shrink-0 text-primary" />
            <Input
              role="combobox"
              aria-label="搜索页面和功能"
              aria-expanded="true"
              aria-controls="command-results"
              aria-autocomplete="list"
              aria-activedescendant={results[selected] ? "command-result-" + selected : undefined}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setSelected(0);
              }}
              placeholder="搜索页面、功能或关键词…"
              className="h-12 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
              onKeyDown={(event) => {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  setSelected((index) =>
                    results.length
                      ? (index + (event.key === "ArrowDown" ? 1 : -1) + results.length) %
                        results.length
                      : 0,
                  );
                }
                if (event.key === "Enter" && results[selected]) {
                  event.preventDefault();
                  navigate(results[selected].href);
                }
              }}
            />
            <kbd className="command-key">ESC</kbd>
          </div>
          <div
            id="command-results"
            role="listbox"
            aria-label="匹配的页面"
            ref={resultsRef}
            className="command-results"
          >
            {results.length ? (
              results.map((item, index) => {
                const Icon = icons[item.icon];
                return (
                  <button
                    type="button"
                    tabIndex={-1}
                    role="option"
                    aria-selected={index === selected}
                    id={"command-result-" + index}
                    data-result-index={index}
                    key={item.href}
                    className={cn("command-result", selected === index && "is-selected")}
                    onPointerMove={() => setSelected(index)}
                    onClick={() => navigate(item.href)}
                  >
                    <Icon size={18} />
                    <span>
                      <strong>{item.label}</strong>
                      <small>{item.group}</small>
                    </span>
                    <ArrowUpRight size={15} />
                  </button>
                );
              })
            ) : (
              <div className="command-empty">
                <Search size={24} />
                <strong>没有匹配的功能</strong>
                <span>试试“邮箱”“任务”或“图片”。</span>
              </div>
            )}
          </div>
          <div className="command-footer">
            <span>↑ ↓ 选择</span>
            <span>↵ 打开</span>
            <span>Esc 关闭</span>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={canvasOpen} onOpenChange={setCanvasOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>连接到无限画布</DialogTitle>
            <DialogDescription>
              此操作会将当前服务地址与密钥发送给已配置的第三方应用。请确认信任该应用；密钥仅在你确认后传递。
            </DialogDescription>
          </DialogHeader>
          <p className="break-all rounded-xl border bg-muted p-4 font-mono text-xs leading-6">
            {canvasPreview}
          </p>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button
              onClick={() => {
                window.open(canvasHref, "_blank", "noopener,noreferrer");
                setCanvasOpen(false);
              }}
            >
              确认并打开
              <ArrowUpRight className="size-4" />
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
