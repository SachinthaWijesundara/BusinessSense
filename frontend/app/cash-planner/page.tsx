/**
 * Purchasing Cash Planner screen (proposal): dated supplier obligations, the Model 2 forecast and
 * the recommended purchasing cash reserve. Waits for those backend parts, so it only explains that.
 */

import { EmptyScreen } from "@/components/EmptyScreen";

/** The Purchasing Cash Planner placeholder. */
export default function CashPlannerPage() {
  return (
    <EmptyScreen
      title="Purchasing Cash Planner"
      blurb="A dated list of supplier obligations, the Model 2 forecast with its baseline, and the recommended purchasing cash reserve."
      needs="Needs Model 2 (purchasing expenditure forecast), supplier payments and the cash planner rules in the backend. They are not built yet. Payments due soon are already on the Dashboard."
    />
  );
}
