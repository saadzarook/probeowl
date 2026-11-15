// Author: @SaadZarook
import * as Dialog from "@radix-ui/react-dialog";
import type { ReactElement, ReactNode } from "react";
import { Button } from "./Button";

type SheetProps = {
  /** The button that opens it: focus goes back to it when the sheet closes. */
  trigger: ReactElement;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  children: ReactNode;
};

/** A panel from the left edge over the page, such as the menu below 1024 px (rule 5). */
export function Sheet({ trigger, open, onOpenChange, title, description, children }: SheetProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-overlay" />
        <Dialog.Content className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-full flex-col gap-4 overflow-y-auto bg-sidebar p-4 text-sidebar-foreground">
          <div className="flex items-center justify-between gap-2">
            <Dialog.Title className="m-0 text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <Button variant="ghost" size="sm" className="text-sidebar-foreground hover:bg-sidebar-active">
                Close
              </Button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">{description}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
