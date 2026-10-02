import type { Provenance } from "@/lib/types";

/**
 * Resolving a citation against the document it points at.
 *
 * A citation states a document, a page, and a character range. The API serves
 * the document's original bytes, so the range can be checked rather than
 * trusted — which is the difference between showing a reviewer a quote and
 * showing them the quote *in the document, where it actually sits*.
 *
 * This file is pure so it can be tested without a server, a browser, or a
 * database. It is the only real logic in the UI, and `citations.test.ts` is
 * where its edges are pinned.
 */

export type CitationStatus =
  /** The range lands on exactly the text the citation quoted. */
  | "verified"
  /** The range resolved, but to different text than the citation claims. */
  | "mismatch"
  /** The range runs past the end of the document. */
  | "out_of_range"
  /** The citation carries no character range. */
  | "no_span"
  /** The document is not text, so a character range cannot be shown in place. */
  | "not_text"
  /**
   * Offsets are positions within an *ingested page*, and a multi-page document's
   * raw bytes are not that page. Slicing anyway would highlight the wrong text
   * with total confidence, which is worse than declining.
   */
  | "page_offsets_unavailable";

export interface ResolvedCitation {
  status: CitationStatus;
  /** Text immediately before the span, for context. Empty unless resolved. */
  before: string;
  /** The span itself, as it appears in the document. */
  span: string;
  /** Text immediately after the span. */
  after: string;
  /** What the citation said the span was, when it said. */
  quoted: string | null;
}

export interface ResolveCitationInput {
  /** The served document decoded as text, or null when it is not text. */
  documentText: string | null;
  /** Pages the run recorded for this document. */
  pageCount: number;
  provenance: Provenance;
  /** How much surrounding text to return on each side. */
  contextChars?: number;
}

const DEFAULT_CONTEXT_CHARS = 320;

function empty(status: CitationStatus, quoted: string | null): ResolvedCitation {
  return { status, before: "", span: "", after: "", quoted };
}

/**
 * Walk back up to `limit` characters, then forward to a line boundary.
 *
 * Forward, not back to the nearest newline: a span almost always starts a line
 * in these documents, so snapping backward lands on the span's own line start
 * and returns no context at all. Going forward from the window's edge trims the
 * partial line instead of the context.
 *
 * The exception is a window too small to reach a line boundary, where snapping
 * would consume all of it. A partial line beats nothing.
 */
function snapBackward(text: string, from: number, limit: number): number {
  const floor = Math.max(0, from - limit);
  if (floor === 0) {
    return 0;
  }
  const newline = text.indexOf("\n", floor);
  if (newline === -1 || newline + 1 >= from) {
    return floor;
  }
  return newline + 1;
}

/**
 * The mirror of `snapBackward`: forward up to `limit`, then back to a boundary.
 *
 * Back, not forward to the nearest newline — a span usually *ends* a line, so
 * snapping forward would stop at the newline immediately after it and return no
 * trailing context. As above, a window too small to hold a whole line keeps the
 * partial one.
 */
function snapForward(text: string, from: number, limit: number): number {
  const ceiling = Math.min(text.length, from + limit);
  if (ceiling === text.length) {
    return text.length;
  }
  const newline = text.lastIndexOf("\n", ceiling);
  if (newline <= from + 1) {
    return ceiling;
  }
  return newline;
}

/** Place one citation in its document, and say whether it actually landed. */
export function resolveCitation(input: ResolveCitationInput): ResolvedCitation {
  const { documentText, pageCount, provenance } = input;
  const contextChars = input.contextChars ?? DEFAULT_CONTEXT_CHARS;
  const quoted = provenance.source_text;

  const { start_char: start, end_char: end } = provenance;
  if (start === null || end === null) {
    return empty("no_span", quoted);
  }
  if (documentText === null) {
    return empty("not_text", quoted);
  }
  if (pageCount > 1) {
    return empty("page_offsets_unavailable", quoted);
  }
  if (start > end || end > documentText.length) {
    return empty("out_of_range", quoted);
  }

  const span = documentText.slice(start, end);
  const before = documentText.slice(snapBackward(documentText, start, contextChars), start);
  const after = documentText.slice(end, snapForward(documentText, end, contextChars));
  const status: CitationStatus = quoted !== null && span !== quoted ? "mismatch" : "verified";

  return { status, before, span, after, quoted };
}

/** What to tell a reviewer about a citation that did not resolve cleanly. */
export function citationStatusMessage(status: CitationStatus): string | null {
  switch (status) {
    case "verified":
      return null;
    case "mismatch":
      return "The document text at this position does not match the quoted span. Treat this citation as unverified.";
    case "out_of_range":
      return "The cited range runs past the end of this document.";
    case "no_span":
      return "This citation records no character range.";
    case "not_text":
      return "This document is not text, so the quoted span cannot be highlighted in place. Open the document to check it.";
    case "page_offsets_unavailable":
      return "Character offsets are positions within one ingested page, and this document has several. Open the document and find the quoted span on the cited page.";
  }
}

/** True for media the reviewer can read as text in the page. */
export function isTextMedia(mediaType: string | null | undefined): boolean {
  if (!mediaType) return false;
  const normalized = mediaType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  return normalized.startsWith("text/") || normalized === "application/json";
}
