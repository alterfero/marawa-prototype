import { useId } from "react";

import { specificityLevels } from "../ontology/relationships";

const examples: Record<number, string> = {
  1: "Someone kills their animal sibling.",
  2: "Someone kills their animal brother.",
  3: "A boy kills his younger animal brother.",
  4: "A young boy fatally stabs his adopted younger brother, who is a snake.",
};

export function SpecificitySelect({ value, onChange, disabled = false }: {
  value: number | undefined;
  onChange: (level: number | undefined) => void;
  disabled?: boolean;
}) {
  const helpId = useId();
  return (
    <label className="field">
      <span>Specificity level</span>
      <select
        className="input"
        aria-describedby={helpId}
        disabled={disabled}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : undefined)}
      >
        <option value="">Automatic rating</option>
        {specificityLevels.map((level) => (
          <option key={level} value={level}>
            {level}{level === 1 ? " · Broadest" : level === 4 ? " · Most specific" : ""}
          </option>
        ))}
      </select>
      <span className="muted" id={helpId}>
        Higher levels describe more specific participants, actions, relationships, or circumstances.
        {value ? ` Example: ${examples[value]}` : " Choose a level or let Marawa rate the wording automatically."}
        {" "}Applies to newly created tropes; existing tropes keep their rating.
      </span>
    </label>
  );
}
