/** Tab-memory-only identity intent and link handling. No browser storage. */
export const identityRoutes = Object.freeze({
  "/account/verify": { purpose: "verify_email", title: "이메일 인증", inspection: "/api/account/identity/verification-inspections", completion: "/api/account/identity/verification-completions", authenticated: true },
  "/account/email-change": { purpose: "change_email", title: "이메일 변경 확인", inspection: "/api/account/identity/email-change-inspections", completion: "/api/account/identity/email-change-completions", authenticated: true },
  "/account/reset": { purpose: "reset_password", title: "비밀번호 재설정", inspection: "/api/auth/recovery/inspections", completion: "/api/auth/recovery/completions", authenticated: false },
});

export class IdentityLinkMemory {
  #value = null;
  accept(hash, replace = () => {}) {
    const raw = String(hash || "").replace(/^#/, "");
    const cut = raw.indexOf("?");
    const path = cut < 0 ? raw : raw.slice(0, cut);
    if (!identityRoutes[path]) { this.clear(); return false; }
    if (cut >= 0) {
      const query = new URLSearchParams(raw.length <= 2048 ? raw.slice(cut + 1) : "");
      const token = query.get("token") || "";
      const valid = raw.length <= 2048 && [...query.keys()].length === 1 && query.getAll("token").length === 1 && /^[A-Za-z0-9_-]{16,128}$/.test(token);
      this.#value = { path, token: valid ? token : "", invalid: !valid, completed: false };
      replace("#" + path);
    } else if (this.#value?.path !== path) {
      this.#value = { path, token: "", invalid: true, completed: false };
    }
    return true;
  }
  forPath(path) { return this.#value?.path === path ? { ...this.#value } : null; }
  complete(path, result) {
    if (identityRoutes[path]) this.#value = { path, token: "", invalid: false, completed: true, requiresLogin: Boolean(result.requiresLogin) };
  }
  actorChanged(previous, next) { if (previous && previous !== next) this.clear(); }
  clear() { this.#value = null; }
}

export const sameIdentityContext = (left, right) => left?.ownerId === right?.ownerId && left?.epoch === right?.epoch;
export const identityRemoteLinkStates = Object.freeze(["ready", "expired", "used", "revoked", "invalid"]);
/** A conservative UI recheck boundary; server completion remains authoritative. */
export function identityLinkDeadline(serverTime, expiresAt, started, received) {
  if (![serverTime, expiresAt].every(v => Number.isSafeInteger(v) && v >= 0) ||
      ![started, received].every(Number.isFinite) || received < started)
    throw new TypeError("Invalid identity link timing");
  // Server timestamps have one-second resolution. Include that margin and
  // the request round trip; never extend a link using the client's wall clock.
  return received + Math.max(0, (expiresAt - serverTime - 1) * 1000 - (received - started));
}
export { retryAfterSeconds } from "./api-retry.js";
export function identityFailure(error) {
  if (error.sessionChanged) return "로그인 상태가 변경됐습니다. 현재 계정에서 다시 확인해주세요.";
  if (error.status === 429) return error.retryAfterSeconds === null || error.retryAfterSeconds === undefined ? "요청이 많습니다. 잠시 후 다시 시도해주세요." : `요청이 많습니다. ${Math.max(1, error.retryAfterSeconds)}초 후 다시 시도해주세요.`;
  if (error.status === 503) return "현재 이메일 인증·복구를 이용할 수 없습니다. 잠시 후 다시 확인해주세요.";
  if (error.status === 401) return "본인 계정으로 로그인한 뒤 다시 확인해주세요.";
  if (error.status === 403) return "현재 로그인한 계정에서 이 요청을 완료할 수 없습니다. 계정을 확인하고 메일의 링크를 다시 열어주세요.";
  if (error.status === 409) return "계정 정보나 요청 상태가 변경됐습니다. 최신 내용을 확인한 뒤 다시 제출해주세요.";
  if (error.status === 400) return "입력이나 링크를 확인해주세요. 만료되었거나 다시 요청한 링크는 사용할 수 없습니다.";
  return "처리 결과를 확인하지 못했습니다. 같은 요청의 결과를 다시 확인하거나 잠시 후 다시 시도해주세요.";
}

export function newIdentityIntent(action, values, makeKey = () => crypto.randomUUID()) {
  return { action, body: { requestKey: makeKey(), ...values } };
}

export const linkStates = Object.freeze({
  ready: { label: "완료 준비", title: "요청을 확인하고 완료하세요.", description: "링크를 여는 것만으로 계정은 변경되지 않습니다. 아래 버튼으로 직접 완료해주세요." },
  expired: { label: "유효 시간 지남", title: "링크의 유효 시간이 지났습니다.", description: "새로운 메일을 요청한 뒤 가장 최근 링크를 열어주세요." },
  used: { label: "이미 사용한 링크", title: "이미 완료에 사용한 링크입니다.", description: "이 링크로 계정 정보를 다시 바꾸지 않습니다. 현재 계정을 확인하거나 새로 요청해주세요." },
  revoked: { label: "사용할 수 없는 링크", title: "이 링크는 더 이상 사용할 수 없습니다.", description: "다시 요청했거나 계정 정보가 변경됐을 수 있습니다. 가장 최근 메일을 확인해주세요." },
  invalid: { label: "링크 확인 필요", title: "유효한 링크를 확인해주세요.", description: "메일의 전체 링크를 다시 열거나 새로운 요청을 시작해주세요." },
  elapsed: { label: "유효 시간 재확인", title: "링크의 유효 시간을 다시 확인해주세요.", description: "링크를 연 뒤 시간이 지났거나 화면을 다시 열었습니다. 최신 상태를 확인하거나 새로운 메일을 요청해주세요." },
  uncertain: { label: "처리 결과 확인 필요", title: "완료 요청의 결과를 먼저 확인해주세요.", description: "처리 결과를 확인하지 못했습니다. 같은 요청을 다시 확인해도 중복으로 적용되지 않습니다. 비밀번호를 새로 입력하지 않고 원래 요청의 결과를 확인합니다." },
});
