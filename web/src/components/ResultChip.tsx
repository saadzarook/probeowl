// Author: @SaadZarook
import type { ExpectationEvaluation } from "../api/client";
import { RESULT_LABELS } from "../runFormat";

/** An expectation's result. A skip a connector's missing binding caused reads "N/A", and "Not applicable" to a screen reader (#25). */
export function ResultChip({ evaluation }: { evaluation: Pick<ExpectationEvaluation, "result" | "not_applicable"> }) {
  const notApplicable = evaluation.not_applicable === true;
  return (
    <span className="result" data-result={evaluation.result} data-not-applicable={notApplicable || undefined}>
      {notApplicable ? (
        <>
          <span aria-hidden="true">N/A</span>
          <span className="sr-only">Not applicable</span>
        </>
      ) : (
        RESULT_LABELS[evaluation.result]
      )}
    </span>
  );
}
