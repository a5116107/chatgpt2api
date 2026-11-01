"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Eye,
  EyeOff,
  ImageIcon,
  LoaderCircle,
  MessagesSquare,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { HeaderActions } from "@/components/header-actions";
import { login } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function LoginPage() {
  const router = useRouter();
  const [authKey, setAuthKey] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const submitting = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const { isCheckingAuth } = useRedirectIfAuthenticated();

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting.current) return;
    const key = authKey.trim();
    if (!key) {
      setError("请输入访问密钥后再登录。");
      input.current?.focus();
      return;
    }
    submitting.current = true;
    setIsSubmitting(true);
    setError("");
    try {
      const data = await login(key);
      await setStoredAuthSession({
        key,
        role: data.role,
        subjectId: data.subject_id,
        name: data.name,
      });
      router.replace(getDefaultRouteForRole(data.role));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "登录未成功，请稍后重试。");
      input.current?.focus();
    } finally {
      submitting.current = false;
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth)
    return (
      <div className="workspace-auth-loading" role="status">
        <LoaderCircle className="size-6 animate-spin text-primary" aria-hidden="true" />
        正在检查登录状态…
      </div>
    );

  return (
    <div className="login-layout">
      <section className="login-story" aria-labelledby="login-story-title">
        <div className="workspace-brand">
          <span className="brand-mark">
            <Sparkles size={22} strokeWidth={1.7} />
          </span>
          <span>
            <strong>
              chatgpt2api<span className="brand-dot">.</span>
            </strong>
            <small>YOUR CREATIVE CONTROL ROOM</small>
          </span>
        </div>
        <div className="login-story-content">
          <span className="login-story-label">A LITTLE SPACE. ENDLESS POSSIBILITIES.</span>
          <h2 id="login-story-title">
            给灵感，
            <br />
            一个<em>生长的空间。</em>
          </h2>
          <p>
            把对话、图像与日常管理放在一起。
            <br />
            少一点切换，多一点创造。
          </p>
        </div>
        <div className="login-story-footer">
          <span className="flex items-center gap-2">
            <MessagesSquare size={15} />
            对话探索
          </span>
          <span className="flex items-center gap-2">
            <ImageIcon size={15} />
            图像创作
          </span>
          <span className="flex items-center gap-2">
            <Workflow size={15} />
            一站管理
          </span>
        </div>
        <div className="login-orbit" aria-hidden="true" />
      </section>
      <section className="login-form-side" aria-labelledby="login-title">
        <HeaderActions className="absolute top-5 right-5 sm:top-8 sm:right-8" />
        <div className="login-form-wrap">
          <span className="login-eyebrow">WELCOME TO YOUR WORKSPACE</span>
          <h1 id="login-title">欢迎回来。</h1>
          <p className="login-form-note">
            输入访问密钥，继续你的创作与管理。
            <br />
            系统会根据密钥自动识别你的访问权限。
          </p>
          <form
            className="login-form"
            noValidate
            onSubmit={(event) => void handleLogin(event)}
            aria-busy={isSubmitting}
          >
            <div className="login-field">
              <label htmlFor="auth-key">访问密钥</label>
              <div className="login-input-wrap">
                <Input
                  ref={input}
                  id="auth-key"
                  name="auth-key"
                  type={visible ? "text" : "password"}
                  autoComplete="current-password"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={authKey}
                  onChange={(event) => {
                    setAuthKey(event.target.value);
                    setError("");
                  }}
                  placeholder="输入你的访问密钥"
                  disabled={isSubmitting}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? "login-error" : "login-security"}
                />
                <button
                  type="button"
                  aria-label={visible ? "隐藏密钥" : "显示密钥"}
                  aria-pressed={visible}
                  onClick={() => setVisible((value) => !value)}
                >
                  {visible ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
              {error && (
                <p id="login-error" role="alert" className="text-xs leading-6 text-destructive">
                  {error}
                </p>
              )}
            </div>
            <Button
              type="submit"
              className="h-12 w-full justify-between px-5"
              disabled={isSubmitting}
            >
              {isSubmitting ? "正在验证…" : "进入工作空间"}
              {isSubmitting ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <ArrowRight className="size-4" aria-hidden="true" />
              )}
            </Button>
          </form>
          <p id="login-security" className="login-security-note">
            <ShieldCheck size={15} className="shrink-0 text-primary" />
            请妥善保管密钥，不要在共享设备上保持登录。
          </p>
        </div>
      </section>
    </div>
  );
}
