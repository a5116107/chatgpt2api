import { expect, test } from "@playwright/test";
import { creativeErrorDetail, creativeErrorSummary } from "../src/lib/creative-error";

test("HTML error details contain the status and stage without CSS", () => {
  const raw =
    "bootstrap failed: status=403, body=<html><head><style>@keyframes enlarge-appear{display:flex}</style></head></html>";
  expect(creativeErrorSummary(raw)).toContain("403");
  expect(creativeErrorDetail(raw)).toContain("bootstrap");
  expect(creativeErrorDetail(raw)).toContain("403");
  expect(creativeErrorDetail(raw)).not.toMatch(/@keyframes|<html|display:flex/);
});

test("browser verification is distinct from an empty account pool", () => {
  const summary = creativeErrorSummary(
    "bootstrap failed: status=403, body=upstream_browser_verification_required",
  );
  expect(summary).toContain("浏览器验证");
  expect(summary).toContain("尚未开始生成");
  expect(summary).not.toContain("暂无可用");
});

test("revoked download tokens explain that generation is not repeated", () => {
  expect(
    creativeErrorSummary("Image was generated upstream, but its download token was revoked."),
  ).toContain("不会自动重复提交");
  expect(creativeErrorSummary("status=401 token_revoked")).toContain("重新登录");
});

test("incomplete responses are distinct from moderation rejection", () => {
  expect(
    creativeErrorSummary("Image generation started upstream but the response was incomplete."),
  ).toContain("不代表内容违规");
  expect(creativeErrorDetail("x".repeat(5000))).toHaveLength(1000);
});

test("missing image quota and video setup explain the required action", () => {
  expect(creativeErrorSummary("no available image quota")).toContain("账号管理");
  expect(creativeErrorSummary("video_provider_not_configured")).toContain("视频服务尚未配置");
});
