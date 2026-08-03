import type { KeyboardEvent } from "react";

export type ConfirmationStatus = "unconfirmed" | "canonical";

interface ConfirmationStatusSwitchProps {
  value: ConfirmationStatus;
  onChange: (value: ConfirmationStatus) => void;
  disabled?: boolean;
  ariaLabel: string;
  className?: string;
}

const STATUS_OPTIONS: Array<{ value: ConfirmationStatus; label: string }> = [
  { value: "unconfirmed", label: "Unconfirmed" },
  { value: "canonical", label: "Canonical" },
];

export function ConfirmationStatusSwitch({
  value,
  onChange,
  disabled = false,
  ariaLabel,
  className = "",
}: ConfirmationStatusSwitchProps) {
  function selectStatus(nextValue: ConfirmationStatus) {
    if (!disabled && nextValue !== value) {
      onChange(nextValue);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    let nextValue: ConfirmationStatus | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp" || event.key === "Home") {
      nextValue = "unconfirmed";
    }
    if (event.key === "ArrowRight" || event.key === "ArrowDown" || event.key === "End") {
      nextValue = "canonical";
    }
    if (!nextValue) {
      return;
    }

    event.preventDefault();
    selectStatus(nextValue);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-status="${nextValue}"]`)?.focus();
  }

  return (
    <div aria-label={ariaLabel} className={`confirmation-status-switch ${className}`.trim()} role="radiogroup">
      {STATUS_OPTIONS.map((option) => (
        <button
          aria-checked={value === option.value}
          className={`confirmation-status-switch-option confirmation-status-switch-option-${option.value} ${
            value === option.value ? "confirmation-status-switch-option-active" : ""
          }`}
          data-status={option.value}
          disabled={disabled}
          key={option.value}
          onClick={() => selectStatus(option.value)}
          onKeyDown={handleKeyDown}
          role="radio"
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
