/**
 * A quiet sign that something is still happening (D41): a pulsing dot, a label, and a thin
 * bar that slides, or fills to the real share when progress is known. With "reduce motion"
 * on, it stays still and only the words say it's working.
 */
export function Working({
  label,
  progress = null,
  compact = false,
}: {
  label: string;
  progress?: number | null;
  compact?: boolean;
}) {
  const known = progress !== null && Number.isFinite(progress);
  const share = known ? Math.min(1, Math.max(0.04, progress as number)) : 0;
  return (
    <div
      className={`working${compact ? " compact" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span className="working-label">
        <span className="working-dot" aria-hidden="true" />
        {label}
      </span>
      <span
        className="working-bar"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? Math.round(share * 100) : undefined}
      >
        <span
          className={known ? "working-fill" : "working-fill sliding"}
          style={known ? { width: `${share * 100}%` } : undefined}
        />
      </span>
    </div>
  );
}
