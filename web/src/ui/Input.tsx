// Author: @SaadZarook
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { CONTROL, cn } from "./cn";
import { useFieldControl } from "./Field";

/** A text input, 40 px; inside a Field it takes the field's id, help and validity. */
export const Input = forwardRef<HTMLInputElement, ComponentPropsWithoutRef<"input">>(function Input({ className, ...props }, ref) {
  const field = useFieldControl();
  return (
    <input
      ref={ref}
      id={field?.id}
      aria-describedby={field?.helpId}
      aria-invalid={field?.invalid || undefined}
      className={cn(CONTROL, className)}
      {...props}
    />
  );
});
