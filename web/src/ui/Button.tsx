// Author: @SaadZarook
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, useId, type ComponentPropsWithoutRef } from "react";
import { cn } from "./cn";

/** Button's classes, for a link that acts as a button: <Link className={buttonClass()}> (rule 8). */
export const buttonClass = cva(
  "m-0 box-border inline-flex h-10 shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap " +
    "rounded-md border border-transparent px-4 text-sm font-semibold no-underline " +
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring " +
    "disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-primary text-primary-foreground hover:bg-primary-hover",
        outline: "border-input bg-card text-foreground hover:bg-background",
        ghost: "bg-transparent text-foreground hover:bg-background",
      },
      // Small only for toolbars and Copy (rule 2).
      size: { default: "", sm: "h-8 px-3 text-xs" },
    },
    defaultVariants: { variant: "primary", size: "default" },
  },
);

type ButtonProps = ComponentPropsWithoutRef<"button"> &
  VariantProps<typeof buttonClass> & {
    /** Why it can't be pressed: shown next to it whenever it is disabled (rule 8). */
    disabledReason?: string;
  };

/** The one button. type="button" unless given, so a button in a form never sends it by accident. */
// forwardRef, so a Radix trigger can give focus back to it (React 18).
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, disabledReason, type = "button", ...props },
  ref,
) {
  const reasonId = useId();
  const explained = props.disabled === true && disabledReason !== undefined;
  const button = (
    <button
      ref={ref}
      type={type}
      className={cn(buttonClass({ variant, size }), className)}
      {...props}
      aria-describedby={explained ? reasonId : props["aria-describedby"]}
    />
  );
  if (!explained) return button;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {button}
      <span id={reasonId} className="text-xs text-muted">
        {disabledReason}
      </span>
    </span>
  );
});
