import { repositoryOriginProblem } from "./repositoryOrigin";


const ORIGIN = "https://github.com/spring-projects/spring-petclinic";
const REPO = "spring-petclinic";


test("accepts an origin already in the server's canonical form", () => {
  expect(repositoryOriginProblem(ORIGIN, REPO)).toBeNull();
  expect(repositoryOriginProblem("https://example.com/acme/demo", "demo")).toBeNull();
  expect(repositoryOriginProblem("https://git.example.com:8443/acme/demo", "demo")).toBeNull();
});


// The server parses with urlsplit, which leaves numeric-looking hosts untouched. WHATWG
// host parsing does not: `new URL` rewrites 127.1 and 0x7f.1 to 127.0.0.1 and throws on
// example.123. Parsing the raw authority is what keeps these verdicts aligned.
test.each([
  ["https://127.1/acme/demo", "demo"],
  ["https://0x7f.1/acme/demo", "demo"],
  ["https://example.123/acme/demo", "demo"],
  ["https://127.0.0.1/acme/demo", "demo"],
  ["https://a/acme/demo", "demo"],
])("accepts %s, which WHATWG URL parsing would rewrite or reject", (origin, repository) => {
  expect(repositoryOriginProblem(origin, repository)).toBeNull();
});


test.each([
  ["https://user@github.com/acme/demo", "demo", /Credential/],
  ["https://github.com:0/acme/demo", "demo", /port is not valid/],
  ["https://github.com:abc/acme/demo", "demo", /port is not valid/],
  ["https://[::1]/acme/demo", "demo", /valid public hostname/],
  ["https://-bad.example/acme/demo", "demo", /valid public hostname/],
  ["https://github.com/acme//demo", "demo", /empty or relative segments/],
  ["https://github.com/acme/../demo", "demo", /empty or relative segments/],
  ["https://example.com\\acme/demo", "demo", /backslash/],
  ["", "demo", /required/],
])("rejects %s, matching the server", (origin, repository, expected) => {
  expect(repositoryOriginProblem(origin, repository)).toMatch(expected);
});


test.each([
  ["http://github.com/acme/demo", "demo", /HTTPS/],
  ["https://user:token@github.com/acme/demo", "demo", /Credential/],
  ["https://github.com/acme/demo?ref=main", "demo", /query/],
  ["https://github.com/acme/demo#frag", "demo", /fragment/],
  ["https://github.com/acme/demo.git", "demo", /\.git/],
  ["https://github.com/acme/demo/", "demo", /trailing slash/],
  ["https://github.com:443/acme/demo", "demo", /:443/],
  ["https://GitHub.com/acme/demo", "demo", /lowercase/],
  ["https://github.com", "demo", /repository path/],
  ["https://github.com/", "demo", /repository path/],
  ["not-a-url", "demo", /must use HTTPS/],
  ["https://github.com/acme/demo demo", "demo", /printable ASCII/],
])("rejects %s, which the server would refuse", (origin, repository, expected) => {
  const problem = repositoryOriginProblem(origin, repository);
  expect(problem).not.toBeNull();
  expect(problem).toMatch(expected);
});


test("requires the repository to match the last path segment", () => {
  const problem = repositoryOriginProblem(ORIGIN, "something-else");
  expect(problem).toMatch(/last path segment/);
  expect(problem).toContain("spring-petclinic");
});


test("rejects an unsafe repository name", () => {
  expect(repositoryOriginProblem("https://example.com/acme/demo", "../demo")).toMatch(
    /safe repository name/,
  );
});
