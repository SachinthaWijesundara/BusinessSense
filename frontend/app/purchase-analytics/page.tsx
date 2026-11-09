/**
 * Purchase Analytics screen (proposal): price history per product with Model 1's expected range.
 * Waits for the backend's Forecast API, so it only explains that for now.
 */

import { EmptyScreen } from "@/components/EmptyScreen";

/** The Purchase Analytics placeholder. */
export default function PurchaseAnalyticsPage() {
  return (
    <EmptyScreen
      title="Purchase Analytics"
      blurb="Price history for each product with the Model 1 expected range, flagged prices, and spend by month and by supplier."
      needs="Needs Model 1 (item price intelligence) and the Forecast API in the backend. They are not built yet."
    />
  );
}
