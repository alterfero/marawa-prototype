import { ExplorationFilterSetTermPicker } from "./ExplorationFilterSetTermPicker";
import type { ExplorationAppliedTropeFilter } from "../api/types";

export function ExplorationFilterSetTropePicker({
  loading,
  query,
  selectedTropes,
  onQueryChange,
  onToggleTrope,
}: {
  loading: boolean;
  query: string;
  selectedTropes: ExplorationAppliedTropeFilter[];
  onQueryChange: (value: string) => void;
  onToggleTrope: (trope: ExplorationAppliedTropeFilter) => void;
}) {
  return (
    <div className="exploration-trope-filter-builder">
      <ExplorationFilterSetTermPicker
        kind="trope"
        loading={loading}
        onQueryChange={onQueryChange}
        onToggleTerm={onToggleTrope}
        query={query}
        selectedTerms={selectedTropes}
        showSimilarityThreshold={false}
      />
    </div>
  );
}
