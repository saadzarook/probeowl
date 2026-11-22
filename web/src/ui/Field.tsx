// Author: @SaadZarook
import { createContext, useContext, type ComponentProps, type ReactNode } from "react";
import { cn } from "./cn";

type FieldControl = { id: string; helpId: string; invalid: boolean };
const FieldContext = createContext<FieldControl | undefined>(undefined);

/** The id, description and validity the Field around a control gives it; nothing outside one. */
export function useFieldControl(): FieldControl | undefined {
  return useContext(FieldContext);
}

type FieldProps = {
  /** The control's id: the Input or Select inside picks it up. */
  id: string;
  label: ReactNode;
  help?: ReactNode;
  /** Shown in help's place, so the fields around it never move (rule 3). */
  error?: string;
  children: ReactNode;
};

/** A field in three rows: the label, a 40 px control, then a row kept for help or an error (rule 3). */
export function Field({ id, label, help, error, children }: FieldProps) {
  const helpId = `${id}-help`;
  const invalid = error !== undefined;
  return (
    <FieldContext.Provider value={{ id, helpId, invalid }}>
      {/* content-start: a field stretched to its row's tallest neighbour keeps label and control at the top. */}
      <div className="grid min-w-0 grid-rows-[auto_2.5rem_minmax(1.25rem,auto)] content-start gap-1">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        {children}
        <p
          id={helpId}
          className={cn("m-0 text-xs", invalid ? "text-production-text" : "text-muted")}
          role={invalid ? "alert" : undefined}
        >
          {error ?? help}
        </p>
      </div>
    </FieldContext.Provider>
  );
}

/** The form grid: one column below 640 px, two from 640, four from 1280 (rule 3). */
export function FormGrid({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2 xl:grid-cols-4", className)} {...props} />;
}
