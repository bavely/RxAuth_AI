import { citationStatusMessage, resolveCitation } from "@/lib/citations";
import type { CitedDocument } from "@/lib/documents";
import type { Provenance } from "@/lib/types";

/**
 * One citation, shown in the document it points at wherever that is possible.
 *
 * The quoted span alone is not verification — a quote can be accurate and still
 * be the wrong half of a sentence. Where the document is text and single-page,
 * the surrounding lines are rendered with the cited range highlighted, so a
 * reviewer sees the quote *and* what sits either side of it.
 */
export function Citation({
  provenance,
  document,
  label,
}: {
  provenance: Provenance;
  document?: CitedDocument;
  label?: string;
}) {
  const resolved = resolveCitation({
    documentText: document?.text ?? null,
    pageCount: document?.pageCount ?? 1,
    provenance,
  });
  const message = citationStatusMessage(resolved.status);

  const tone =
    resolved.status === "verified"
      ? "citation-verified"
      : resolved.status === "mismatch"
        ? "citation-mismatch"
        : "citation-unresolved";

  return (
    <div className={`citation ${tone}`}>
      <div className="citation-source">
        {label && <span className="badge badge-neutral">{label}</span>}
        <span>{provenance.filename ?? provenance.document_id ?? "unknown document"}</span>
        {provenance.page !== null && <span>page {provenance.page}</span>}
        {provenance.start_char !== null && provenance.end_char !== null && (
          <span>
            chars {provenance.start_char}–{provenance.end_char}
          </span>
        )}
        {resolved.status === "verified" && <span className="badge badge-ok">verified</span>}
        {resolved.status === "mismatch" && <span className="badge badge-bad">mismatch</span>}
      </div>

      {resolved.status === "verified" || resolved.status === "mismatch" ? (
        <p className="excerpt">
          {resolved.before}
          <mark className={resolved.status === "mismatch" ? "excerpt-mismatch" : undefined}>
            {resolved.span}
          </mark>
          {resolved.after}
        </p>
      ) : (
        <p className="quoted">
          {resolved.quoted !== null ? `“${resolved.quoted}”` : <span className="faint">—</span>}
        </p>
      )}

      {resolved.status === "mismatch" && resolved.quoted !== null && (
        <p className="citation-warning citation-warning-bad">
          The run quoted “{resolved.quoted}”.
        </p>
      )}

      {message && (
        <p
          className={`citation-warning ${resolved.status === "mismatch" ? "citation-warning-bad" : ""}`}
        >
          {message}
        </p>
      )}

      {document?.unavailable && <p className="citation-warning">{document.unavailable}</p>}
    </div>
  );
}

/**
 * A policy citation.
 *
 * Policy documents live in the corpus on disk, not in object storage, so the
 * API serves no bytes for them and the span cannot be shown in context the way
 * a patient document's can. The quote and its exact location are still recorded,
 * which is what makes the requirement traceable.
 */
export function PolicyCitation({ provenance }: { provenance: Provenance }) {
  return (
    <div className="citation citation-unresolved">
      <div className="citation-source">
        <span className="badge badge-neutral">policy</span>
        <span>{provenance.filename ?? provenance.document_id ?? "unknown policy"}</span>
        {provenance.page !== null && <span>page {provenance.page}</span>}
        {provenance.start_char !== null && provenance.end_char !== null && (
          <span>
            chars {provenance.start_char}–{provenance.end_char}
          </span>
        )}
      </div>
      <p className="quoted">
        {provenance.source_text !== null ? (
          `“${provenance.source_text}”`
        ) : (
          <span className="faint">—</span>
        )}
      </p>
    </div>
  );
}
