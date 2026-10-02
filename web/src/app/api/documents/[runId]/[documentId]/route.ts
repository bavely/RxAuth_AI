import { ApiError, fetchCitedDocument } from "@/lib/api";

/**
 * Serve one cited document to the reviewer's browser, through this server.
 *
 * The browser cannot call the API itself — it has no token, by design — so a
 * PDF or a scan needs a same-origin URL to point an `<embed>` or a link at.
 * This route is that URL and nothing more: it forwards one request, with the
 * organization scoping and the SHA-256 integrity check still done by the API.
 *
 * The disposition and `nosniff` headers are preserved rather than relaxed.
 * These are patient documents from an upload path that accepts several formats,
 * and rendering one inline in this origin would make the reviewer UI itself the
 * XSS surface the API took care to avoid being.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string; documentId: string }> },
) {
  const { runId, documentId } = await params;

  let upstream: Response;
  try {
    upstream = await fetchCitedDocument(runId, documentId);
  } catch (error) {
    if (error instanceof ApiError) {
      return Response.json({ detail: error.detail }, { status: error.status });
    }
    return Response.json(
      { detail: error instanceof Error ? error.message : "The document could not be read." },
      { status: 502 },
    );
  }

  const headers = new Headers();
  for (const header of ["content-type", "content-length", "content-disposition"]) {
    const value = upstream.headers.get(header);
    if (value) headers.set(header, value);
    }
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Cache-Control", "no-store, private");

  return new Response(upstream.body, { status: upstream.status, headers });
}
