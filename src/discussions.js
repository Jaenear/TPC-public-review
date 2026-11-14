import { renderQAWorkspace, contentActions } from "./qa-workspace.js";
import { bindFileLinks } from "./qa-files.js";
import { renderCommunityBlocks } from "./community-blocks.js";
import { renderNotificationInbox } from "./notification-inbox.js";
import { renderNotificationSettings } from "./notification-settings.js";
import {
  renderCommunityWorkspace,
  commentFormHTML,
  bindCommentForm,
  redactCommunityView,
} from "./community-workspace.js";
import {
  CommentIntent,
  conversationGroups,
  commentPath,
  replyPath,
  postPath,
} from "./community-model.js";
import { esc, icon } from "./ui.js";
import { hasCourseAccess } from "./access.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  textBody,
  date,
  tag,
  statusLabel,
  pager,
  opsNav,
} from "./platform-ui.js";

export async function renderDiscussions(path, params, ctx) {
  const blockWorkspace = await renderCommunityBlocks(path, params, ctx);
  if (blockWorkspace) return blockWorkspace;
  const communityWorkspace = await renderCommunityWorkspace(path, params, ctx);
  if (communityWorkspace) return communityWorkspace;
  const workspace = await renderQAWorkspace(path, params, ctx);
  if (workspace) return workspace;
  if (path === "/community") return communityList(params, ctx);
  if (path === "/community/new") return postEditor(params, ctx);
  const edit = path.match(/^\/community\/post\/([a-z0-9_]+)\/edit$/);
  if (edit) return postEditor(params, ctx, edit[1]);
  const post = path.match(/^\/community\/post\/([a-z0-9_]+)$/);
  if (post) return postDetail(post[1], ctx, params);
  if (path === "/qa") return qaList(params, ctx);
  if (path === "/qa/new") return questionEditor(params, ctx);
  const question = path.match(/^\/qa\/([a-z0-9_]+)$/);
  if (question) return questionDetail(question[1], ctx);
  if (path === "/inbox") return renderNotificationInbox(params, ctx);
  if (path === "/inbox/preferences") return renderNotificationSettings(ctx);
  return null;
}

async function communityList(params, ctx) {
  let data;
  try {
    data = await ctx.api(`/api/community/posts?${params}`);
  } catch (error) {
    if (error.status === 401)
      return authGate(ctx, "회원들과 대화를 이어가세요.");
    throw error;
  }
  const board = data.board;
  return {
    html: `<section class="container p-page">${head("TPC COMMUNITY", "함께 배우는 캠프.", "핸드의 관점, 플레이 경험과 학습 루틴을 함께 나눕니다.", `<a class="btn btn-primary" href="#/community/new?board=${esc(board.id)}">글쓰기 ${icon("external", 17)}</a>`)}<div class="p-community-layout"><aside class="p-board-nav"><h2>커뮤니티</h2>${data.boards.map((b) => `<a class="${b.id === board.id ? "active" : ""}" href="#/community?board=${esc(b.id)}">${esc(b.title)}${b.read_access !== "public" ? icon("lock", 13) : ""}</a>`).join("")}<div class="p-board-note"><strong>서로의 판단을 존중해주세요.</strong><p>타인의 개인정보와 광고, 실제 베팅 중개 글은 올리지 않도록 함께 관리합니다.</p></div>${ctx.user ? '<a class="text-link" href="#/community/moderation/actions">내 조치 안내·이의 신청 ↗</a>' : ""}</aside><div><div class="p-list-toolbar"><div><h2>${esc(board.title)}</h2><p>${esc(board.description)} <b>${data.total}</b>개 글</p></div><form id="community-search" class="search-box" role="search">${icon("search", 16)}<input name="q" aria-label="게시글 검색" placeholder="제목·내용 검색" value="${esc(params.get("q") || "")}"/><button aria-label="검색" type="submit">${icon("arrow", 16)}</button></form></div>${data.posts.length ? `<div class="p-post-list">${data.posts.map((post) => `<a href="#/community/post/${esc(post.id)}"><div>${post.pinned ? tag("공지", "mint") : ""}${post.locked ? tag("댓글 잠금") : ""}<h3>${esc(post.title)}</h3><p>${esc(post.body.slice(0, 130))}</p><span>${esc(post.author)} · ${date(post.createdAt)}</span></div><div class="p-post-counts"><span>댓글 <b>${post.commentCount}</b></span><span>좋아요 <b>${post.likeCount}</b></span></div></a>`).join("")}</div>` : empty("아직 등록된 글이 없습니다.", "첫 글로 스터디의 대화를 시작해보세요.")} ${pager("/community", params, data.page, data.total)}</div></div></section>`,
    bind() {
      bindForm("#community-search", (data) => {
        const q = new URLSearchParams(params);
        q.set("q", data.q);
        q.delete("page");
        ctx.navigate(`/community?${q}`);
      });
    },
  };
}

async function postEditor(params, ctx, id) {
  if (!ctx.user) return authGate(ctx, "커뮤니티에 생각을 남겨보세요.");
  let post = id ? (await ctx.api(`/api/community/posts/${id}`)).post : null;
  if (post && post.userId !== ctx.user.id)
    return {
      html: `<section class="container p-page">${empty("본인이 작성한 글만 수정할 수 있습니다.", "게시글로 돌아가주세요.", `<a class="btn btn-outline" href="#/community/post/${id}">돌아가기</a>`)}</section>`,
    };
  const boardData = await ctx.api(
    `/api/community/posts?board=${encodeURIComponent(post?.boardId || params.get("board") || "general")}`,
  );
  return {
    html: `<section class="container p-page p-compose"><a class="back-link" href="#/community${post ? `/post/${esc(id)}` : ""}">← ${post ? "게시글" : "커뮤니티"}</a>${head("COMMUNITY", post ? "기록을 다듬어보세요." : "생각을 나누는 첫 문장.", "경험과 생각을 구체적으로 나눠주세요.")}<form class="p-panel" id="post-form" data-session-draft data-recovery-version="${esc(post ? `post:${post.id}:${post.version}` : "new")}" data-recovery-context="boardId"><label class="field">게시판<select name="boardId" ${post ? "disabled" : ""}>${boardData.boards
      .filter(
        (b) =>
          b.write_access !== "staff" ||
          ctx.user.role === "admin" ||
          ctx.user.roles?.includes("moderator"),
      )
      .map(
        (b) =>
          `<option value="${esc(b.id)}" ${b.id === (post?.boardId || boardData.board.id) ? "selected" : ""}>${esc(b.title)}</option>`,
      )
      .join(
        "",
      )}</select></label><label class="field">제목<input name="title" required minlength="3" maxlength="150" placeholder="어떤 이야기를 나누고 싶나요?" value="${esc(post?.title || "")}"/></label><label class="field">본문<textarea name="body" rows="15" required minlength="10" maxlength="20000" placeholder="학습 중인 주제, 경험한 핸드와 고민을 구체적으로 적어주세요.">${esc(post?.body || "")}</textarea></label><p class="form-note">개인정보나 다른 사람의 비공개 자료는 포함하지 마세요.</p><p class="form-error" data-error role="alert"></p><div class="p-form-actions"><a class="btn btn-outline" href="#/community">취소</a><button class="btn btn-primary" type="submit">${post ? "수정 저장" : "게시글 등록"} ${icon("arrow", 17)}</button></div></form></section>`,
    bind() {
      bindForm("#post-form", async (data) => {
        if (post) data.version = post.version;
        const result = await ctx.api(
          `/api/community/posts${post ? `/${id}` : ""}`,
          { method: post ? "PATCH" : "POST", body: data },
        );
        ctx.toast("게시글을 저장했습니다.");
        ctx.navigate(`/community/post/${result.post.id}`);
      });
    },
  };
}

export function communityCommentHTML(comment, post, ctx) {
  if (comment.tombstone)
    return `<article class="cm-comment cm-unavailable" id="comment-${esc(comment.id)}" tabindex="-1" data-comment-id="${esc(comment.id)}"><p class="p-muted">현재 표시할 수 없는 원댓글입니다.</p></article>`;
  const own = ctx.user?.id === comment.userId;
  const root = comment.parentId
    ? post.comments?.find((c) => c.id === comment.parentId && !c.tombstone)
    : comment;
  return `<article class="cm-comment" id="comment-${esc(comment.id)}" tabindex="-1" data-comment-id="${esc(comment.id)}">${comment.parentId ? '<p class="cm-reply-label">원댓글에 남긴 답글</p>' : ""}<div class="cm-comment-head"><strong>${esc(comment.author)}</strong><span>${date(comment.createdAt)}${comment.editedAt ? ` · ${date(comment.editedAt)} 수정됨` : ""}</span></div><div class="p-user-body">${textBody(comment.body)}</div><div class="cm-comment-actions">${ctx.user && root?.canReply ? `<a class="btn-text" href="${replyPath(post.id, root.id)}">${comment.parentId ? "대화에 답글" : "답글"}</a>` : ""}${own && comment.canEdit ? `<a class="btn-text" href="${commentPath(post.id, comment.id, "edit")}">댓글 수정</a>` : ""}${own && comment.canViewHistory !== false ? `<a class="btn-text" href="${commentPath(post.id, comment.id, "history")}">수정 이력</a>` : ""}${own || post.canModerate ? `<button class="btn-text" data-delete-comment="${esc(comment.id)}">${own ? "댓글 삭제" : "댓글 운영 삭제"}</button>` : ""}${ctx.user && !own ? `<a class="btn-text" href="#/community/blocks/${esc(comment.userId)}?back=${esc(post.id)}">대화 차단</a>` : ""}</div></article>`;
}

const canComment = (post, ctx) =>
  Boolean(
    ctx.user &&
      (post.canComment ?? (!post.locked && post.status === "published")),
  );
function conversationHTML(post, ctx, intent) {
  return `<section class="p-panel p-comments cm-thread" data-community-thread><div class="cm-section-head"><h2>댓글 <span>${post.comments.filter((c) => !c.tombstone).length}</span></h2>${ctx.user ? '<a class="text-link" href="#/community/blocks">내 대화 차단 관리 ↗</a>' : ""}</div><p class="cm-thread-note">각 원댓글 아래에서 대화를 이어갈 수 있습니다.</p><div class="cm-conversations" data-community-feed>${
    conversationGroups(post.comments)
      .map(
        ({ root, replies }) =>
          `<section class="cm-conversation" aria-label="댓글 대화">${communityCommentHTML(root, post, ctx)}${replies.length ? `<ol class="cm-replies">${replies.map((r) => `<li>${communityCommentHTML(r, post, ctx)}</li>`).join("")}</ol>` : ""}</section>`,
      )
      .join("") || '<p class="p-muted">첫 번째 댓글을 남겨보세요.</p>'
  }</div>${intent ? commentFormHTML(intent, { available: canComment(post, ctx) }) : '<button class="btn btn-outline" id="comment-login">로그인하고 댓글 쓰기</button>'}</section>`;
}

async function postDetail(id, ctx, params = new URLSearchParams()) {
  const post = (await ctx.api(`/api/community/posts/${id}`)).post;
  let viewRoot, route;
  const request = async (...args) => {
    try {
      return await ctx.api(...args);
    } catch (error) {
      if (
        location.hash === route &&
        viewRoot?.isConnected &&
        ([401, 403, 404].includes(error.status) || error.sessionChanged)
      )
        redactCommunityView(viewRoot, error);
      throw error;
    }
  };
  const intent = ctx.user
    ? new CommentIntent({ ownerId: ctx.user.id, postId: id })
    : null;
  async function action(name, extra = {}) {
    const route = location.hash;
    try {
      const result = await request(`/api/community/posts/${id}/actions`, {
        method: "POST",
        body: { action: name, version: post.version, ...extra },
      });
      if (
        location.hash !== route ||
        !viewRoot?.isConnected ||
        viewRoot.dataset.unavailable === "true"
      )
        return false;
      if (name === "like") {
        post.liked = result.post.liked;
        post.likeCount = result.post.likeCount;
        const button = document.querySelector("#post-like");
        button.classList.toggle("liked", post.liked);
        button.innerHTML = `${icon("check", 15)} 좋아요 ${post.likeCount}`;
      } else ctx.reload();
      return true;
    } catch (e) {
      ctx.toast(e.message, "error");
      return false;
    }
  }
  return {
    html: `<section class="container p-page p-thread" data-community-post><a class="back-link" href="#/community?board=${esc(post.boardId)}">← ${esc(post.boardTitle)}</a><article class="p-panel"><div class="p-thread-header">${post.status !== "published" ? tag(statusLabel(post.status), "amber") : ""}${post.pinned ? tag("공지", "mint") : ""}${post.locked ? tag("댓글 잠금") : ""}<h1>${esc(post.title)}</h1><p>${esc(post.author)} · ${date(post.createdAt)}${post.updatedAt !== post.createdAt ? " · 수정됨" : ""}</p></div><div class="p-user-body">${textBody(post.body)}</div><div class="p-thread-actions"><button class="btn btn-outline btn-sm ${post.liked ? "liked" : ""}" id="post-like">${icon("check", 15)} 좋아요 ${post.likeCount}</button><div>${ctx.user?.id === post.userId && post.status === "published" ? `<a href="#/community/post/${id}/edit" class="btn-text">수정</a><button class="btn-text" id="post-delete">삭제</button>` : ""}<button class="btn-text" id="post-report">신고하기</button></div></div></article>${ctx.user?.id === post.userId ? '<p class="p-notice"><a class="text-link" href="#/community/moderation/actions">내 글·댓글의 운영 사유와 이의 상태 확인 ↗</a></p>' : ""}${conversationHTML(post, ctx, intent)}${post.canModerate ? `<section class="p-panel"><h2>운영 처리</h2><form id="post-moderate" class="p-moderation-form"><label class="field">처리<select name="action"><option value="hide">게시글 숨기기</option><option value="restore">게시글 복원</option><option value="lock">댓글 잠금</option><option value="unlock">댓글 잠금 해제</option><option value="pin">상단 공지로 고정</option><option value="unpin">고정 해제</option></select></label><label class="field">처리 사유<input name="reason" required minlength="5" maxlength="2000"/></label><p class="form-error" data-error role="alert"></p><button class="btn btn-outline btn-sm" type="submit">처리하고 이력 남기기</button></form></section>` : ""}</section>`,
    bind() {
      route = location.hash;
      viewRoot = document.querySelector("[data-community-post]");
      let commentEditor;
      if (intent) {
        commentEditor = bindCommentForm(
          document.querySelector("[data-community-editor]"),
          intent,
          {
            ctx,
            current: { post },
            load: async () => ({
              post: (
                await ctx.api(`/api/community/posts/${id}`, {
                  ownerId: ctx.user.id,
                })
              ).post,
            }),
            permitted: (data) => canComment(data.post, ctx),
            onSaved(receipt, result) {
              ctx.toast(
                result.currentAvailable === false
                  ? "저장 영수증을 확인했습니다. 현재 댓글은 표시할 수 없습니다."
                  : "댓글을 등록했습니다.",
              );
              ctx.navigate(postPath(id, receipt.commentId).slice(1));
            },
          },
        );
        document
          .querySelector("[data-community-thread]")
          .addEventListener("click", (event) => {
            const link = event.target.closest("a");
            if (link && commentEditor.dirty()) {
              event.preventDefault();
              ctx.toast(
                "작성 중인 댓글을 먼저 등록하거나 작성 취소 후 이동해주세요.",
                "error",
              );
            }
          });
      }
      const requested = params.get("comment");
      if (requested && /^[A-Za-z0-9_-]+$/.test(requested)) {
        const target = document.getElementById("comment-" + requested);
        if (target) {
          target.setAttribute("data-route-focus", "");
          target.focus();
          target.scrollIntoView({ block: "center" });
        } else ctx.toast("현재 표시할 수 없는 댓글입니다.", "error");
      }
      document.querySelectorAll("[data-delete-comment]").forEach((button) => {
        button.onclick = () =>
          ctx.modal(
            '<h2>댓글을 삭제할까요?</h2><form id="comment-delete"><label class="field">처리 사유<input name="reason" minlength="5" maxlength="2000" required value="작성 댓글 삭제"/></label><p class="form-error" data-error role="alert"></p><button class="btn btn-danger" type="submit">댓글 삭제</button></form>',
            (dialog) =>
              bindForm(dialog.querySelector("form"), async (data) => {
                await request(
                  `/api/community/comments/${button.dataset.deleteComment}`,
                  {
                    method: "DELETE",
                    body: {
                      ...data,
                      ...(post.moderation
                        ? { policyId: post.moderation.policyId }
                        : {}),
                    },
                  },
                );
                dialog.close();
                ctx.toast("댓글을 삭제했습니다.");
                ctx.reload();
              }),
          );
      });
      document.querySelector("#post-like").onclick = () => {
        const perform = () => action("like", { active: !post.liked });
        if (ctx.requireAuth(perform)) perform();
      };
      document
        .querySelector("#comment-login")
        ?.addEventListener("click", () => ctx.requireAuth());
      document.querySelector("#post-report").onclick = () => {
        const open = () =>
          ctx.modal(
            `<p class="eyebrow">REPORT</p><h2>운영자에게 알려주세요.</h2><form id="report-form"><label class="field">신고 사유<textarea name="reason" required minlength="5" maxlength="2000" rows="4"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">신고 접수</button></form>`,
            (dialog) =>
              bindForm(dialog.querySelector("form"), async (data) => {
                await request(`/api/community/posts/${id}/reports`, {
                  method: "POST",
                  body: { ...data, version: post.version },
                });
                dialog.close();
                ctx.toast("신고를 접수했습니다.");
              }),
          );
        if (ctx.requireAuth(open)) open();
      };
      document.querySelector("#post-delete")?.addEventListener("click", () =>
        ctx.modal(
          '<h2>게시글을 삭제할까요?</h2><p class="dialog-description">목록에서 제외됩니다. 운영 이력은 보존됩니다.</p><button class="btn btn-danger" id="confirm-post-delete">삭제하기</button>',
          (dialog) => {
            dialog.querySelector("button#confirm-post-delete").onclick =
              async () => {
                if (await action("delete")) {
                  dialog.close();
                  ctx.navigate("/community");
                }
              };
          },
        ),
      );
      bindForm("#post-moderate", (data) =>
        action(data.action, {
          reason: data.reason,
          ...(post.moderation ? { policyId: post.moderation.policyId } : {}),
        }),
      );
    },
  };
}

async function availableCourses(ctx) {
  const me = await ctx.api("/api/me");
  let ids = new Set(
    me.enrollments.filter(hasCourseAccess).map((e) => e.courseId),
  );
  if (ctx.user.role === "admin") return ctx.catalog.courses;
  if (ctx.user.roles?.includes("coach")) {
    const studio = await ctx.api("/api/studio/courses");
    studio.courses.forEach((c) => ids.add(c.id));
    return [...ctx.catalog.courses, ...studio.courses].filter(
      (c, i, all) => ids.has(c.id) && all.findIndex((a) => a.id === c.id) === i,
    );
  }
  return ctx.catalog.courses.filter((c) => ids.has(c.id));
}

async function qaList(params, ctx) {
  if (!ctx.user) return authGate(ctx, "배움의 질문을 이어가세요.");
  const desk = params.get("desk") === "1";
  const data = await ctx.api(
      `/api/${desk ? "studio/questions" : "qa/questions"}?${params}`,
    ),
    courses = await availableCourses(ctx);
  return {
    html: `<section class="container p-page">${head("COURSE Q&A", desk ? "코치 질문 데스크." : "막힌 개념을, 함께 풀어보세요.", "강의와 차시에 연결된 질문, 공식 답변과 후속 질문을 한곳에서 확인합니다.", `<a class="btn btn-primary" href="#/qa/new${params.get("courseId") ? `?courseId=${esc(params.get("courseId"))}` : ""}">질문하기 ${icon("external", 17)}</a>`)}${desk ? opsNav(ctx, "qa") : ""}<form id="qa-filter" class="p-filter-bar"><label>강의<select name="courseId"><option value="">${desk ? "담당 강의 전체" : "내가 작성한 질문"}</option>${courses.map((c) => `<option value="${esc(c.id)}" ${params.get("courseId") === c.id ? "selected" : ""}>${esc(c.title)}</option>`).join("")}</select></label><label>진행 상태<select name="status"><option value="">전체</option>${["open", "answered", "closed"].map((s) => `<option value="${s}" ${params.get("status") === s ? "selected" : ""}>${statusLabel(s)}</option>`).join("")}</select></label><button class="btn btn-outline btn-sm" type="submit">조회</button></form>${data.questions.length ? `<div class="p-post-list">${data.questions.map((q) => `<a href="#/qa/${esc(q.id)}"><div><div class="p-tag-row">${tag(statusLabel(q.status), q.status === "answered" ? "mint" : "")}${q.visibility === "private" ? tag("비공개") : ""}${q.acceptedAnswerId ? tag("해결됨", "mint") : ""}</div><h3>${esc(q.title)}</h3><p>${esc(q.courseTitle)}</p><span>${esc(q.author)} · ${date(q.createdAt)}</span></div><div class="p-post-counts"><span>답변 <b>${q.answerCount}</b></span></div></a>`).join("")}</div>` : empty("아직 질문이 없습니다.", "수강 중인 강의의 차시 화면에서도 질문을 작성할 수 있습니다.", `<a class="btn btn-outline" href="#/library">강의 둘러보기</a>`)}${pager("/qa", params, data.page, data.total)}</section>`,
    bind() {
      bindForm("#qa-filter", (data) => {
        const q = new URLSearchParams(data);
        if (desk) q.set("desk", "1");
        ctx.navigate(`/qa?${q}`);
      });
    },
  };
}

async function questionEditor(params, ctx) {
  if (!ctx.user) return authGate(ctx, "질문을 남기고 코치의 관점을 만나세요.");
  const courses = await availableCourses(ctx);
  if (!courses.length)
    return {
      html: `<section class="container p-page">${head("COURSE Q&A", "강의에서 함께 시작해요.", "질문은 수강권이 있는 강의에서 작성할 수 있습니다.")}${empty("먼저 무료 강의에 수강 등록해주세요.", "등록 후 차시별 질문과 학습 기록을 이용할 수 있습니다.", `<a href="#/library" class="btn btn-primary">무료 강의 찾기</a>`)}</section>`,
    };
  const selected =
    courses.find((c) => c.id === params.get("courseId")) || courses[0];
  return {
    html: `<section class="container p-page p-compose"><a class="back-link" href="#/qa">← 강의 Q/A</a>${head("ASK YOUR COACH", "좋은 질문은, 좋은 학습의 시작.", "당시의 상황과 이해한 부분, 막힌 지점을 구체적으로 남겨주세요.")}<form id="question-form" class="p-panel" data-recovery-private data-session-draft data-recovery-version="new" data-recovery-context="courseId"><label class="field">강의<select name="courseId" id="question-course">${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === selected.id ? "selected" : ""}>${esc(c.title)}</option>`).join("")}</select></label><label class="field">차시 (선택)<select name="lessonId" id="question-lesson"></select></label><label class="field">공개 범위<select name="visibility"><option value="course">이 강의 수강생과 코치에게 공개</option><option value="private">나와 담당 코치·관리자만 확인</option></select></label><label class="field">질문 제목<input name="title" required minlength="3" maxlength="150"/></label><label class="field">질문 내용<textarea name="body" rows="12" required minlength="10" maxlength="10000" placeholder="어느 차시의 어떤 개념인가요? 지금까지 이해한 내용과 구체적으로 비교하고 싶은 선택을 적어주세요."></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">질문 등록 ${icon("arrow", 17)}</button></form></section>`,
    bind() {
      const courseSelect = document.querySelector("#question-course"),
        lessonSelect = document.querySelector("#question-lesson");
      function update() {
        const course = courses.find((c) => c.id === courseSelect.value);
        lessonSelect.innerHTML = `<option value="">강의 전체 질문</option>${course.lessons
          .filter((l) => !l.status || l.status === "published")
          .map(
            (l) =>
              `<option value="${esc(l.id)}" ${params.get("lessonId") === l.id ? "selected" : ""}>${esc(l.title)}</option>`,
          )
          .join("")}`;
      }
      update();
      courseSelect.onchange = update;
      courseSelect.form.addEventListener("tpc:prepare-draft", (event) => {
        const saved = event.detail.entries.find(
          (entry) => entry.key === "courseId",
        );
        if (saved) courseSelect.value = saved.value;
        update();
      });
      courseSelect.form.addEventListener("tpc:reset-draft", update);
      bindForm("#question-form", async (data) => {
        const result = await ctx.api("/api/qa/questions", {
          method: "POST",
          body: data,
        });
        ctx.toast("질문을 등록했습니다.");
        ctx.navigate(`/qa/${result.question.id}`);
      });
    },
  };
}

async function questionDetail(id, ctx) {
  if (!ctx.user) return authGate(ctx);
  const q = (await ctx.api(`/api/qa/questions/${id}`)).question,
    owner = q.userId === ctx.user.id;
  const lesson = ctx.catalog.courses
    .find((course) => course.id === q.courseId)
    ?.lessons.find((item) => item.id === q.lessonId);
  return {
    html: `<section class="container p-page p-thread" data-qa-detail><a class="back-link" href="#/qa?courseId=${esc(q.courseId)}">← 강의 Q/A</a><article class="p-panel"><div class="p-thread-header"><div class="p-tag-row">${tag(statusLabel(q.status))}${q.visibility === "private" ? tag("비공개") : ""}${q.acceptedAnswerId ? tag("해결됨", "mint") : ""}</div><h1>${esc(q.title)}</h1><p>${esc(q.author)} · ${date(q.createdAt)}</p><a class="text-link" href="#/course/${esc(q.courseId)}">${esc(q.courseTitle)} ${icon("external", 14)}</a>${q.lessonId ? `<p><a class="text-link" href="#/learn/${esc(q.courseId)}/${esc(q.lessonId)}">${esc(lesson?.title || "질문한 차시로 돌아가기")} ${icon("arrow", 14)}</a></p>` : ""}</div><div class="p-user-body">${textBody(q.body)}</div>${contentActions(q, q, "question")}${owner || q.canAnswerOfficially ? `<div class="p-thread-actions"><span class="p-muted">${q.visibility === "private" ? "나와 담당 코치·관리자만 볼 수 있는 질문입니다." : "이 강의의 수강생에게 공개됩니다."}</span><button class="btn btn-outline btn-sm" id="question-state">${q.status === "closed" ? "질문 다시 열기" : "질문 닫기"}</button></div>` : ""}</article><section class="p-panel p-comments"><h2>답변 <span>${q.answers.length}</span></h2>${q.answers.map((answer) => `<article class="p-answer ${answer.official ? "official" : ""} ${q.acceptedAnswerId === answer.id ? "accepted" : ""}"><div><strong>${esc(answer.author)}</strong>${answer.official ? tag("공식 답변", "mint") : tag("수강생")}${q.acceptedAnswerId === answer.id ? tag("해결 답변", "mint") : ""}<span>${date(answer.createdAt)}</span></div><div class="p-user-body">${textBody(answer.body)}</div>${contentActions(q, answer, "answer")}${owner && answer.userId !== ctx.user.id && q.acceptedAnswerId !== answer.id ? `<button class="btn btn-outline btn-sm" data-resolve-answer="${esc(answer.id)}">이 답변으로 해결했어요 ${icon("check", 14)}</button>` : ""}</article>`).join("") || '<p class="p-muted">아직 답변이 없습니다. 답변이 등록되면 알림에서 확인할 수 있습니다.</p>'}${q.status !== "closed" ? `<form id="answer-form" data-recovery-private data-session-draft data-recovery-version="${esc(q.version)}"><label class="field">${q.canAnswerOfficially ? "담당 코치·관리자의 공식 답변" : "답변 또는 추가 질문"}<textarea name="body" rows="6" required minlength="2" maxlength="10000" placeholder="${q.canAnswerOfficially ? "질문의 전제를 확인하고 이유와 다음 과제를 설명해주세요." : "배경을 추가하거나 함께 살펴볼 관점을 남겨주세요."}"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">${q.canAnswerOfficially ? "공식 답변 등록" : "답변 등록"} ${icon("arrow", 17)}</button></form>` : '<p class="p-lock-note">질문이 닫혀 있습니다. 작성자 또는 담당자가 다시 열 수 있습니다.</p>'}</section></section>`,
    bind() {
      const root = document.querySelector("[data-qa-detail]");
      bindFileLinks(
        root,
        [
          ...(q.contentAttachments || []),
          ...q.answers.flatMap((a) => a.contentAttachments || []),
        ],
        ctx,
        (error) => {
          if (error.status === 401) root.hidden = true;
          else if ([403, 404].includes(error.status) || error.sessionChanged)
            root.innerHTML = empty(
              "현재 자료를 표시할 수 없습니다.",
              "질문이나 자료 상태가 변경되었습니다. 최신 화면에서 확인해주세요.",
              `<button type="button" class="btn btn-outline" id="qa-detail-reload">최신 질문 다시 확인</button>`,
            );
          else ctx.toast(error.message, "error");
          root
            .querySelector("#qa-detail-reload")
            ?.addEventListener("click", () => ctx.reload());
        },
      );
      bindForm("#answer-form", async (data) => {
        await ctx.api(`/api/qa/questions/${id}/answers`, {
          method: "POST",
          body: data,
        });
        ctx.toast("답변을 등록했습니다.");
        ctx.reload();
      });
      async function update(action, answerId) {
        try {
          await ctx.api(`/api/qa/questions/${id}`, {
            method: "PATCH",
            body: { action, answerId, version: q.version },
          });
          ctx.reload();
        } catch (e) {
          ctx.toast(e.message, "error");
        }
      }
      document
        .querySelector("#question-state")
        ?.addEventListener("click", () =>
          update(q.status === "closed" ? "reopen" : "close"),
        );
      document
        .querySelectorAll("[data-resolve-answer]")
        .forEach(
          (b) => (b.onclick = () => update("resolve", b.dataset.resolveAnswer)),
        );
    },
  };
}
