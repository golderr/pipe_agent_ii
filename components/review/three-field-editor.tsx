"use client";

import { Input } from "@/components/ui/input";
import { formatValue } from "@/lib/review/payload";
import type { ReviewValueChangePayload } from "@/lib/review/types";
import { cn } from "@/lib/utils";

type ThreeFieldEditorProps = {
  valueChange: ReviewValueChangePayload;
  resultValue: string;
  onResultChange?: (value: string) => void;
  editable?: boolean;
  compact?: boolean;
};

export function ThreeFieldEditor({
  valueChange,
  resultValue,
  onResultChange,
  editable = true,
  compact = false
}: ThreeFieldEditorProps) {
  return (
    <div
      className={cn(
        "grid gap-3",
        compact ? "md:grid-cols-3" : "lg:grid-cols-3"
      )}
    >
      <ValueCell label="Current" value={valueChange.currentValue} compact={compact} />
      <ValueCell label="Evidence" value={valueChange.evidenceValue} compact={compact} />
      <div className="min-w-0 rounded-md border border-teal-200 bg-teal-50/60 p-3">
        <p className="text-xs font-medium uppercase tracking-normal text-teal-800">Result</p>
        <div className="mt-2">
          {editable ? (
            <ResultInput
              valueChange={valueChange}
              value={resultValue}
              onChange={onResultChange ?? (() => undefined)}
            />
          ) : (
            <p className="break-words text-sm text-slate-950">{formatValue(resultValue)}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ValueCell({
  label,
  value,
  compact
}: {
  label: string;
  value: unknown;
  compact: boolean;
}) {
  return (
    <div className={cn("min-w-0 rounded-md border border-slate-200 bg-slate-50 p-3", compact && "p-2")}>
      <p className="text-xs font-medium uppercase tracking-normal text-slate-500">{label}</p>
      <p className={cn("mt-2 break-words text-slate-950", compact ? "text-sm" : "text-base")}>
        {formatValue(value)}
      </p>
    </div>
  );
}

function ResultInput({
  valueChange,
  value,
  onChange
}: {
  valueChange: ReviewValueChangePayload;
  value: string;
  onChange: (value: string) => void;
}) {
  const enumValues = valueChange.constraints.enumValues ?? [];
  if (enumValues.length > 0) {
    return (
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
      >
        <option value="">Select value</option>
        {enumValues.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  if (valueChange.fieldType === "integer" || valueChange.fieldType === "decimal") {
    return (
      <Input
        type="number"
        min={valueChange.constraints.min}
        max={valueChange.constraints.max}
        step={valueChange.fieldType === "integer" ? 1 : "any"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  if (valueChange.fieldType === "date") {
    return (
      <Input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return <Input value={value} onChange={(event) => onChange(event.target.value)} />;
}
