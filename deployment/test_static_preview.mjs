import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createPreviewFetch, routeKey, loadPreviewSample, previewMediaUrl } from "./static-preview-api.mjs";

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

test("owned sample becomes a verified seekable Blob under a Pages subpath", async () => {
  const bytes = await readFile(new URL('../review-media/preflop-sample.mp4', import.meta.url));
  const sampleSha256 = '1d2cf29f94ecab4cdf0f1af93f28515b6da7f5c2e2a4f82f397d46ef9535dc39';
  const sampleId = 'asset_' + sampleSha256.slice(0,32);
  const calls = [];
  const blobURL = await loadPreviewSample({sampleSha256,sampleId}, async (url, options) => {
    calls.push({url,options}); return new Response(bytes);
  }, origin + '/TPC-public-review/');
  assert.ok(blobURL.startsWith('blob:'));
  assert.equal(previewMediaUrl(sampleId), blobURL);
  assert.equal(calls[0].url, origin + '/TPC-public-review/public-media/' + sampleId + '.mp4');
  assert.equal(calls[0].options.credentials, 'omit');
  assert.throws(() => previewMediaUrl('asset_private'));
  URL.revokeObjectURL(blobURL);
});
test("sample identity, integrity, request failure and size are checked", async () => {
  const sampleSha256 = '1d2cf29f94ecab4cdf0f1af93f28515b6da7f5c2e2a4f82f397d46ef9535dc39';
  const sampleId = 'asset_' + sampleSha256.slice(0,32);
  await assert.rejects(loadPreviewSample({sampleId:'../private',sampleSha256}, noNetwork, origin + '/'));
  await assert.rejects(loadPreviewSample({sampleId,sampleSha256}, async()=>new Response('denied',{status:403}), origin + '/'));
  await assert.rejects(loadPreviewSample({sampleId,sampleSha256}, async()=>new Response('wrong content'), origin + '/'));
  await assert.rejects(loadPreviewSample({sampleId,sampleSha256}, async()=>new Response(new Uint8Array(4*1024*1024+1)), origin + '/'));
});
