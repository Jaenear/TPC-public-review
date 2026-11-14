import { esc } from "./ui.js";
import { weekdays } from "./coaching-model.js";

const field = (name, title, value, { area = false, min = 0, max = 2000, type = "text", step = 1, note = "", required = true } = {}) => `<label>${esc(title)}${area ? `<textarea name="${name}" rows="3" minlength="${min}" maxlength="${max}" ${required ? "required" : ""}>${esc(value)}</textarea>` : `<input name="${name}" type="${type}" value="${esc(value)}" ${type === "number" ? `min="${min}" max="${max}" step="${step}"` : `minlength="${min}" maxlength="${max}"`} ${required ? "required" : ""}>`}${note ? `<small>${esc(note)}</small>` : ""}</label>`;
const choice = (name, title, value, choices) => `<label>${title}<select name="${name}">${choices.map(([key, text]) => `<option value="${key}" ${key === value ? "selected" : ""}>${text}</option>`).join("")}</select></label>`;
const group = (title, body, open = true) => `<details class="co-form-section" ${open ? "open" : ""}><summary>${title}</summary><div class="co-fields">${body}</div></details>`;

export function productFields(p) {
  return group("01 · 어떤 배움을 제공하나요", field("title", "상품 제목", p.title, { min: 3, max: 120 }) + field("description", "상품 설명", p.description, { area: true, min: 20, max: 5000 }) + field("topics", "전문 주제", p.topics.join("\n"), { area: true, max: 647, note: "한 줄에 한 주제 · 1–8개, 각 80자 이내" }) + field("audience", "대상과 권장 수준", p.audience, { area: true, min: 10, max: 1500 }) + field("outcomes", "학습 성과", p.outcomes.join("\n"), { area: true, max: 3009, note: "한 줄에 한 성과 · 1–10개, 각 300자 이내" })) +
    group("02 · 진행 방식과 가격", choice("method", "진행 방식", p.method, [["online_live", "온라인 실시간"], ["in_person", "대면"]]) + field("deliverySummary", "진행 장소·도구·방식", p.deliverySummary, { area: true, min: 10 }) + field("durationMinutes", "회당 시간 (분)", p.durationMinutes, { type: "number", min: 15, max: 240, step: 5 }) + field("sessionCount", "총 회차", p.sessionCount, { type: "number", min: 1, max: 24 }) + field("price", "전체 회차 총액 (KRW)", p.price, { type: "number", min: 0, max: 10000000, note: "회당 가격이 아닌 전체 상품의 총액입니다." }) + field("sessionValidityDays", "회차 유효 기간 (일)", p.sessionValidityDays, { type: "number", min: 1, max: 365 }) + field("preparation", "수강생 준비물", p.preparation, { area: true, max: 3000, required: false }) + field("bufferBeforeMinutes", "시작 전 준비 시간 (분)", p.bufferBeforeMinutes, { type: "number", min: 0, max: 180, step: 5 }) + field("bufferAfterMinutes", "종료 후 정리 시간 (분)", p.bufferAfterMinutes, { type: "number", min: 0, max: 180, step: 5 }) + choice("bookingSequence", "향후 예약·결제 순서", p.bookingSequence, [["coach_then_payment", "코치 확인 후 결제"], ["payment_then_coach", "결제 후 코치 확인"]])) +
    group("03 · 수행과 변경 조건", [ ["cancellationSummary", "취소·환불 기준"], ["rescheduleSummary", "일정 변경 기준"], ["noShowSummary", "수강생 불참 기준"], ["coachAbsenceSummary", "코치 불참 시 처리"], ["fulfillmentSummary", "회차 수행·완료 기준"] ].map(([name, title]) => field(name, title, p[name], { area: true, min: 10 })).join(""));
}
export function weeklyRow(row = { weekday: 0, start: "19:00", end: "22:00", endsNextDay: false }) {
  return `<div class="co-week-row" data-week-row><label>요일<select data-week-day>${weekdays.map((w, i) => `<option value="${i}" ${i === row.weekday ? "selected" : ""}>${w}요일</option>`).join("")}</select></label><label>시작<input type="time" data-week-start value="${esc(row.start)}" required></label><label>종료<input data-week-end value="${esc(row.end)}" placeholder="22:00 또는 24:00" pattern="(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00" required></label><label class="co-check"><input type="checkbox" data-week-next ${row.endsNextDay ? "checked" : ""}>다음 날 종료</label><button type="button" class="btn btn-ghost btn-sm" data-remove-row aria-label="가능 시간 구간 삭제">삭제</button></div>`;
}
export function offRow(row = { startsAt: "", endsAt: "", reason: "" }) {
  return `<div class="co-off-row" data-off-row><label>시작 · UTC offset 포함<input data-off-start value="${esc(row.originalStartsAt || row.startsAt)}" placeholder="2026-11-12T19:00:00+09:00" required></label><label>종료 · UTC offset 포함<input data-off-end value="${esc(row.originalEndsAt || row.endsAt)}" placeholder="2026-11-12T21:00:00+09:00" required></label><label>내부 사유<input data-off-reason maxlength="1000" value="${esc(row.reason)}" required></label><button type="button" class="btn btn-ghost btn-sm" data-remove-row aria-label="제외 시간 구간 삭제">삭제</button></div>`;
}
export function calendarFields(p) {
  return group("01 · 일정 기준", field("timeZone", "코치 기준 시간대 (IANA)", p.timeZone, { max: 100, note: "예: Asia/Seoul, America/New_York · 현지 시각 기준으로 생성합니다." }) + field("effectiveFrom", "적용 시작일", p.effectiveFrom, { type: "date" }) + field("effectiveUntil", "적용 종료일", p.effectiveUntil, { type: "date" }) + field("leadTimeHours", "최소 사전 준비 (시간)", p.leadTimeHours, { type: "number", min: 0, max: 720 }) + field("horizonDays", "후보 게시 범위 (일)", p.horizonDays, { type: "number", min: 1, max: 180 }) + field("stepMinutes", "후보 시작 간격 (분)", p.stepMinutes, { type: "number", min: 5, max: 240, step: 5 })) +
    `<section class="co-form-section"><h3>02 · 주간 가능 시간</h3><p class="p-muted">여러 구간을 추가할 수 있습니다. 자정을 넘기면 다음 날 종료를 선택하세요. 24:00은 당일 자정 경계입니다.</p><div data-week-rows>${p.weekly.map(weeklyRow).join("")}</div><button type="button" class="btn btn-outline btn-sm" data-add-week>가능 시간 추가</button></section>` +
    `<section class="co-form-section"><h3>03 · 휴무·플레이 제외 시간</h3><p class="p-muted">초 단위 ISO 시각과 UTC offset을 명시하세요. 이 내부 사유는 공개되지 않습니다. DST 중복·누락 시각은 후보 생성에서 제외됩니다.</p><div data-off-rows>${p.timeOff.map(offRow).join("")}</div><button type="button" class="btn btn-outline btn-sm" data-add-off>제외 시간 추가</button></section>`;
}
const lines = (value) => value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
export function readCoachingForm(form, kind) {
  const p = Object.fromEntries(new FormData(form));
  delete p.reason;
  if (kind === "product") {
    for (const name of ["durationMinutes", "sessionCount", "price", "bufferBeforeMinutes", "bufferAfterMinutes", "sessionValidityDays"]) p[name] = Number(p[name]);
    p.topics = lines(p.topics); p.outcomes = lines(p.outcomes); p.currency = "KRW";
  } else {
    for (const name of ["leadTimeHours", "horizonDays", "stepMinutes"]) p[name] = Number(p[name]);
    p.weekly = [...form.querySelectorAll("[data-week-row]")].map((row) => ({ weekday: Number(row.querySelector("[data-week-day]").value), start: row.querySelector("[data-week-start]").value, end: row.querySelector("[data-week-end]").value, endsNextDay: row.querySelector("[data-week-next]").checked }));
    p.timeOff = [...form.querySelectorAll("[data-off-row]")].map((row) => ({ startsAt: row.querySelector("[data-off-start]").value, endsAt: row.querySelector("[data-off-end]").value, reason: row.querySelector("[data-off-reason]").value }));
  }
  return p;
}
export const reasonField = () => field("reason", "변경·처리 사유", "", { min: 3, max: 1000 });
