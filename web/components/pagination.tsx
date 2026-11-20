import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";

export function buildQuery(
  params: Record<string, string | undefined>,
  overrides: Record<string, string | undefined> = {},
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...params, ...overrides })) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const value = query.toString();
  return value ? `?${value}` : "";
}

export function Pagination({
  total,
  limit,
  offset,
  params = {},
}: {
  total: number;
  limit: number;
  offset: number;
  params?: Record<string, string | undefined>;
}) {
  if (total <= 0) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const previous = Math.max(0, offset - limit);
  const next = offset + limit;
  const rangeStart = offset + 1;
  const rangeEnd = Math.min(offset + limit, total);

  return (
    <div className="pagination">
      <span className="small muted">
        {rangeStart}–{rangeEnd} of {total}
      </span>
      <div className="row" style={{ gap: 8 }}>
        <Link
          className={`pagination__link${offset === 0 ? " is-disabled" : ""}`}
          aria-disabled={offset === 0}
          href={offset === 0 ? "#" : buildQuery(params, { offset: previous ? String(previous) : undefined })}
        >
          <ChevronLeft size={14} /> Previous
        </Link>
        <span className="small muted">
          Page {page} / {pages}
        </span>
        <Link
          className={`pagination__link${next >= total ? " is-disabled" : ""}`}
          aria-disabled={next >= total}
          href={next >= total ? "#" : buildQuery(params, { offset: String(next) })}
        >
          Next <ChevronRight size={14} />
        </Link>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
