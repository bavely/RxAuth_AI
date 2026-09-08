import { describe, expect, it } from "vitest";

import { citationStatusMessage, isTextMedia, resolveCitation } from "@/lib/citations";
import type { Provenance } from "@/lib/types";

/**
 * The document below is shaped like a real one from `data/cases/PA-CASE-001`:
 * labelled lines, one page, offsets counted from the first character.
 */
const DOCUMENT = [
  "PRIOR AUTHORIZATION REQUEST",
  "Patient ID: SYNTH-0001",
  "Diagnosis: Example Condition",
  "Requested medication: Drug A",
].join("\n");

const DIAGNOSIS_START = DOCUMENT.indexOf("Diagnosis: Example Condition");
const DIAGNOSIS_END = DIAGNOSIS_START + "Diagnosis: Example Condition".length;

function provenance(overrides: Partial<Provenance> = {}): Provenance {
  return {
    document_id: "D1",
    filename: "01_pa_request.txt",
    page: 1,
    start_char: DIAGNOSIS_START,
    end_char: DIAGNOSIS_END,
    source_text: "Diagnosis: Example Condition",
    ...overrides,
  };
}

describe("resolveCitation", () => {
  it("lands the span exactly where the citation says it does", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
    });

    expect(resolved.status).toBe("verified");
    expect(resolved.span).toBe("Diagnosis: Example Condition");
  });

  it("returns the surrounding lines so a quote can be read in context", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
    });

    // A quote a reviewer cannot see the neighbours of is a quote they have to
    // take on trust, which is the thing this whole project refuses to ask for.
    expect(resolved.before).toContain("Patient ID: SYNTH-0001");
    expect(resolved.after).toContain("Requested medication: Drug A");
  });

  it("reports a mismatch rather than highlighting the wrong text confidently", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ start_char: 0, end_char: 10 }),
    });

    expect(resolved.status).toBe("mismatch");
    expect(resolved.span).toBe("PRIOR AUTH");
    expect(resolved.quoted).toBe("Diagnosis: Example Condition");
  });

  it("refuses a range that runs past the end of the document", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ start_char: 10, end_char: DOCUMENT.length + 50 }),
    });

    expect(resolved.status).toBe("out_of_range");
    expect(resolved.span).toBe("");
  });

  it("refuses an inverted range", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ start_char: 40, end_char: 10 }),
    });

    expect(resolved.status).toBe("out_of_range");
  });

  it("says so when the citation carries no character range", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ start_char: null, end_char: null }),
    });

    expect(resolved.status).toBe("no_span");
  });

  it("declines to index a multi-page document", () => {
    // Offsets are positions within one ingested page. Slicing the whole file
    // would highlight unrelated text with complete confidence.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 4,
      provenance: provenance(),
    });

    expect(resolved.status).toBe("page_offsets_unavailable");
    expect(resolved.span).toBe("");
  });

  it("declines when the document is not text", () => {
    const resolved = resolveCitation({
      documentText: null,
      pageCount: 1,
      provenance: provenance(),
    });

    expect(resolved.status).toBe("not_text");
  });

  it("accepts a span with no quoted text as verified", () => {
    // Nothing to contradict: the range resolved and the citation made no claim
    // about what it would find.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ source_text: null }),
    });

    expect(resolved.status).toBe("verified");
    expect(resolved.quoted).toBeNull();
  });

  it("keeps context inside the document at both edges", () => {
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance({ start_char: 0, end_char: DOCUMENT.length, source_text: null }),
    });

    expect(resolved.before).toBe("");
    expect(resolved.after).toBe("");
    expect(resolved.span).toBe(DOCUMENT);
  });

  it("trims a partial first line rather than starting context mid-word", () => {
    // 30 characters back from the span lands inside the title line. Context
    // should begin at the next line start, not halfway through a word.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
      contextChars: 30,
    });

    expect(resolved.before.startsWith("Patient")).toBe(true);
  });

  it("keeps a partial line when the window is too small to reach a boundary", () => {
    // Snapping forward here would land on the span's own line start and return
    // nothing. Some context beats none.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
      contextChars: 12,
    });

    expect(resolved.before).not.toBe("");
    expect(resolved.before).toContain("SYNTH-0001");
  });

  it("gives context after a span that ends its own line", () => {
    // The same regression on the trailing side: a span that ends at a newline
    // would otherwise stop the excerpt dead at the newline after it.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
    });

    expect(resolved.after).not.toBe("");
    expect(resolved.after).toContain("Requested medication: Drug A");
  });

  it("gives context before a span that starts its own line", () => {
    // The regression the first implementation had: every citation in this
    // corpus starts a line, so snapping backward returned an empty `before`
    // for all of them and the context panel was silently useless.
    const resolved = resolveCitation({
      documentText: DOCUMENT,
      pageCount: 1,
      provenance: provenance(),
    });

    expect(resolved.before).not.toBe("");
  });
});

describe("citationStatusMessage", () => {
  it("says nothing when the citation verified", () => {
    expect(citationStatusMessage("verified")).toBeNull();
  });

  it("tells a reviewer a mismatched citation is unverified", () => {
    expect(citationStatusMessage("mismatch")).toContain("unverified");
  });

  it("explains the multi-page limitation instead of hiding it", () => {
    expect(citationStatusMessage("page_offsets_unavailable")).toContain("cited page");
  });
});

describe("isTextMedia", () => {
  it("accepts the text types the upload path allows", () => {
    expect(isTextMedia("text/plain")).toBe(true);
    expect(isTextMedia("text/markdown; charset=utf-8")).toBe(true);
    expect(isTextMedia("TEXT/PLAIN")).toBe(true);
  });

  it("rejects everything a browser would have to render itself", () => {
    expect(isTextMedia("application/pdf")).toBe(false);
    expect(isTextMedia("image/png")).toBe(false);
    expect(isTextMedia(null)).toBe(false);
    expect(isTextMedia(undefined)).toBe(false);
  });
});
