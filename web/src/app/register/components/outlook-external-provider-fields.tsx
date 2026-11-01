"use client";
import { useId } from "react";
import {
  Checkbox,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";

import type { MailProviderConfig } from "./mail-provider-catalog";

type OutlookExternalProviderFieldsProps = {
  provider: MailProviderConfig;
  disabled: boolean;
  onChange: (patch: MailProviderConfig) => void;
};

function fieldText(value: MailProviderConfig[string]): string {
  return value == null ? "" : String(value);
}

function OutlookApiFields({ provider, disabled, onChange }: OutlookExternalProviderFieldsProps) {
  const fieldId = useId();
  return (
    <>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-1"}>
          API Base
        </label>
        <Input
          value={fieldText(provider.api_base)}
          onChange={(event) => onChange({ api_base: event.target.value })}
          placeholder="https://mail.acica.top"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-1"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-2"}>
          API Key
        </label>
        <Input
          type="password"
          autoComplete="new-password"
          value={fieldText(provider.api_key)}
          onChange={(event) => onChange({ api_key: event.target.value })}
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-2"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-3"}>
          邮箱 API 代理
        </label>
        <Input
          value={fieldText(provider.proxy)}
          onChange={(event) => onChange({ proxy: event.target.value })}
          placeholder="http://proxy:port"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-3"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-4"}>
          邮件详情登录密码
        </label>
        <Input
          type="password"
          autoComplete="new-password"
          value={fieldText(provider.detail_password)}
          onChange={(event) => onChange({ detail_password: event.target.value })}
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-4"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-5"}>
          API 请求超时（秒）
        </label>
        <Input
          type="number"
          min={5}
          max={180}
          value={Number(provider.request_timeout || 30)}
          onChange={(event) =>
            onChange({
              request_timeout: Number(event.target.value) || 5,
            })
          }
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-5"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-6"}>
          分组 ID
        </label>
        <Input
          type="number"
          min={1}
          value={fieldText(provider.group_id)}
          onChange={(event) =>
            onChange({
              group_id: event.target.value ? Number(event.target.value) : null,
            })
          }
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-6"}
        />
      </div>
    </>
  );
}

function OutlookRetrievalFields({
  provider,
  disabled,
  onChange,
}: OutlookExternalProviderFieldsProps) {
  const fieldId = useId();
  return (
    <>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-7"}>
          取信文件夹
        </label>
        <Select
          value={String(provider.folder || "all")}
          onValueChange={(value) => onChange({ folder: value })}
          disabled={disabled}
        >
          <SelectTrigger className="h-10 rounded-xl border-border bg-card" id={fieldId + "-7"}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="inbox">收件箱</SelectItem>
            <SelectItem value="junkemail">垃圾邮件</SelectItem>
            <SelectItem value="deleteditems">已删除邮件</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-8"}>
          单次取信数量
        </label>
        <Input
          type="number"
          min={1}
          max={50}
          value={Number(provider.top || 20)}
          onChange={(event) => onChange({ top: Number(event.target.value) || 1 })}
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-8"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-9"}>
          预检账号数
        </label>
        <Input
          type="number"
          min={1}
          max={20}
          value={Number(provider.preflight_attempts || 8)}
          onChange={(event) =>
            onChange({
              preflight_attempts: Number(event.target.value) || 1,
            })
          }
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-9"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-10"}>
          验证码等待秒数
        </label>
        <Input
          type="number"
          min={10}
          max={120}
          value={Number(provider.otp_wait_seconds || 120)}
          onChange={(event) =>
            onChange({
              otp_wait_seconds: Number(event.target.value) || 10,
            })
          }
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-10"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-11"}>
          服务端轮询间隔（秒）
        </label>
        <Input
          type="number"
          min={1}
          max={15}
          value={Number(provider.otp_poll_interval || 3)}
          onChange={(event) =>
            onChange({
              otp_poll_interval: Number(event.target.value) || 1,
            })
          }
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-11"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-12"}>
          验证码邮件发件人过滤
        </label>
        <Input
          value={fieldText(provider.otp_from_contains)}
          onChange={(event) => onChange({ otp_from_contains: event.target.value })}
          placeholder="openai"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-12"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-13"}>
          验证码邮件主题过滤
        </label>
        <Input
          value={fieldText(provider.otp_subject_contains)}
          onChange={(event) => onChange({ otp_subject_contains: event.target.value })}
          placeholder="可留空"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-13"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-14"}>
          验证码邮件关键词
        </label>
        <Input
          value={fieldText(provider.otp_keyword)}
          onChange={(event) => onChange({ otp_keyword: event.target.value })}
          placeholder="可留空"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-14"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-15"}>
          验证码提取正则
        </label>
        <Input
          value={fieldText(provider.otp_code_regex)}
          onChange={(event) => onChange({ otp_code_regex: event.target.value })}
          placeholder="(?<!\\d)(\\d{6})(?!\\d)"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-15"}
        />
      </div>
    </>
  );
}

type OutlookToggleFieldProps = {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: (checked: boolean) => void;
};

function OutlookToggleField({ checked, disabled, label, onChange }: OutlookToggleFieldProps) {
  return (
    <label className="flex items-center gap-3 pt-8 text-sm text-foreground/80">
      <Checkbox
        checked={checked}
        onCheckedChange={(value) => onChange(Boolean(value))}
        disabled={disabled}
        aria-label={label}
      />
      {label}
    </label>
  );
}

export function OutlookExternalProviderFields(props: OutlookExternalProviderFieldsProps) {
  const { provider, disabled, onChange } = props;
  return (
    <>
      <OutlookApiFields {...props} />
      <details className="col-span-full rounded-xl border border-border bg-muted/40 p-4">
        <summary className="cursor-pointer text-sm font-medium text-foreground">
          取信与验证码高级设置
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            文件夹、预检、轮询与别名策略
          </span>
        </summary>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <OutlookRetrievalFields {...props} />
          <OutlookToggleField
            checked={Boolean(provider.prefer_alias ?? true)}
            disabled={disabled}
            label="优先已有别名"
            onChange={(checked) => onChange({ prefer_alias: checked })}
          />
          <OutlookToggleField
            checked={Boolean(provider.use_plus_alias ?? true)}
            disabled={disabled}
            label="启用 Plus 别名"
            onChange={(checked) => onChange({ use_plus_alias: checked })}
          />
          <OutlookToggleField
            checked={Boolean(provider.realtime_preflight ?? true)}
            disabled={disabled}
            label="启用实时取信预检"
            onChange={(checked) => onChange({ realtime_preflight: checked })}
          />
          <OutlookToggleField
            checked={Boolean(provider.server_otp_enabled ?? true)}
            disabled={disabled}
            label="服务端验证码长轮询"
            onChange={(checked) => onChange({ server_otp_enabled: checked })}
          />
          <OutlookToggleField
            checked={Boolean(provider.detail_session_enabled ?? false)}
            disabled={disabled}
            label="预览截断时读取完整正文"
            onChange={(checked) => onChange({ detail_session_enabled: checked })}
          />
        </div>
      </details>
    </>
  );
}
