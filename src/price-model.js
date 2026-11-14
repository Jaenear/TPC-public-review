import { fromSeoulInput } from "./access.js";
export const priceStates = {
  draft: "가격 초안",
  submitted: "검수 대기",
  rejected: "수정 요청",
  published: "가격 발행",
  withdrawn: "가격 철회",
  cancelled: "작성 중단",
};
export const priceTiming = {
  active: "현재 적용",
  missing: "가격 미정",
  scheduled: "시행 예정",
  expired: "가격 종료",
  withdrawn: "가격 철회",
  paused: "신규 판매 중지",
  course_changed: "상품 변경 재검수",
  instructor_unavailable: "담당자 확인",
  unpublished: "상품 발행 대기",
  pilot_live: "파일럿 · 상용 판매 불가",
};
export function isFreeCourse(course) {
  return (
    course.price === 0 &&
    course.pricing?.ready !== false &&
    course.pricing?.purchaseReady !== false
  );
}
export function priceLabel(course) {
  if (
    course.price == null ||
    course.pricing?.ready === false ||
    course.pricing?.purchaseReady === false
  )
    return course.pricing?.status === "paused"
      ? "신규 수강 중지"
      : "제공 준비 중";
  return course.price === 0
    ? "무료"
    : "₩" + Number(course.price).toLocaleString("ko-KR");
}
export function integerPrice(value) {
  const s = String(value ?? "").trim();
  if (!s) return null;
  if (!/^\d{1,8}$/.test(s) || Number(s) > 10000000)
    throw new Error("가격은 0~10,000,000원의 정수로 입력해주세요.");
  return Number(s);
}
export function pricePayload(v) {
  return {
    label: v.label.trim(),
    amount: integerPrice(v.amount),
    currency: "KRW",
    effectiveAt: fromSeoulInput(v.effectiveAt) || null,
    expiresAt: fromSeoulInput(v.expiresAt) || null,
  };
}
const canonical = (x) =>
  JSON.stringify(
    Object.keys(x)
      .sort()
      .map((k) => [k, x[k]]),
  );
export const priceChanged = (a, b) => canonical(a) !== canonical(b);
