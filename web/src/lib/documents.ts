import "server-only";

import { fetchCitedDocument } from "@/lib/api";
import { isTextMedia } from "@/lib/citations";
import type { AssembledDocument } from "@/lib/types";

/**
 * Loading the documents a run cited, so citations can be shown in place.
 *
 * The bytes are fetched by the server and the text is passed to the page as
 * props. The browser never calls the API, so a patient document is never the
 * subject of a client-side request that could be cached, logged by an
 * extension, or replayed from history.
 */

/**
 * Above this, a document is linked rather than inlined.
 *
 * Uploads are capped at 25 MiB and a reviewer's browser should not be asked to
 * lay out a document of that size to show one highlighted line. The cap is
 * about rendering, not trust.
 */
const MAX_INLINE_BYTES = 2 * 1024 * 1024;

export interface CitedDocument {
  documentId: string;
  filename: string;
  pageCount: number;
  mediaType: string | null;
  /** The document decoded as text, or null when it cannot be shown inline. */
  text: string | null;
  /** Why the text is absent, when it is. Shown to the reviewer, not swallowed. */
  unavailable: string | null;
}

function mediaTypeOf(response: Response): string | null {
  return response.headers.get("content-type");
}

async function loadOne(runId: string, document: AssembledDocument): Promise<CitedDocument> {
  const base: Omit<CitedDocument, "text" | "unavailable" | "mediaType"> = {
    documentId: document.id,
    filename: document.filename,
    pageCount: document.page_count,
  };

  let response: Response;
  try {
    response = await fetchCitedDocument(runId, document.id);
  } catch (error) {
    return {
      ...base,
      mediaType: null,
      text: null,
      unavailable: error instanceof Error ? error.message : String(error),
    };
  }

  const mediaType = mediaTypeOf(response);
  if (!isTextMedia(mediaType)) {
    // Not an error: a PDF or a scan is fetched by the viewer route instead.
    return { ...base, mediaType, text: null, unavailable: null };
  }

  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > MAX_INLINE_BYTES) {
    return {
      ...base,
      mediaType,
      text: null,
      unavailable: `This document is ${Math.round(declared / 1024)} KiB, too large to show inline.`,
    };
  }

  return { ...base, mediaType, text: await response.text(), unavailable: null };
}

/**
 * Every document a run cited, keyed by the run-local id a citation carries.
 *
 * One document failing does not fail the page. A run started from the CLI
 * stores no object at all, and a reviewer should still see the criteria and the
 * quoted spans, with the reason the document itself is missing.
 */
export async function loadCitedDocuments(
  runId: string,
  documents: AssembledDocument[],
): Promise<Map<string, CitedDocument>> {
  const loaded = await Promise.all(documents.map((document) => loadOne(runId, document)));
  return new Map(loaded.map((document) => [document.documentId, document]));
}
