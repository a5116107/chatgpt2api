import { Badge } from "@/components/ui/badge";
import type { AccountHealthCheck } from "@/lib/api";

export const healthLabels: Record<string, string> = {
  login_verified: "登录有效",
  browser_verification_required: "需要浏览器验证",
  login_expired: "登录已过期",
  login_redirect: "需要重新登录",
  proxy_auth_required: "代理认证失败",
  proxy_tunnel_rejected: "代理拒绝连接",
  network_timeout: "连接超时",
  network_error: "网络连接异常",
  tls_error: "证书或 TLS 异常",
  rate_limited: "上游限流",
  access_denied: "上游拒绝访问",
  region_restricted: "当前地区受限",
  upstream_error: "上游服务异常",
  invalid_response: "登录验证未通过",
  skipped: "使用中或已移除，已跳过",
  changed: "登录信息或线路已变更，待重检",
  cached: "沿用 1 分钟内的检测",
  check_error: "检测未完成",
};

const healthHints: Record<string, string> = {
  login_verified: "已通过登录身份接口验证。对话、生图和各模型的权限与额度仍以实际请求为准。",
  browser_verification_required:
    "网站返回浏览器验证页面。请在正常浏览器登录环境完成上游验证；这不代表账号被封或额度耗尽。",
  login_expired: "上游返回 401，请通过账号原有登录流程更新登录信息。",
  login_redirect: "登录入口返回跳转，请检查账号原有登录状态。",
  proxy_auth_required: "请检查当前绑定代理的用户名、密码及授权有效期。",
  proxy_tunnel_rejected: "代理在建立 CONNECT 隧道时拒绝连接，请检查代理服务的访问规则。",
  network_timeout: "请检查当前绑定线路的连通性和代理服务状态。",
  network_error: "请求尚未得到有效响应，请检查当前绑定线路。",
  tls_error: "请检查证书链、系统时间与 TLS 配置；检测始终验证证书。",
  rate_limited: "上游返回 429，请等待限制解除后再检测。",
  access_denied: "上游返回 403，尚未证实具体原因，请核对账号权限及上游服务状态。",
  region_restricted: "上游明确提示地区限制，请核对服务支持范围。",
  upstream_error: "上游返回异常状态，请稍后检查服务状态。",
  invalid_response: "响应未包含有效的登录身份，暂不判定账号有效。",
};

export function AccountHealthStatus({
  check,
  now,
}: {
  check?: AccountHealthCheck | null;
  now: number;
}) {
  if (!check) return <span className="text-xs text-muted-foreground">登录状态待检测</span>;
  const stale = check.stale || now - check.checked_at * 1000 >= 15 * 60 * 1000;
  const time = new Date(check.checked_at * 1000).toLocaleString("zh-CN", { hour12: false });
  return (
    <details className="max-w-60 text-xs" data-testid="account-health-status">
      <summary className="cursor-pointer rounded-md py-1 focus-visible:outline-2 focus-visible:outline-ring">
        <Badge
          variant={stale ? "secondary" : check.state === "login_verified" ? "success" : "warning"}
        >
          {stale ? "待重新检测" : healthLabels[check.state] || "检测未完成"}
        </Badge>
      </summary>
      <div className="space-y-1.5 py-2 leading-relaxed text-muted-foreground">
        <p>
          {stale
            ? `上次结果：${healthLabels[check.state] || "检测未完成"}，结果已过期或登录信息、线路已变化。`
            : healthHints[check.state]}
        </p>
        <p>检测时间：{time}</p>
        <p>
          {check.phase === "login" ? "登录验证" : "网站连接"}
          {check.http_status ? ` · HTTP ${check.http_status}` : ""} ·{" "}
          {(check.duration_ms / 1000).toFixed(1)} 秒
        </p>
      </div>
    </details>
  );
}
