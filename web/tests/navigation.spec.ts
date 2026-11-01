import { test, expect } from "@playwright/test";
import {
  buildThirdPartyHref,
  findNavigationItem,
  getNavigationItems,
  navigationItems,
  searchNavigation,
} from "../src/lib/navigation";

test("navigation has exactly one entry for every existing application route", () => {
  expect(navigationItems).toHaveLength(13);
  expect(new Set(navigationItems.map((item) => item.href)).size).toBe(13);
});
test("user navigation never returns administrative destinations", () => {
  expect(getNavigationItems("user").map((item) => item.href)).toEqual([
    "/conversations",
    "/image",
    "/video",
  ]);
  expect(searchNavigation("user", "邮箱")).toEqual([]);
});
test("search supports Chinese, English, whitespace and multiple terms", () => {
  expect(searchNavigation("admin", "邮箱").map((item) => item.href)).toContain("/register");
  expect(searchNavigation("admin", "  ACCOUNT pool  ").map((item) => item.href)).toEqual([
    "/accounts",
  ]);
  expect(searchNavigation("admin", "不存在的功能")).toEqual([]);
});
test("active route matching respects segment boundaries", () => {
  expect(findNavigationItem("/image/")?.href).toBe("/image");
  expect(findNavigationItem("/image-manager")?.href).toBe("/image-manager");
  expect(findNavigationItem("/images-unknown")).toBeUndefined();
});
test("third-party launch accepts only web URLs and encodes the explicit credentials", () => {
  expect(buildThirdPartyHref("javascript:alert(1)", "https://example.test", "fixture-key")).toBe(
    "",
  );
  expect(buildThirdPartyHref("not-a-url", "https://example.test", "fixture-key")).toBe("");
  const url = new URL(
    buildThirdPartyHref(
      "https://canvas.example.test/path?existing=1",
      "https://service.example.test",
      "fixture+/=&",
    ),
  );
  expect(url.searchParams.get("existing")).toBe("1");
  expect(url.searchParams.get("apiKey")).toBe("fixture+/=&");
});
