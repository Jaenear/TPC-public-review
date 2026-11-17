import test from "node:test";
import assert from "node:assert/strict";
import { createPreviewFetch, routeKey } from "./static-preview-api.mjs";

const origin = "https://jaenear.github.io";
const routes = {
  "/api/session": { status: 200, body: { user: null } },
  "/api/community/posts?board=general": { status: 200, body: { posts: [], total: 0 } },
  "/api/community/posts?board=study": { status: 401, body: { error: "로그인 후 이용해주세요." } },
  "/api/qa/questions": { status: 401, body: { error: "로그인 후 이용해주세요." } },
};
const manifest = { format: 1, mode: "static-read-only", routes };
const noNetwork = () => { throw Error("Unexpected network request"); };
const fetchPreview = createPreviewFetch(manifest, noNetwork, origin);

test("manifest is mandatory and query canonicalization is stable", () => {
  assert.throws(() => createPreviewFetch({}, noNetwork, origin));
  assert.equal(routeKey("/api/x?z=1&context=account&mode=live"), "/api/x?context=account&mode=live&z=1");
});
test("anonymous session stays anonymous and HEAD has no body", async () => {
  assert.deepEqual(await (await fetchPreview("/api/session")).json(), { user: null });
  assert.equal(await (await fetchPreview("/api/session", { method: "HEAD" })).text(), "");
});
test("every mutation is rejected, including a Request method", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    const response = await fetchPreview("/api/auth/register", { method });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).code, "static_preview_read_only");
  }
  assert.equal((await fetchPreview(new Request(origin + "/api/watch", { method: "PUT" }))).status, 503);
});
test("private and paid routes cannot fall back to catalog content", async () => {
  for (const path of ["/api/me", "/api/manage/users", "/api/studio/courses", "/api/courses/live-cash/lessons/multiway", "/api/community/posts/real-user-post"]) {
    assert.equal((await fetchPreview(path)).status, 401, path);
  }
});
test("search works on public listings while private boards retain 401", async () => {
  const publicResult = await fetchPreview("/api/community/posts?board=general&q=test&page=1");
  assert.deepEqual(await publicResult.json(), { posts: [], total: 0 });
  assert.equal((await fetchPreview("/api/community/posts?board=study&q=test")).status, 401);
  assert.equal((await fetchPreview("/api/community/posts?board=general&ownerId=other")).status, 401);
  assert.equal((await fetchPreview("/api/community/posts?board=unknown")).status, 401);
  assert.equal((await fetchPreview("/api/qa/questions?course=private")).status, 401);
});
test("normal files and external origins use the original fetch unchanged", async () => {
  const calls = [];
  const api = createPreviewFetch(manifest, (...args) => { calls.push(args); return "delegated"; }, origin);
  assert.equal(await api("/TPC-public-review/preview-data.json"), "delegated");
  assert.equal(await api("https://other.example/api/session"), "delegated");
  assert.equal(calls.length, 2);
});
test("returned objects are isolated from subsequent calls", async () => {
  const data = await (await fetchPreview("/api/session")).json();
  data.user = { id: "fake" };
  assert.equal((await (await fetchPreview("/api/session")).json()).user, null);
});
