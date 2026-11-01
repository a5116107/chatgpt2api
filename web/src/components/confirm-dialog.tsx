"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
type Confirmation = {
  title: string;
  description: string;
  confirmLabel?: string;
  destructive?: boolean;
};
export function useConfirmation() {
  const [options, setOptions] = useState<Confirmation | null>(null);
  const resolver = useRef<((value: boolean) => void) | null>(null);
  const focusTarget = useRef<HTMLElement | null>(null);
  useEffect(
    () => () => {
      resolver.current?.(false);
      resolver.current = null;
    },
    [],
  );
  const ask = useCallback(
    (next: Confirmation) =>
      new Promise<boolean>((resolve) => {
        resolver.current?.(false);
        resolver.current = resolve;
        focusTarget.current =
          document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setOptions(next);
      }),
    [],
  );
  const finish = (value: boolean) => {
    resolver.current?.(value);
    resolver.current = null;
    setOptions(null);
  };
  const dialog = (
    <Dialog
      open={Boolean(options)}
      onOpenChange={(open) => {
        if (!open) finish(false);
      }}
    >
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (focusTarget.current?.isConnected) {
            event.preventDefault();
            focusTarget.current.focus();
          }
        }}
      >
        <DialogHeader>
          <span
            className={
              options?.destructive === false
                ? "mb-2 grid size-11 place-items-center rounded-xl bg-primary/10 text-primary"
                : "mb-2 grid size-11 place-items-center rounded-xl bg-destructive/10 text-destructive"
            }
          >
            <AlertTriangle className="size-5" />
          </span>
          <DialogTitle>{options?.title}</DialogTitle>
          <DialogDescription className="leading-7">{options?.description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => finish(false)} autoFocus>
            取消
          </Button>
          <Button
            variant={options?.destructive === false ? "default" : "destructive"}
            onClick={() => finish(true)}
          >
            {options?.confirmLabel || "确认删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
  return { confirm: ask, confirmationDialog: dialog };
}
