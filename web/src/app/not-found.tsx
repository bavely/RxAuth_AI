import Link from "next/link";

/**
 * The 404 page, and a real 404 status.
 *
 * Rendering "not found" inside a 200 response would make every monitor, log,
 * and crawler read a missing case as a healthy page. `notFound()` from the page
 * gets the status right and lands here.
 *
 * The wording matters as much as the code. The API answers "absent" and
 * "belongs to another organization" identically on purpose — a guessed
 * identifier must not reveal that something exists elsewhere — so this page
 * says both rather than implying the resource is gone.
 */
export default function NotFound() {
  return (
    <>
      <div className="page-head">
        <h1>Not found</h1>
      </div>
      <div className="notice notice-warn">
        <h2>No such case or run here</h2>
        <p>
          It does not exist, or it belongs to another organization. The API answers both the same
          way on purpose, so a guessed identifier reveals nothing about what other tenants hold.
        </p>
        <p>
          <Link href="/">Back to the worklist</Link>
        </p>
      </div>
    </>
  );
}
