import { repositoryOriginProblem } from "./repositoryOrigin";


const ORIGIN = "https://github.com/spring-projects/spring-petclinic";
const REPO = "spring-petclinic";


test("accepts an origin already in the server's canonical form", () => {
  expect(repositoryOriginProblem(ORIGIN, REPO)).toBeNull();
  expect(repositoryOriginProblem("https://example.com/acme/demo", "demo")).toBeNull();
  expect(repositoryOriginProblem("https://git.example.com:8443/acme/demo", "demo")).toBeNull();
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
  ["not-a-url", "demo", /canonical HTTPS/],
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
