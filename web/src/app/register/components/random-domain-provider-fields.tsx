import { useId } from "react";
import type { ComponentType } from "react";

import { Input } from "@/components/ui";

import type { MailProviderConfig } from "./mail-provider-catalog";
import type { RegisterMailProviderValue } from "@/lib/api";

type ProviderFieldProps = {
  provider: MailProviderConfig;
  disabled: boolean;
  onChange: (patch: MailProviderConfig) => void;
};

type RandomDomainProviderFieldsProps = ProviderFieldProps & {
  providerName: string;
};

function optionalFieldText(value: RegisterMailProviderValue | undefined): string {
  return value == null ? "" : String(value);
}

function RandomDomainAttemptsField({ provider, disabled, onChange }: ProviderFieldProps) {
  const fieldId = useId();
  return (
    <div className="space-y-2">
      <label className="text-sm text-foreground/80" htmlFor={fieldId + "-1"}>
        随机域名尝试次数
      </label>
      <Input
        type="number"
        min={1}
        max={20}
        value={Number(provider.random_domain_attempts || 8)}
        onChange={(event) => onChange({ random_domain_attempts: Number(event.target.value) })}
        className="h-10 rounded-xl border-border bg-card"
        disabled={disabled}
        id={fieldId + "-1"}
      />
    </div>
  );
}

function TempMailRandomDomainFields(props: ProviderFieldProps) {
  return <RandomDomainAttemptsField {...props} />;
}

function DropMailRandomDomainFields({ provider, disabled, onChange }: ProviderFieldProps) {
  const fieldId = useId();
  return (
    <>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-2"}>
          API Base
        </label>
        <Input
          value={optionalFieldText(provider.api_base)}
          onChange={(event) => onChange({ api_base: event.target.value })}
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-2"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-3"}>
          Token 生命周期
        </label>
        <Input
          value={String(provider.token_lifetime || "1d")}
          onChange={(event) => onChange({ token_lifetime: event.target.value })}
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-3"}
        />
      </div>
      <RandomDomainAttemptsField provider={provider} disabled={disabled} onChange={onChange} />
    </>
  );
}

const randomDomainEditors: Record<string, ComponentType<ProviderFieldProps>> = {
  tempmail_lol: TempMailRandomDomainFields,
  dropmail: DropMailRandomDomainFields,
};

export function RandomDomainProviderFields({
  providerName,
  provider,
  disabled,
  onChange,
}: RandomDomainProviderFieldsProps) {
  const ProviderFields = randomDomainEditors[providerName];
  return ProviderFields ? (
    <ProviderFields provider={provider} disabled={disabled} onChange={onChange} />
  ) : null;
}
