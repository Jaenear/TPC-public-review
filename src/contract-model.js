import { fromSeoulInput } from "./access.js";
export const contractStates = {
  draft: "작성 중",
  submitted: "검토 중",
  rejected: "수정 요청",
  recorded: "합의 자료 기록",
  withdrawn: "적용 철회",
  cancelled: "작성 중단",
};
export const productionModels = {
  unresolved: "미정",
  company: "회사 주도형",
  joint: "공동 제작형",
  coach: "코치 주도형",
};
export const basisLabels = {
  unresolved: "미정",
  gross_collections: "총 수취액 기준",
  net_collections: "합의 공제 후 수취액 기준",
};
export const contractTextFields = {
  coachContribution: "코치 제작 기여",
  companyContribution: "회사 제작 기여",
  deductionPolicy: "공제 항목·비용 반영 기준",
  taxPolicy: "세무·증빙 기준",
  refundPolicy: "환불·조정 반영 기준",
  rightsPolicy: "원본·편집본·샘플 권리와 사용 범위",
  settlementPolicy: "정산 주기·기준일·보류·승인 절차",
  body: "합의 조건 원문",
};
export const bindingStates = {
  active: "적용 중",
  scheduled: "적용 예정",
  stopped: "적용 종료",
  expired: "기간 만료",
  cancelled: "적용 전 중단",
};
export function percentToBps(value) {
  const s = String(value ?? "").trim();
  if (!s) return null;
  if (!/^\d{1,3}(?:\.\d{1,2})?$/.test(s))
    throw new Error("배분율은 0~100%와 소수 둘째 자리까지 입력해주세요.");
  const [whole, part = ""] = s.split(".");
  const n = Number(whole) * 100 + Number(part.padEnd(2, "0"));
  if (n > 10000) throw new Error("배분율은 100%를 넘을 수 없습니다.");
  return n;
}
export function bpsToPercent(value) {
  return value == null
    ? ""
    : (value / 100)
        .toFixed(2)
        .replace(/\.00$/, "")
        .replace(/(\.\d)0$/, "$1");
}
export function contractPayload(values) {
  return {
    title: values.title.trim(),
    productionModel: values.productionModel,
    currency: "KRW",
    revenueBasis: values.revenueBasis,
    coachShareBps: percentToBps(values.coachPercent),
    companyShareBps: percentToBps(values.companyPercent),
    effectiveAt: fromSeoulInput(values.effectiveAt) || null,
    expiresAt: fromSeoulInput(values.expiresAt) || null,
    ...Object.fromEntries(
      Object.keys(contractTextFields).map((k) => [k, (values[k] || "").trim()]),
    ),
  };
}
const canonical = (x) =>
  JSON.stringify(
    Object.keys(x)
      .sort()
      .map((k) => [k, x[k]]),
  );
export const contractChanged = (next, saved) =>
  canonical(next) !== canonical(saved);
