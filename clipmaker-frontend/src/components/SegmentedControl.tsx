interface Option<T extends string> {
  value: T;
  label: string;
  detail?: string;
  disabled?: boolean;
}

interface SegmentedControlProps<T extends string> {
  label: string;
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  compact?: boolean;
}

export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  compact = false,
}: SegmentedControlProps<T>) {
  return (
    <fieldset className={`field-group ${compact ? "compact" : ""}`}>
      <legend>{label}</legend>
      <div className="segment-control" style={{ "--segments": options.length } as CSSProperties}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === value ? "active" : ""}
            aria-pressed={option.value === value}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
          >
            <span>{option.label}</span>
            {option.detail ? <small>{option.detail}</small> : null}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
import type { CSSProperties } from "react";
