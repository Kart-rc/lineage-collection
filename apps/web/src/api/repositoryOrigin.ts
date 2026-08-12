/**
 * Client-side mirror of the server's canonical HTTPS origin rules.
 *
 * The authority is `canonicalize_https_origin` / `validate_repository_identity` in
 * `apps/api/src/lineage_api/application/repository_sources.py`. The server accepts an
 * origin only when it is already byte-identical to its own canonical form, so anything
 * the server would rewrite — a `.git` suffix, a trailing slash, an explicit `:443`, an
 * uppercase host — is a rejection, not a normalization. Mirroring those rules here keeps
 * the form from accepting an origin the API will refuse with `INVALID_REQUEST`.
 *
 * Several checks read the raw string rather than the parsed URL on purpose: `new URL`
 * silently drops a default `:443` and lower-cases the host, which are exactly the two
 * rewrites the server treats as errors.
 */

const HTTPS_PREFIX = "https://";
const HTTPS_HOST = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/;
const HTTPS_PATH = /^\/[A-Za-z0-9._~!$&'()*+,;=:@/-]+$/;
const REPOSITORY_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/;
const PRINTABLE_ASCII = /^[\x21-\x7e]+$/;


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

  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return "Repository origin must be a canonical HTTPS repository URL.";
  }

  if (parsed.protocol !== "https:") return "Repository origin must use HTTPS.";
  if (parsed.username || parsed.password) {
    return "Credential-bearing repository origins are forbidden.";
  }
  if (parsed.search) return "Repository origin must not carry a query string.";
  if (parsed.hash) return "Repository origin must not carry a fragment.";

  // Read the authority from the raw string: URL parsing has already normalized away
  // the two rewrites the server rejects.
  const rawAuthority = origin.slice(HTTPS_PREFIX.length).split("/")[0];
  if (/:443$/.test(rawAuthority)) {
    return "Drop the explicit :443 — the canonical origin omits the default port.";
  }
  if (rawAuthority !== rawAuthority.toLowerCase()) {
    return "Repository origin host must be lowercase.";
  }
  if (!HTTPS_HOST.test(parsed.hostname)) {
    return "Repository origin host is not a valid public hostname.";
  }
  if (parsed.port && !/^[1-9][0-9]{0,4}$/.test(parsed.port)) {
    return "Repository origin port is not valid.";
  }

  if (parsed.pathname === "" || parsed.pathname === "/") {
    return "Repository origin must include a repository path.";
  }
  if (origin.endsWith("/")) {
    return "Drop the trailing slash — the canonical origin has none.";
  }
  if (parsed.pathname.endsWith(".git")) {
    return "Drop the .git suffix — the canonical origin has none.";
  }
  if (!HTTPS_PATH.test(parsed.pathname)) {
    return "Repository origin must include a repository path.";
  }
  const segments = parsed.pathname.split("/").slice(1);
  if (segments.some((part) => part === "" || part === "." || part === "..")) {
    return "Repository origin path must not contain empty or relative segments.";
  }

  // The server rebuilds the origin from its parts and refuses anything it would rewrite.
  // A non-default port is preserved; 443 and "no port" both render as bare host.
  const authority = parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
  if (`${HTTPS_PREFIX}${authority}${parsed.pathname}` !== origin) {
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
