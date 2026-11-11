// Author: @SaadZarook
interface OwlMarkProps {
  className?: string;
}

/** The ProbeOwl mark, decorative; text beside it carries the name. */
export function OwlMark({ className }: OwlMarkProps) {
  return <img className={className} src="/owl.svg" alt="" />;
}
