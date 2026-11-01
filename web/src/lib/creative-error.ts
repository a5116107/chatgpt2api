/** Keep upstream HTML and diagnostics out of the main creative surface. */
export function creativeErrorSummary(raw: string) {
  if (/upstream_browser_verification_required/i.test(raw))
    return "上游要求浏览器验证（403），本次尚未开始生成。请在账号的正常登录环境完成验证后重试。";
  if (/download token was revoked/i.test(raw))
    return "账号登录凭据已失效。已停止本次任务，请重新登录该账号；生成过的图片不会自动重复提交。";
  if (/401|token.revoked|invalidated.oauth/i.test(raw))
    return "账号登录凭据已失效，请在账号管理中重新登录后重试。";
  if (/incomplete|image_result_unavailable|result could not be retrieved/i.test(raw))
    return "上游未返回完整图片结果。本次未记为成功，也不代表内容违规；请先检查任务详情。";
  if (/slots busy/i.test(raw)) return "可用图片任务槽已占满，请等待当前任务结束后重试。";
  if (/no available text account/i.test(raw))
    return "暂无可用的对话账号。请在账号管理中检查可用额度与账号状态。";
  if (/no available image quota|no available image account/i.test(raw))
    return "暂无可用的图片额度。请先在账号管理中检查登录状态和图片额度，再重新提交。";
  if (/video_provider_not_configured/i.test(raw))
    return "视频服务尚未配置。请先完成视频服务配置，再创建任务。";
  if (/403|access denied|forbidden/i.test(raw))
    return "上游服务拒绝了请求（403）。请检查账号或代理状态后重试。";
  if (/429|quota|rate.limit/i.test(raw)) return "当前额度不足或请求过于频繁，请稍后重试。";
  if (/超时|timeout|timed out/i.test(raw))
    return "等待结果超时，任务可能仍在处理。可继续等待或重新生成。";
  if (/<[a-z!][\s\S]*>/i.test(raw)) return "上游返回了异常页面，本次请求未成功。请稍后重试。";
  return raw.length > 220 ? raw.slice(0, 220) + "…" : raw || "请求失败，请稍后重试。";
}

export function creativeErrorDetail(raw: string) {
  if (
    /<(?:!doctype|html|head|style|script|body)\b/i.test(raw) ||
    /@keyframes|font-family:|flex-direction:/i.test(raw)
  ) {
    const status = raw.match(/(?:status\s*[=:]\s*|\b)(401|403|429|5\d\d)\b/i)?.[1];
    const stage = raw.match(/^([\w/.-]+)\s+failed/i)?.[1];
    return [
      stage ? `失败阶段：${stage}` : "上游返回了 HTML 错误页面",
      status ? `HTTP 状态：${status}` : "",
      "页面样式和脚本已省略。",
    ]
      .filter(Boolean)
      .join("\n");
  }
  return raw.slice(0, 1000);
}
