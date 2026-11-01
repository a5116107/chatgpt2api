import type { AuthRole } from "@/store/auth";

export type NavigationItem = {
  href: string;
  label: string;
  description: string;
  icon: string;
  group: string;
  adminOnly?: boolean;
  keywords: string;
};

export const navigationItems: readonly NavigationItem[] = [
  {
    href: "/conversations",
    label: "对话空间",
    description: "让想法展开，继续每一段对话。",
    icon: "messages",
    group: "创作空间",
    keywords: "chat conversation 聊天 会话",
  },
  {
    href: "/image",
    label: "图像创作",
    description: "从一个灵感，到一张值得留下的图像。",
    icon: "image",
    group: "创作空间",
    keywords: "image generate 生图 画图",
  },
  {
    href: "/video",
    label: "视频创作",
    description: "管理视频生成与任务进度。",
    icon: "video",
    group: "创作空间",
    keywords: "video 视频生成",
  },
  {
    href: "/image-manager",
    label: "图像资料库",
    description: "整理、查找和管理你的创作资产。",
    icon: "gallery",
    group: "创作空间",
    adminOnly: true,
    keywords: "images gallery 图片管理",
  },
  {
    href: "/accounts",
    label: "账号池",
    description: "账号状态、可用额度与批量操作，一处掌握。",
    icon: "users",
    group: "资源管理",
    adminOnly: true,
    keywords: "account pool 号池管理 账户",
  },
  {
    href: "/runtime-profiles",
    label: "账号连接",
    description: "管理账号线路、自动故障恢复和设备信息。",
    icon: "fingerprint",
    group: "资源管理",
    adminOnly: true,
    keywords: "runtime profile 运行画像 画像管理 TLS 自动换线 账号连接",
  },
  {
    href: "/proxies",
    label: "代理池",
    description: "查看可用线路、出口地区和账号容量。",
    icon: "network",
    group: "资源管理",
    adminOnly: true,
    keywords: "proxy network 网络",
  },
  {
    href: "/register",
    label: "注册工作台",
    description: "配置邮箱来源，跟踪注册任务的每一步。",
    icon: "workflow",
    group: "运行管理",
    adminOnly: true,
    keywords: "register mail 注册机 邮箱源",
  },
  {
    href: "/tasks",
    label: "任务中心",
    description: "集中跟踪任务、执行进度与处理结果。",
    icon: "tasks",
    group: "运行管理",
    adminOnly: true,
    keywords: "task job 任务",
  },
  {
    href: "/risk",
    label: "风控中心",
    description: "聚焦异常事件，核对账号的能力与风险。",
    icon: "shield",
    group: "运行管理",
    adminOnly: true,
    keywords: "risk security 风险 异常",
  },
  {
    href: "/logs",
    label: "运行日志",
    description: "按时间与类型检索，快速找到关键线索。",
    icon: "logs",
    group: "系统",
    adminOnly: true,
    keywords: "log 日志管理",
  },
  {
    href: "/debug",
    label: "接口调试",
    description: "检查请求与响应，验证接口行为。",
    icon: "terminal",
    group: "系统",
    adminOnly: true,
    keywords: "debug api 调试",
  },
  {
    href: "/settings",
    label: "系统设置",
    description: "管理连接、存储与服务的运行偏好。",
    icon: "settings",
    group: "系统",
    adminOnly: true,
    keywords: "settings config 配置",
  },
];

export function getNavigationItems(role: AuthRole) {
  return navigationItems.filter((item) => role === "admin" || !item.adminOnly);
}

export function findNavigationItem(pathname: string) {
  return navigationItems.find(
    (item) => pathname === item.href || pathname.startsWith(item.href + "/"),
  );
}

export function searchNavigation(role: AuthRole, query: string) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return getNavigationItems(role).filter((item) => {
    const text = [item.label, item.description, item.keywords, item.group, item.href]
      .join(" ")
      .toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
}

export function buildThirdPartyHref(appUrl: string, baseUrl: string, apiKey: string) {
  try {
    const target = new URL(appUrl.trim());
    if (!["https:", "http:"].includes(target.protocol)) return "";
    target.searchParams.set("apiKey", apiKey);
    target.searchParams.set("baseUrl", baseUrl);
    return target.toString();
  } catch {
    return "";
  }
}
