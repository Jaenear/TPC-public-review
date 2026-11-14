import { esc } from "./ui.js";

export const hasCourseAccess = (entry) =>
  typeof entry?.canAccess === "boolean"
    ? entry.canAccess
    : entry?.accessStatus === "active";
export const accessLabel = (state) =>
  ({
    active: "수강 가능",
    scheduled: "시작 예정",
    expired: "기간 만료",
    revoked: "권한 회수",
    none: "수강권 없음",
  })[state] || "수강권 확인 필요";
export const sourceLabel = (source) =>
  ({
    legacy: "기존 등록",
    free: "무료 등록",
    pilot: "파일럿 승인",
    payment: "결제 승인",
    manual: "운영자 발급",
  })[source] || "등록 출처";
export const accessDate = (value) =>
  value
    ? new Date(value).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "";
export const grantPeriod = (grant) =>
  `${grant.startsAt ? accessDate(grant.startsAt) : "기존 시작 제한 없음"} → ${grant.expiresAt ? accessDate(grant.expiresAt) : "종료 기한 없음"}`;

export function toSeoulInput(value) {
  if (!value) return "";
  const time = Date.parse(value);
  return Number.isFinite(time)
    ? new Date(time + 9 * 3600000).toISOString().slice(0, 19)
    : "";
}
export function fromSeoulInput(value) {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value))
    throw new Error("한국 시간 기준의 날짜와 시각을 확인해주세요.");
  const normalized = value.length === 16 ? value + ":00" : value;
  const time = Date.parse(normalized + "+09:00");
  if (
    !Number.isFinite(time) ||
    toSeoulInput(new Date(time).toISOString()) !== normalized
  )
    throw new Error("유효한 날짜와 시각을 입력해주세요.");
  return new Date(time).toISOString().replace(".000Z", "Z");
}

export function accessNotice(entry) {
  if (!entry) return "";
  const access = entry.access || entry;
  const state = access.status || entry.accessStatus;
  const detail =
    state === "active"
      ? access.expiresAt
        ? `${accessDate(access.expiresAt)}까지 연속 수강 가능`
        : "종료 기한 없이 수강할 수 있습니다."
      : state === "scheduled"
        ? `${accessDate(access.nextStartsAt)}부터 학습을 시작할 수 있습니다.`
        : "완료 진도와 기존 학습 기록은 그대로 보존됩니다.";
  return `<div class="p-access-summary ${state === "active" ? "" : "is-inactive"}"><strong>${esc(accessLabel(state))}</strong><p>${esc(detail)}</p>${state === "revoked" && access.reason ? `<p class="p-access-reason">회수 안내: ${esc(access.reason)}</p>` : ""}<small>시간은 한국 시간(UTC+09:00) 기준입니다.</small></div>`;
}

export function grantHistory(access) {
  if (!access?.grants?.length) return "";
  return `<details class="p-grant-history"><summary>출처별 수강권 · ${access.grants.length}건</summary><div>${access.grants.map((grant) => `<article class="p-grant-record"><div><strong>${esc(sourceLabel(grant.source))}</strong><span class="p-tag ${grant.status === "active" ? "mint" : "amber"}">${esc(accessLabel(grant.status))}</span></div><p>${esc(grantPeriod(grant))}</p>${grant.reference ? `<p>발급 참조: ${esc(grant.reference)}</p>` : ""}${grant.reason ? `<p>발급 사유: ${esc(grant.reason)}</p>` : ""}${grant.revocationReason ? `<p>회수 사유: ${esc(grant.revocationReason)}</p>` : ""}</article>`).join("")}</div></details>`;
}
