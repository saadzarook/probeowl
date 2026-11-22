// Author: @SaadZarook
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { CONTROL, cn } from "./cn";
import { useFieldControl } from "./Field";

type SelectProps = ComponentPropsWithoutRef<"select"> & {
  /** Production's red, as on the environment selector. */
  tone?: "production";
};

/** The browser's own select, 40 px. Native, not Radix's: that one injects a <style>, which a strict CSP refuses (#105). */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({ className, tone, ...props }, ref) {
  const field = useFieldControl();
  return (
    <select
      ref={ref}
      id={field?.id}
      aria-describedby={field?.helpId}
      aria-invalid={field?.invalid || undefined}
      className={cn(CONTROL, tone === "production" && "border-production font-semibold text-production-text", className)}
      {...props}
    />
  );
});
