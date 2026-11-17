// This adapter belongs to the generated public preview, never the live app.
export const PREVIEW_MESSAGE = "공개 미리보기입니다. 로그인·회원별 진도 저장·글쓰기·결제는 서버 연결 후 이용할 수 있습니다.";

export function routeKey(input) {
  const url = new URL(input, "https://tpc-static-preview.example");
  const params = [...url.searchParams.entries()].sort(([a, av], [b, bv]) => a.localeCompare(b) || av.localeCompare(bv));
  const query = new URLSearchParams(params).toString();
  return url.pathname + (query ? "?" + query : "");
}

export function createPreviewFetch(manifest, nativeFetch, origin) {
  if (manifest.format !== 1 || manifest.mode !== "static-read-only") throw new Error("Invalid preview manifest");
  const json = (body, status) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
  return async (input, options = {}) => {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, origin);
    if (url.origin !== origin || !url.pathname.startsWith("/api/")) return nativeFetch(input, options);
    const method = (options.method || input?.method || "GET").toUpperCase();
    if (!["GET", "HEAD"].includes(method)) return json({ error: PREVIEW_MESSAGE, code: "static_preview_read_only" }, 503);
    let entry = manifest.routes[routeKey(url.href)];
    // Public empty listings support navigation/search without inventing content.
    // Never normalize a private route into a public one or erase its 401 status.
    if (!entry && url.pathname === "/api/community/posts") {
      const allowed = new Set(["board", "page", "pageSize", "q", "search", "sort"]);
      if ([...url.searchParams.keys()].every(key => allowed.has(key))) {
        const board = url.searchParams.get("board") || "general";
        entry = manifest.routes[routeKey("/api/community/posts?board=" + encodeURIComponent(board))];
      }
    }
    if (!entry && url.pathname === "/api/coaching/products") entry = manifest.routes[url.pathname];
    if (!entry && url.pathname === "/api/qa/questions") entry = manifest.routes[url.pathname];
    if (!entry && url.pathname === "/api/policies/current" && !url.searchParams.has("courseId")) {
      entry = manifest.routes[routeKey("/api/policies/current?mode=" + encodeURIComponent(url.searchParams.get("mode") || "live") + (url.searchParams.get("context") === "account" ? "&context=account" : ""))];
    }
    if (!entry) return json({ error: PREVIEW_MESSAGE, code: "static_preview_server_required" }, 401);
    if (method === "HEAD") return new Response(null, { status: entry.status, headers: { "Content-Type": "application/json; charset=utf-8" } });
    return json(entry.body, entry.status);
  };
}

export function showPreviewInfo() {
  const dialog = document.querySelector("#app-dialog");
  dialog.innerHTML = `<form method="dialog" class="preview-info"><p class="eyebrow">TPC · PUBLIC PREVIEW</p><h2>지금 둘러볼 수 있는 범위</h2><p>강의 라이브러리, 무료 수업과 샘플 영상, 학습 도구, 코치 소개와 공개 게시판 화면을 확인할 수 있습니다.</p><p>${PREVIEW_MESSAGE}</p><p>이 화면에서는 계정을 만들거나 개인정보를 입력받지 않습니다. 영상의 현재 재생 비율은 볼 수 있으며, 다음 방문을 위한 회원별 저장은 제공하지 않습니다.</p><button class="btn btn-primary" type="submit">확인</button></form>`;
  if (!dialog.open) dialog.showModal();
}

export function installStaticPreview(manifest) {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = createPreviewFetch(manifest, nativeFetch, location.origin);
}
