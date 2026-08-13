/**
 * Client-side mirror of the server's canonical HTTPS origin rules.
 *
 * The authority is `canonicalize_https_origin` / `validate_repository_identity` in
 * `apps/api/src/lineage_api/application/repository_sources.py`. The server accepts an
 * origin only when it is already byte-identical to its own canonical form, so anything
 * the server would rewrite — a `.git` suffix, a trailing slash, an explicit `:443`, an
 * uppercase host — is a rejection, not a normalization.
 *
 * The raw string is parsed here rather than with `new URL`. WHATWG host parsing is not
 * the same function as Python's `urlsplit`: it rewrites numeric-looking hosts
 * (`127.1` and `0x7f.1` both become `127.0.0.1`) and throws on others (`example.123`),
 * while `urlsplit` leaves all three untouched and the server accepts them. Relying on
 * `URL` therefore produced a mirror that disagreed with the server on exactly those
 * spellings.
 */

const HTTPS_PREFIX = "https://";
const HTTPS_HOST = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/;
const HTTPS_PATH = /^\/[A-Za-z0-9._~!$&'()*+,;=:@/-]+$/;
const REPOSITORY_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/;
const PRINTABLE_ASCII = /^[\x21-\x7e]+$/;
const AUTHORITY_AND_REST = /^([^/?#]*)([^?#]*)(\?[^#]*)?(#[\s\S]*)?$/;
const PORT = /^[0-9]+$/;


/**
 * Returns a human-readable problem, or null when the pair would satisfy the server.
 */
export function repositoryOriginProblem(
  origin: string,
  repository: string,
): string | null {
  if (!origin) return "Repository origin is required.";
  if (!PRINTABLE_ASCII.test(origin)) {
    return "Repository origin must be printable ASCII with no spaces.";
  }
  if (origin.includes("\\")) return "Repository origin must not contain a backslash.";
  if (!origin.startsWith(HTTPS_PREFIX)) return "Repository origin must use HTTPS.";

  const parts = AUTHORITY_AND_REST.exec(origin.slice(HTTPS_PREFIX.length));
  if (!parts) return "Repository origin must be a canonical HTTPS repository URL.";
  const [, authority, rawPath, query, fragment] = parts;

  if (query) return "Repository origin must not carry a query string.";
  if (fragment) return "Repository origin must not carry a fragment.";
  if (authority.includes("@")) {
    return "Credential-bearing repository origins are forbidden.";
  }
  if (authority.includes("[") || authority.includes("]")) {
    return "Repository origin host is not a valid public hostname.";
  }

  const separator = authority.lastIndexOf(":");
  const host = separator === -1 ? authority : authority.slice(0, separator);
  const port = separator === -1 ? "" : authority.slice(separator + 1);
  if (separator !== -1 && !PORT.test(port)) {
    return "Repository origin port is not valid.";
  }
  if (port && Number(port) === 0) return "Repository origin port is not valid.";
  if (port === "443") {
    return "Drop the explicit :443 — the canonical origin omits the default port.";
  }
  if (host !== host.toLowerCase()) {
    return "Repository origin host must be lowercase.";
  }
  if (!HTTPS_HOST.test(host)) {
    return "Repository origin host is not a valid public hostname.";
  }

  if (rawPath === "" || rawPath === "/") {
    return "Repository origin must include a repository path.";
  }
  if (rawPath.endsWith("/")) {
    return "Drop the trailing slash — the canonical origin has none.";
  }
  if (rawPath.endsWith(".git")) {
    return "Drop the .git suffix — the canonical origin has none.";
  }
  if (!HTTPS_PATH.test(rawPath)) {
    return "Repository origin must include a repository path.";
  }
  const segments = rawPath.split("/").slice(1);
  if (segments.some((part) => part === "" || part === "." || part === "..")) {
    return "Repository origin path must not contain empty or relative segments.";
  }

  // The server rebuilds the origin from its parts and refuses anything it would rewrite.
  const canonicalAuthority = port ? `${host}:${port}` : host;
  if (`${HTTPS_PREFIX}${canonicalAuthority}${rawPath}` !== origin) {
    return "Repository origin must already be in canonical form.";
  }

  if (!REPOSITORY_NAME.test(repository)) {
    return "Repository must be a safe repository name.";
  }
  if (segments[segments.length - 1] !== repository) {
    return `Repository must match the last path segment of the origin ("${segments[segments.length - 1]}").`;
  }
  return null;
}
