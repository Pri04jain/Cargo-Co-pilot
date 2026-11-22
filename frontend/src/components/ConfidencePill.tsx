/**
 * Small coloured pill showing a confidence percentage.
 * Green ≥ 80%, Yellow 60–79%, Red < 60%, Grey if null.
 */
export default function ConfidencePill({ value }: { value: number | null }) {
  if (value === null || value === undefined) {
    return <span className="text-gray-400 text-xs">—</span>;
  }
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "bg-green-100 text-green-700"
    : pct >= 60 ? "bg-yellow-100 text-yellow-700"
    : "bg-red-100 text-red-700";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {pct}%
    </span>
  );
}
