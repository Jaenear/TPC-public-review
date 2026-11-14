import { esc } from "./ui.js";
import {
  head,
  empty,
  authGate,
  bindForm,
  textBody,
  date,
  tag,
  opsNav,
} from "./platform-ui.js";
import { fromSeoulInput } from "./access.js";
import {
  profileStates,
  readProfileFields,
  hasProfileChanges,
} from "./profile-model.js";

const route = (id) => `/studio/profiles/${encodeURIComponent(id)}`;
const label = (p) => p.publishedPayload?.name || "공개 소개 작성 전";
const note = (message) => `<p class="form-note">${esc(message)}</p>`;
const stateTag = (p) =>
  tag(
    p.publiclyAvailable
      ? "공개 중"
      : p.state === "inactive"
        ? "운영 중지"
        : "공개 준비",
    p.publiclyAvailable ? "mint" : "amber",
  );
const eventLabels = {
  "draft.created": "변경안 생성",
  created: "프로필 생성",
  managed: "운영 상태 변경",
  saved: "변경안 저장",
  submit: "검수 요청",
  withdraw: "검수 요청 회수",
  approve: "공개 발행",
  reject: "수정 요청",
  cancel: "작성 중단",
  rebase: "기준 재연결",
  "verification.added": "확인 사실 등록",
  "verification.revoked": "확인 사실 회수",
};
const factState = {
  active: "현재판 적용",
  expired: "기간 만료",
  superseded: "이전 공개판",
  revoked: "회수",
};

export async function renderCoachStudio(path, params, ctx) {
  if (
    path !== "/studio/profiles" &&
    !path.startsWith("/studio/profiles/") &&
    !(path === "/manage" && params.get("tab") === "profiles")
  )
    return null;
  if (!ctx.user) return authGate(ctx, "코치 프로필 관리에 로그인해주세요.");
  if (ctx.user.role !== "admin" && !ctx.user.roles?.includes("coach"))
    return {
      html: `<section class="container p-page">${empty("코치 또는 관리자 권한이 필요합니다.", "담당 계정에 연결된 소개와 변경안을 관리할 수 있습니다.")}</section>`,
    };
  if (path.startsWith("/studio/profiles/"))
    return editor(decodeURIComponent(path.slice(17)), params, ctx);
  return list(ctx);
}
function ownerFields(profile, users) {
  const current = profile?.ownerUserId;
  return `<label class="field">담당 코치 검색<input name="ownerQuery" placeholder="이름 또는 이메일"/></label><button type="button" class="btn btn-outline btn-sm" data-find-owner>담당 코치 찾기</button><label class="field">담당 계정<select name="ownerUserId"><option value="">담당자 미지정</option>${current && !users.some((u) => u.id === current) ? `<option value="${esc(current)}" selected>${esc(profile.ownerName || current)} · 현재 담당</option>` : ""}${users.map((u) => `<option value="${esc(u.id)}" ${u.id === current ? "selected" : ""}>${esc(u.name)} · ${esc(u.email)}</option>`).join("")}</select></label><p class="form-note" data-owner-result>활성 코치 계정만 선택할 수 있습니다. 검색 결과는 최대20명씩 표시합니다.</p>`;
}
function bindOwnerSearch(root, ctx, profile) {
  root
    .querySelector("[data-find-owner]")
    ?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const output = root.querySelector("[data-owner-result]");
      try {
        const q = root.querySelector('[name="ownerQuery"]').value;
        const { users, total } = await ctx.api(
          `/api/manage/profile-owners?${new URLSearchParams({ q, coachId: profile?.id || "" })}`,
        );
        const select = root.querySelector('[name="ownerUserId"]'),
          selected = select.selectedOptions[0];
        const retained =
          selected?.value && !users.some((u) => u.id === selected.value)
            ? `<option value="${esc(selected.value)}" selected>${esc(selected.textContent)}</option>`
            : "";
        select.innerHTML = `<option value="">담당자 미지정</option>${retained}${users.map((u) => `<option value="${esc(u.id)}" ${u.id === selected?.value ? "selected" : ""}>${esc(u.name)} · ${esc(u.email)}</option>`).join("")}`;
        output.textContent = `검색 결과 ${total}명 · ${users.length}명 표시`;
      } catch (error) {
        output.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
}
async function list(ctx) {
  const { profiles } = await ctx.api("/api/studio/profiles");
  const admin = ctx.user.role === "admin";
  return {
    html: `<section class="container p-page">${head("COACH PROFILE STUDIO", admin ? "소개에서 공개 검수까지." : "나의 교육 방식을 소개하세요.", "공개 중인 소개는 유지하면서 변경안을 작성하고 검수합니다.", admin ? `<button id="new-profile" class="btn btn-primary">새 코치 프로필</button>` : "")}${opsNav(ctx, "profiles")}<div class="p-profile-admin-list">${profiles.map((p) => `<article class="p-panel"><div class="p-inline-heading"><h2>${esc(label(p))}</h2>${stateTag(p)}</div><p class="p-muted">${esc(p.ownerName || "담당자 미지정")} · 공개 ${p.publicVersion}판${p.publishedPayload?.pilot ? " · 파일럿 예시" : ""}</p><p>${p.openRevision ? `${tag(profileStates[p.openRevision.status], "amber")} 변경안 있음` : "새 변경안을 작성할 수 있습니다."}</p><p class="p-muted">진행 중 코칭 접수 ${p.inquiryCount}건${p.inquiryCount && !p.publiclyAvailable ? " · 인계 상태 확인 필요" : ""}</p><a class="btn btn-outline btn-sm" href="#${route(p.id)}">소개·검수·접수 관리 →</a></article>`).join("") || empty("연결된 프로필이 없습니다.", "관리자가 담당 코치 계정과 프로필을 연결하면 편집을 시작할 수 있습니다.")}</div></section>`,
    bind() {
      document
        .querySelector("#new-profile")
        ?.addEventListener("click", async () => {
          try {
            const { users } = await ctx.api("/api/manage/profile-owners");
            ctx.modal(
              `<p class="eyebrow">NEW COACH PROFILE</p><h2>새 프로필 준비</h2><form id="create-profile">${ownerFields(null, users)}<label class="field">생성 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">프로필 준비 시작</button></form>`,
              (dialog) => {
                const form = dialog.querySelector("form");
                bindOwnerSearch(form, ctx, null);
                bindForm(form, async (data) => {
                  const result = await ctx.api("/api/manage/profiles", {
                    method: "POST",
                    body: {
                      ownerUserId: data.ownerUserId,
                      reason: data.reason,
                    },
                  });
                  dialog.close();
                  ctx.navigate(route(result.profile.id));
                });
              },
            );
          } catch (error) {
            ctx.toast(error.message, "error");
          }
        });
    },
  };
}
function payloadSummary(payload) {
  if (!payload) return `<p class="p-muted">공개된 소개가 없습니다.</p>`;
  return `<h3>${esc(payload.name)}</h3><p class="p-muted">${esc(payload.role)} · ${payload.pilot ? "파일럿 예시" : "실재 코치 소개"}</p><div class="tag-list">${payload.specialty.map((x) => `<span>${esc(x)}</span>`).join("")}</div>${textBody(payload.bio)}<h4>교육 방식</h4>${textBody(payload.teachingApproach)}${payload.claims.length ? `<h4>코치 소개 경험</h4><ul>${payload.claims.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}<p class="p-muted">대상 ${payload.levels.map(esc).join(" · ")} · 샘플 ${payload.samples.length}개 · ${payload.acceptsInquiry ? "신규 상담 접수" : "접수 중지"}</p>`;
}
function lessonOptions(courses, courseId, lessonId) {
  const course = courses.find((c) => c.id === courseId),
    lessons = course?.lessons.filter((l) => l.free) || [];
  return `<option value="">무료 차시 선택</option>${lessonId && !lessons.some((l) => l.id === lessonId) ? `<option value="${esc(lessonId)}" selected>공개 범위 확인 필요 · ${esc(lessonId)}</option>` : ""}${lessons.map((l) => `<option value="${esc(l.id)}" ${l.id === lessonId ? "selected" : ""}>${esc(l.title)}</option>`).join("")}`;
}
function sampleFields(payload, courses, editable) {
  return `<details class="p-profile-sample-editor"><summary>교육 샘플 · 최대3개</summary><p class="form-note">무료 공개 차시나 YouTube 링크를 연결하세요. 샘플 공개 범위와 근거는 발행 검수에서 확인합니다.</p>${Array.from(
    { length: 3 },
    (_, i) => {
      const s = payload.samples[i] || {},
        disabled = editable ? "" : "disabled";
      return `<fieldset data-sample-slot="${i}"><legend>샘플 ${i + 1}</legend><label class="field">샘플 ${i + 1} 유형<select name="sampleKind${i}" ${disabled}><option value="">등록 안 함</option><option value="lesson" ${s.kind === "lesson" ? "selected" : ""}>무료 공개 차시</option><option value="video" ${s.kind === "video" ? "selected" : ""}>YouTube 외부 영상</option></select></label><div data-sample-body ${s.kind ? "" : "hidden"}><label class="field">샘플 ${i + 1} 제목<input name="sampleTitle${i}" maxlength="100" value="${esc(s.title || "")}" ${disabled}/></label><div data-sample-lesson ${s.kind === "lesson" ? "" : "hidden"}><label class="field">샘플 ${i + 1} 강의<select name="sampleCourse${i}" ${disabled}><option value="">강의 선택</option>${s.courseId && !courses.some((c) => c.id === s.courseId) ? `<option value="${esc(s.courseId)}" selected>공개 범위 확인 필요 · ${esc(s.courseId)}</option>` : ""}${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === s.courseId ? "selected" : ""}>${esc(c.title)}</option>`).join("")}</select></label><label class="field">샘플 ${i + 1} 차시<select name="sampleLesson${i}" ${disabled}>${lessonOptions(courses, s.courseId, s.lessonId)}</select></label></div><label class="field" data-sample-video ${s.kind === "video" ? "" : "hidden"}>샘플 ${i + 1} YouTube 주소<input name="sampleUrl${i}" type="url" value="${esc(s.url || "")}" ${disabled}/></label></div></fieldset>`;
    },
  ).join("")}</details>`;
}
function fields(payload, courses, editable) {
  const ro = editable ? "" : "readonly",
    disabled = editable ? "" : "disabled";
  return `<label class="field">공개 이름<input name="name" required minlength="2" maxlength="60" value="${esc(payload.name)}" ${ro}/></label><label class="field">전문 분야 한 줄 소개<input name="role" required minlength="3" maxlength="100" value="${esc(payload.role)}" ${ro}/></label><div class="p-form-grid"><label class="field">이니셜<input name="initials" required pattern="[A-Za-z0-9]{1,4}" maxlength="4" value="${esc(payload.initials)}" ${ro}/></label><label class="field">프로필 색상<select name="accent" ${disabled}>${["green", "gold", "blue"].map((x) => `<option value="${x}" ${payload.accent === x ? "selected" : ""}>${{ green: "민트", gold: "골드", blue: "블루" }[x]}</option>`).join("")}</select></label></div><label class="field">전문 분야 · 쉼표로 구분<input name="specialty" required value="${esc(payload.specialty.join(", "))}" ${ro}/></label><fieldset class="p-profile-levels"><legend>대상 난이도</legend>${["입문", "중급", "심화"].map((x) => `<label><input type="checkbox" name="levels" value="${x}" ${payload.levels.includes(x) ? "checked" : ""} ${disabled}/> ${x}</label>`).join("")}</fieldset><label class="field">자기소개<textarea name="bio" required minlength="20" maxlength="4000" rows="7" ${ro}>${esc(payload.bio)}</textarea></label><label class="field">교육 방식<textarea name="teachingApproach" required minlength="20" maxlength="3000" rows="5" ${ro}>${esc(payload.teachingApproach)}</textarea></label><label class="field">소개할 경험 · 한 줄에 하나씩<textarea name="claims" maxlength="1504" rows="4" ${ro}>${esc(payload.claims.join("\n"))}</textarea></label>${note("작성한 경험은 코치의 소개로 표시합니다. 운영자가 근거로 확인한 사실은 별도로 관리합니다.")}<div class="p-profile-switches"><label><input name="pilot" type="checkbox" ${payload.pilot ? "checked" : ""} ${disabled}/> 파일럿 예시로 표시</label><label><input name="acceptsInquiry" type="checkbox" ${payload.acceptsInquiry ? "checked" : ""} ${disabled}/> 신규 코칭 상담 접수</label></div>${sampleFields(payload, courses, editable)}`;
}
function readEditor(form) {
  const values = Object.fromEntries(new FormData(form));
  values.pilot = form.elements.pilot.checked;
  values.acceptsInquiry = form.elements.acceptsInquiry.checked;
  return readProfileFields(values, new FormData(form).getAll("levels"));
}
function pages(path, params, key, data, description) {
  const total = Math.ceil(data.total / data.pageSize);
  if (total <= 1) return "";
  const link = (page) => {
    const q = new URLSearchParams(params);
    q.set(key, page);
    return `#${path}?${q}`;
  };
  return `<nav class="p-pagination" aria-label="${esc(description)}">${data.page > 1 ? `<a class="btn btn-outline btn-sm" href="${esc(link(data.page - 1))}">← 이전</a>` : ""}<span>${data.page}/${total}</span>${data.page < total ? `<a class="btn btn-outline btn-sm" href="${esc(link(data.page + 1))}">다음 →</a>` : ""}</nav>`;
}
async function editor(id, params, ctx) {
  const admin = ctx.user.role === "admin";
  const [data, history, inquiries, catalog, owners] = await Promise.all([
    ctx.api(`/api/studio/profiles/${id}`),
    ctx.api(
      `/api/studio/profiles/${id}/revisions?page=${params.get("page") || 1}`,
    ),
    ctx.api(
      `/api/studio/profiles/${id}/bookings?page=${params.get("bookingPage") || 1}`,
    ),
    ctx.api("/api/catalog"),
    admin
      ? ctx.api(`/api/manage/profile-owners?coachId=${encodeURIComponent(id)}`)
      : Promise.resolve({ users: [] }),
  ]);
  const p = data.profile;
  let revision =
    data.revisions.find((r) => r.id === params.get("revision")) ||
    data.revisions.find((r) =>
      ["draft", "submitted", "rejected"].includes(r.status),
    ) ||
    data.revisions[0];
  if (
    params.get("revision") &&
    !data.revisions.some((r) => r.id === params.get("revision"))
  )
    revision = (
      await ctx.api(
        `/api/studio/profiles/${id}/revisions/${encodeURIComponent(params.get("revision"))}`,
      )
    ).revision;
  const editable = revision && ["draft", "rejected"].includes(revision.status),
    open = data.revisions.find((r) =>
      ["draft", "submitted", "rejected"].includes(r.status),
    );
  const courses = catalog.courses.filter((c) => c.status === "published");
  return {
    html: `<section class="container p-page"><a class="back-link" href="#/studio/profiles">← 코치 프로필</a>${head("PROFILE REVIEW", label(p), "공개본·변경안·확인 근거와 코칭 접수 흐름을 함께 관리합니다.", p.publiclyAvailable ? `<a class="btn btn-outline" href="#/coach/${esc(id)}" target="_blank" rel="noopener">공개 프로필 ↗</a>` : "")}${opsNav(ctx, "profiles")}<div class="p-profile-stage">${stateTag(p)}<span>공개 ${p.publicVersion}판 · 운영 v${p.version}</span><span>${esc(p.ownerName || "담당자 미지정")}</span>${open ? tag(profileStates[open.status], "amber") : ""}</div><div class="p-profile-review-layout"><div><article class="p-panel"><div class="p-inline-heading"><h2>${revision ? profileStates[revision.status] : "새 소개 준비"}</h2>${!open ? `<button class="btn btn-primary btn-sm" id="start-profile-draft">변경안 만들기</button>` : open.id !== revision?.id ? `<a class="btn btn-outline btn-sm" href="#${route(id)}?revision=${esc(open.id)}">진행 중 변경안</a>` : ""}</div>${revision ? `<p class="p-muted" id="profile-basis">기준 공개 ${revision.basePublicVersion}판 · 운영 v${revision.baseProfileVersion} · 변경안 v${revision.version}</p>${revision.reviewNote ? `<div class="p-notice amber"><strong>최근 검토 의견</strong><p>${esc(revision.reviewNote)}</p></div>` : ""}<form id="coach-profile-editor" data-session-draft data-recovery-version="${revision.version}:${p.version}:${p.publicVersion}">${fields(revision.payload, courses, editable)}<label class="field">변경 설명<textarea name="reason" maxlength="1000" rows="3" ${editable ? "" : "readonly"}>${esc(revision.reason)}</textarea></label><p class="form-error" data-error role="alert"></p>${editable ? `<div class="p-form-actions"><button class="btn btn-primary" type="submit">변경안 저장</button><button class="btn btn-outline" type="button" data-profile-action="submit">검수 요청</button><button class="btn btn-quiet" type="button" data-profile-action="rebase">최신 공개본·기준 비교</button><button class="btn btn-quiet" type="button" data-profile-action="cancel">작성 중단</button></div>` : revision.status === "submitted" ? `<p class="p-notice">제출한 원문을 검수 중입니다. 수정하려면 요청을 회수해주세요.</p><button class="btn btn-outline" type="button" data-profile-action="withdraw">검수 요청 회수</button>` : note("이 버전은 보존된 원문입니다. 수정할 내용은 새 변경안으로 작성하세요.")}</form>${admin && revision.status === "submitted" ? `<form id="profile-review" class="p-profile-review-form" data-no-session-draft><h3>제출 원문 검수</h3><label class="field">검수 결과<select name="action" required><option value="">결과 선택</option><option value="approve">공개 발행 · 원문 고정</option><option value="reject">수정 요청 · 반려</option></select></label><label class="field">코치에게 전달할 검토 의견<textarea name="reason" required minlength="5" maxlength="1000" rows="4"></textarea></label><label class="field">공개 승인 근거 · 발행 시 필수<textarea name="approvalReference" maxlength="2000" rows="3"></textarea></label>${note("소개와 샘플의 공개 범위를 확인하세요. 실재 코치 소개는 활성 담당 계정이 필요합니다. 소개 발행이 실력·성과 확인을 뜻하지 않습니다.")}<p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">검수 결과 적용</button></form>` : ""}` : empty("아직 작성한 소개가 없습니다.", "변경안을 만들고 검수할 소개와 샘플을 준비하세요.")}</article><section class="p-panel"><h2>코칭 접수 · ${inquiries.total}건</h2>${inquiries.bookings.map((b) => `<article class="p-profile-inquiry"><div class="p-inline-heading"><h3>${esc(b.studentName)} · ${esc(b.type)}</h3>${tag(b.status)}</div><p>${esc(b.preferredDate)} · ${esc(b.preferredTime)}</p><p class="p-muted">현재 담당 ${esc(b.assignedCoachName)} · 인계 v${b.assignmentVersion}</p>${b.profileSnapshotRecorded ? `<small>접수 당시 ${esc(b.originalCoachName)} · 소개 ${b.originalProfileVersion}판</small>` : note("이전 신청의 소개 버전은 미수집 상태입니다.")}${b.needsHandoff ? `<div class="p-notice amber">담당 코치가 비활성 상태입니다. 기존 접수를 유지하며 인계를 확인해주세요.</div>` : ""}<details><summary>신청 내용·안내</summary>${textBody(b.goal)}${b.reply ? `<h4>운영 안내</h4>${textBody(b.reply)}` : ""}</details>${admin ? `<div class="p-form-actions">${!["완료", "취소"].includes(b.status) ? `<button class="btn btn-outline btn-sm" data-handoff="${esc(b.id)}">담당 코치 인계</button>` : ""}<button class="btn btn-quiet btn-sm" data-handoff-history="${esc(b.id)}">인계 이력</button><a class="text-link" href="#/admin?tab=bookings">진행·안내 관리 ↗</a></div>` : ""}</article>`).join("") || note("현재 이 프로필이 담당하는 접수가 없습니다.")}${pages(route(id), params, "bookingPage", inquiries, "코칭 접수 페이지")}</section></div><aside><section class="p-panel" id="profile-public-baseline"><p class="eyebrow">PUBLISHED PROFILE</p><h2>지금 공개 중인 원문</h2>${payloadSummary(p.publishedPayload)}</section>${admin ? `<form class="p-panel" id="profile-management" data-no-session-draft><h2>운영·담당 계정</h2>${ownerFields(p, owners.users)}<label class="field">운영 상태<select name="state"><option value="active" ${p.state === "active" ? "selected" : ""}>활성</option><option value="inactive" ${p.state === "inactive" ? "selected" : ""}>비활성 · 신규 접수 중지</option></select></label><label class="field">변경 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label>${note("실재 공개 이력이나 담당 신청이 있는 계정 교체는 새 프로필·명시적 인계로 처리합니다. 비활성화해도 기존 신청은 보존됩니다.")}<p class="form-error" data-error role="alert"></p><button class="btn btn-outline" type="submit">운영 상태 저장</button></form>` : ""}<section class="p-panel"><h2>확인 사실·근거</h2>${data.verifications.map((f) => `<article class="p-profile-fact"><strong>${esc(f.label)}</strong>${tag(factState[f.status] || f.status, f.status === "active" ? "mint" : "amber")}<p>${esc(f.publicNote)}</p><small>${date(f.checkedAt)}${f.expiresAt ? ` → ${date(f.expiresAt)}` : ""}</small>${admin ? `<details><summary>비공개 확인 근거</summary><p class="p-policy-hash">${esc(f.approvalReference)}</p></details>${f.status !== "revoked" ? `<button class="btn btn-quiet btn-sm" data-revoke-fact="${esc(f.id)}">확인 사실 회수</button>` : ""}` : ""}</article>`).join("") || note("등록된 확인 사실이 없습니다.")}${admin && p.publishedPayload && !p.publishedPayload.pilot && p.publiclyAvailable ? `<form id="profile-fact" class="p-profile-review-form" data-no-session-draft><label class="field">확인 종류<select name="kind"><option value="identity">본인·프로필 자료</option><option value="experience">경험 자료</option><option value="teaching">교육 자료</option></select></label><label class="field">공개 표시명<input name="label" required minlength="3" maxlength="100"/></label><label class="field">공개 확인 설명<textarea name="publicNote" required minlength="5" maxlength="1000" rows="3"></textarea></label><label class="field">비공개 확인 근거<textarea name="approvalReference" required minlength="5" maxlength="2000" rows="3"></textarea></label><label class="field">적용 만료 시각 · 한국 시간<input name="expiresAt" type="datetime-local" step="1"/></label>${note("현재 공개판에만 연결합니다. 새 공개판은 근거를 다시 확인하며 이전 표시를 자동 승계하지 않습니다.")}<p class="form-error" data-error role="alert"></p><button class="btn btn-outline" type="submit">확인 사실 등록</button></form>` : note("확인 사실은 실재 코치의 활성 공개판에서 관리자가 등록합니다.")}</section><section class="p-panel"><h2>소개 버전 이력</h2>${history.revisions.map((r) => `<p><a class="text-link" href="#${route(id)}?revision=${esc(r.id)}">${esc(profileStates[r.status])} · ${date(r.createdAt)}</a><small class="p-muted">변경안 v${r.version} · 기준 공개 ${r.basePublicVersion}판</small></p>`).join("")}${pages(route(id), params, "page", history, "소개 이력 페이지")}</section><details class="p-panel"><summary>최근 진행 이력 · ${data.events.length}건</summary>${data.events.map((e) => `<p><strong>${esc(eventLabels[e.action] || e.action)}</strong><br>${esc(e.note)}<br><small class="p-muted">${date(e.createdAt)}</small></p>`).join("")}</details></aside></div></section>`,
    bind() {
      let current = revision;
      const editForm = document.querySelector("#coach-profile-editor");
      document
        .querySelector("#start-profile-draft")
        ?.addEventListener("click", async (event) => {
          event.currentTarget.disabled = true;
          try {
            const result = await ctx.api(
              `/api/studio/profiles/${id}/revisions`,
              { method: "POST", body: {} },
            );
            ctx.navigate(`${route(id)}?revision=${result.revision.id}`);
          } catch (error) {
            ctx.toast(error.message, "error");
            event.currentTarget.disabled = false;
          }
        });
      document.querySelectorAll("[data-sample-slot]").forEach((slot) => {
        const n = slot.dataset.sampleSlot,
          kind = slot.querySelector(`[name="sampleKind${n}"]`),
          course = slot.querySelector(`[name="sampleCourse${n}"]`);
        const toggle = () => {
          slot.querySelector("[data-sample-body]").hidden = !kind.value;
          slot.querySelector("[data-sample-lesson]").hidden =
            kind.value !== "lesson";
          slot.querySelector("[data-sample-video]").hidden =
            kind.value !== "video";
          slot.querySelector(`[name="sampleUrl${n}"]`).disabled =
            !editable || kind.value !== "video";
          for (const key of ["Course", "Lesson"])
            slot.querySelector(`[name="sample${key}${n}"]`).disabled =
              !editable || kind.value !== "lesson";
        };
        kind.addEventListener("change", toggle);
        toggle();
        editForm.addEventListener("tpc:prepare-draft", (event) => {
          const entry = (key) =>
            event.detail.entries.find((e) => e.key === key)?.value;
          const selectedKind = entry(`sampleKind${n}`) ?? kind.value;
          const selectedCourse = entry(`sampleCourse${n}`) ?? course.value;
          kind.value = selectedKind;
          course.innerHTML = `<option value="">강의 선택</option>${courses.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("")}`;
          course.value = selectedCourse;
          slot.querySelector(`[name="sampleLesson${n}"]`).innerHTML =
            lessonOptions(courses, selectedCourse, "");
          toggle();
        });
        editForm.addEventListener("tpc:draft-restored", toggle);
        editForm.addEventListener("tpc:reset-draft", () => {
          const saved = current.payload.samples[n] || {};
          kind.value = saved.kind || "";
          course.innerHTML = `<option value="">강의 선택</option>${saved.courseId && !courses.some((c) => c.id === saved.courseId) ? `<option value="${esc(saved.courseId)}">공개 범위 확인 필요 · ${esc(saved.courseId)}</option>` : ""}${courses.map((c) => `<option value="${esc(c.id)}">${esc(c.title)}</option>`).join("")}`;
          course.value = saved.courseId || "";
          slot.querySelector(`[name="sampleLesson${n}"]`).innerHTML =
            lessonOptions(courses, saved.courseId, saved.lessonId);
          toggle();
        });
        course.addEventListener("change", () => {
          slot.querySelector(`[name="sampleLesson${n}"]`).innerHTML =
            lessonOptions(courses, course.value, "");
        });
      });
      if (editable)
        bindForm(editForm, async (data) => {
          const result = await ctx.api(
            `/api/studio/profiles/${id}/revisions/${current.id}`,
            {
              method: "PATCH",
              body: {
                version: current.version,
                payload: readEditor(editForm),
                reason: data.reason,
              },
            },
          );
          ctx.toast("변경안을 저장했습니다. 공개본은 검수 후 바뀝니다.");
          ctx.navigate(`${route(id)}?revision=${result.revision.id}`);
          ctx.reload();
        });
      document.querySelectorAll("[data-profile-action]").forEach(
        (button) =>
          (button.onclick = async () => {
            const action = button.dataset.profileAction;
            try {
              if (
                action === "submit" &&
                (!editForm.reportValidity() ||
                  hasProfileChanges(readEditor(editForm), current.payload))
              )
                throw new Error(
                  "작성한 소개와 샘플을 먼저 변경안 저장해주세요.",
                );
              let latest = null;
              if (action === "rebase")
                latest = (await ctx.api(`/api/studio/profiles/${id}`)).profile;
              ctx.modal(
                `<p class="eyebrow">PROFILE CHANGE</p><h2>${{ submit: "검수 요청", withdraw: "요청 회수", cancel: "작성 중단", rebase: "최신 기준과 비교" }[action]}</h2>${latest ? `<p>기준 공개 ${current.basePublicVersion}판 / 운영 v${current.baseProfileVersion} → 최신 공개 ${latest.publicVersion}판 / 운영 v${latest.version}</p><details open><summary>최신 공개 소개</summary>${payloadSummary(latest.publishedPayload)}</details>${note("새 기준을 연결해도 현재 입력한 내용은 유지됩니다. 비교한 뒤 수동 저장하고 검수를 요청하세요.")}` : note("저장된 변경안에 적용합니다. 작성 중인 내용은 먼저 저장해주세요.")}<form id="profile-action-form" data-no-session-draft><label class="field">처리 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">${action === "rebase" ? "비교한 기준 연결" : "처리 적용"}</button></form>`,
                (dialog) =>
                  bindForm(dialog.querySelector("form"), async (values) => {
                    const result = await ctx.api(
                      `/api/studio/profiles/${id}/revisions/${current.id}`,
                      {
                        method: "POST",
                        body: {
                          action,
                          version: current.version,
                          reason: values.reason,
                        },
                      },
                    );
                    dialog.close();
                    if (action === "rebase") {
                      current = result.revision;
                      editForm.dataset.recoveryVersion = `${current.version}:${latest.version}:${latest.publicVersion}`;
                      document.querySelector("#profile-basis").textContent =
                        `기준 공개 ${current.basePublicVersion}판 · 운영 v${current.baseProfileVersion} · 변경안 v${current.version}`;
                      document.querySelector(
                        "#profile-public-baseline",
                      ).innerHTML =
                        `<p class="eyebrow">PUBLISHED PROFILE</p><h2>지금 공개 중인 원문</h2>${payloadSummary(latest.publishedPayload)}`;
                      ctx.toast(
                        "새 기준을 연결했습니다. 입력 내용을 확인하고 수동 저장해주세요.",
                      );
                    } else {
                      ctx.toast("변경안 상태를 저장했습니다.");
                      ctx.reload();
                    }
                  }),
              );
            } catch (error) {
              ctx.toast(error.message, "error");
            }
          }),
      );
      bindForm("#profile-review", async (values) => {
        await ctx.api(`/api/studio/profiles/${id}/revisions/${current.id}`, {
          method: "POST",
          body: { ...values, version: current.version },
        });
        await ctx.refreshCatalog();
        ctx.toast("검수 결과를 적용했습니다.");
        ctx.reload();
      });
      bindOwnerSearch(
        document.querySelector("#profile-management") ||
          document.createElement("div"),
        ctx,
        p,
      );
      bindForm("#profile-management", async (values) => {
        await ctx.api(`/api/manage/profiles/${id}`, {
          method: "PATCH",
          body: {
            version: p.version,
            ownerUserId: values.ownerUserId,
            state: values.state,
            reason: values.reason,
          },
        });
        await ctx.refreshCatalog();
        ctx.toast("운영 상태를 저장했습니다.");
        ctx.reload();
      });
      bindForm("#profile-fact", async (values) => {
        await ctx.api(`/api/manage/profiles/${id}/verifications`, {
          method: "POST",
          body: {
            ...values,
            publicVersion: p.publicVersion,
            expiresAt: fromSeoulInput(values.expiresAt),
          },
        });
        await ctx.refreshCatalog();
        ctx.toast("현재 공개판의 확인 사실을 기록했습니다.");
        ctx.reload();
      });
      document.querySelectorAll("[data-revoke-fact]").forEach(
        (button) =>
          (button.onclick = () => {
            const fact = data.verifications.find(
              (f) => f.id === button.dataset.revokeFact,
            );
            ctx.modal(
              `<h2>확인 사실 회수</h2><p>${esc(fact.label)}</p><form id="revoke-profile-fact"><label class="field">회수 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label><p class="form-error" data-error role="alert"></p><button class="btn btn-danger" type="submit">현재 표시 회수</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (values) => {
                  await ctx.api(
                    `/api/manage/profiles/${id}/verifications/${fact.id}`,
                    {
                      method: "POST",
                      body: { ...values, version: fact.version },
                    },
                  );
                  dialog.close();
                  ctx.reload();
                }),
            );
          }),
      );
      document.querySelectorAll("[data-handoff]").forEach(
        (button) =>
          (button.onclick = () => {
            const b = inquiries.bookings.find(
                (b) => b.id === button.dataset.handoff,
              ),
              targets = catalog.coaches.filter(
                (p) => p.id !== b.assignedCoachId && p.acceptsInquiry,
              );
            ctx.modal(
              `<p class="eyebrow">INQUIRY HANDOFF</p><h2>코칭 접수 인계</h2><p>${esc(b.studentName)} · 현재 ${esc(b.assignedCoachName)}</p><form id="profile-handoff" data-no-session-draft><label class="field">인계할 담당 코치<select name="coachId" required><option value="">활성 담당 선택</option>${targets.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}${t.pilot ? " · 파일럿" : ""}</option>`).join("")}</select></label><label class="field">신청자에게 안내할 인계 사유<textarea name="reason" required minlength="5" maxlength="1000" rows="3"></textarea></label>${note("원래 신청과 소개 기록을 보존합니다. 새 담당과 사유는 신청자의 마이 캠프에도 표시됩니다.")}<p class="form-error" data-error role="alert"></p><button class="btn btn-primary" type="submit">담당 인계</button></form>`,
              (dialog) =>
                bindForm(dialog.querySelector("form"), async (values) => {
                  await ctx.api(`/api/manage/bookings/${b.id}/handoff`, {
                    method: "POST",
                    body: { ...values, assignmentVersion: b.assignmentVersion },
                  });
                  dialog.close();
                  ctx.toast(
                    "담당을 인계하고 신청자에게 변경 내용을 기록했습니다.",
                  );
                  ctx.reload();
                }),
            );
          }),
      );
      document.querySelectorAll("[data-handoff-history]").forEach(
        (button) =>
          (button.onclick = async () => {
            try {
              const { handoffs } = await ctx.api(
                `/api/manage/bookings/${button.dataset.handoffHistory}/handoff`,
              );
              ctx.modal(
                `<h2>접수 인계 이력</h2>${handoffs.map((h) => `<article class="p-profile-inquiry"><p>${esc(h.from_coach_id)} → ${esc(h.to_coach_id)} · v${h.version}</p><p>${esc(h.reason)}</p><small>${date(h.createdAt)}</small></article>`).join("") || note("담당 인계 이력이 없습니다.")}`,
                () => {},
              );
            } catch (error) {
              ctx.toast(error.message, "error");
            }
          }),
      );
    },
  };
}
