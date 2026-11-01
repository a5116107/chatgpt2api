"use client";
import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
export function ActionMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline">
          更多操作
          <ChevronDown className="size-3.5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-72 p-2"
        aria-label="更多操作"
        onClick={(event) => {
          const button = (event.target as HTMLElement).closest("button");
          if (button && !button.disabled) setOpen(false);
        }}
      >
        <div className="flex flex-col gap-1 [&_button]:h-auto [&_button]:min-h-10 [&_button]:w-full [&_button]:justify-start [&_button]:whitespace-normal [&_button]:border-0 [&_button]:text-left [&_button]:shadow-none">
          {children}
        </div>
      </PopoverContent>
    </Popover>
  );
}
