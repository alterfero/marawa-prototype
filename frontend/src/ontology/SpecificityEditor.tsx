import { useId } from "react";
import type { OntologyNode } from "../api/types";
import { specificityLevels } from "./relationships";

export function SpecificityEditor({ node, busy, onSave }: {
  node: OntologyNode; busy: boolean; onSave: (level: number) => void;
}) {
  const inputId = useId();
  return <form className="ontology-actions" aria-label={`Specificity of ${node.text}`} onSubmit={e => {
    e.preventDefault();
    onSave(Number(new FormData(e.currentTarget).get("level")));
  }}>
    <div><label htmlFor={inputId}>Specificity level</label>{" "}
      <select id={inputId} className="input" name="level" key={`${node.id}:${node.specificity_level}`}
      disabled={busy} defaultValue={node.specificity_level}>
      {specificityLevels.map(level => <option key={level}>{level}</option>)}
      </select></div>
    <button className="button" disabled={busy}>Save curator rating</button>
  </form>;
}
