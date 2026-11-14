import { esc, icon } from "./ui.js";
import { head, date } from "./platform-ui.js";

export async function renderLearningStart(path, ctx) {
  const match = path.match(/^\/start\/([a-z0-9-]+)$/);
  if (!match) return null;
  const { start } = await ctx.api(`/api/courses/${match[1]}/start`);
  return startPage(start, ctx);
}

export function startPage(start, ctx) {
  const c = start.course,
    active = start.access.canAccess,
    ack = start.acknowledgement;
  const steps = [
    [
      "내 계정",
      start.signedIn,
      start.signedIn
        ? "본인의 수강권과 기록을 확인하고 있습니다."
        : "같은 계정으로 로그인하면 학습 기록을 이어갈 수 있습니다.",
    ],
    ["수강권 확인", active, start.message],
    [
      "학습 방법 확인",
      ack.confirmed,
      ack.confirmed
        ? `확인한 안내 · ${date(ack.acknowledgedAt)}`
        : "아래 안내를 읽고 직접 확인을 남길 수 있습니다.",
    ],
    [
      "첫 학습 기록",
      start.progress.completed > 0,
      `${start.progress.completed} / ${start.progress.total} 공개 차시 완료 · 안내 확인과 학습 진도는 따로 기록합니다.`,
    ],
  ];
  return {
    title: c.title + " · 수강 시작 안내",
    html: `<section class="container p-page p-start" id="learning-start"><a class="back-link" href="#/course/${esc(c.id)}">← 강의 상세</a>${head("YOUR LEARNING, FROM HERE", c.title, "한 차시를 배우고, 이해를 확인하고, 다음 질문으로 이어가세요.")}<div class="p-start-layout"><article><div class="p-panel p-start-status"><span class="eyebrow">${active ? "READY TO LEARN" : "PREPARE YOUR NEXT STEP"}</span><h2>${active ? (start.state === "complete" ? "배운 내용을 다시 꺼내볼 시간." : "나의 다음 차시가 기다립니다.") : "지금 필요한 시작 단계."}</h2><p>${esc(start.message)}</p>${start.purchase.mode === "test" && !active ? '<p class="form-note">결제 기능은 테스트 환경으로 구분됩니다.</p>' : ""}<ol class="p-start-steps" aria-label="수강 시작 단계">${steps.map(([title, done, text], i) => `<li class="${done ? "is-done" : ""}"><span class="p-start-number" aria-hidden="true">${done ? "✓" : i + 1}</span><div><h3>${esc(title)} <small>${done ? "완료" : "다음 단계"}</small></h3><p>${esc(text)}</p></div></li>`).join("")}</ol></div><section class="p-panel"><p class="eyebrow">A SMALL LEARNING ROUTINE</p><h2>학습은 이렇게 쌓아갑니다.</h2><div class="p-start-method"><article><span>01 / LEARN</span><h3>한 차시씩, 내 속도로</h3><p>영상이 있는 차시는 일시정지하고 다시 볼 수 있습니다. 본문과 핵심 정리로 판단의 기준을 확인하세요.</p></article><article><span>02 / CHECK</span><h3>이해하고 직접 완료하기</h3><p>확인 퀴즈와 해설을 읽은 뒤 학습 완료를 직접 표시하세요. 영상 시청 위치와 차시 완료 기록은 별도로 저장합니다.</p></article><article><span>03 / ASK</span><h3>막힌 지점을 질문으로</h3><p>차시의 질문하기에서 상황과 고민을 적어보세요. 내 질문과 담당 코치의 답변은 강의 Q/A에서 이어집니다.</p></article></div>${start.canAcknowledge ? (ack.confirmed ? `<p class="p-notice" role="status">${icon("check", 18)} 이 안내를 확인했습니다. 실제 학습 완료는 각 차시에 기록합니다.</p>` : '<form id="start-ack-form" data-no-session-draft><label class="p-consent-option"><input type="checkbox" name="readGuide" required/> 학습 방법과 기록 방식을 확인했습니다.</label><p class="form-error" data-start-error role="alert"></p><button class="btn btn-outline" type="submit">안내 확인 기록</button></form>') : '<p class="form-note">활성 수강권이 있는 계정에서 안내 확인을 기록할 수 있습니다.</p>'}</section></article><aside class="p-panel p-start-next"><p class="eyebrow">YOUR NEXT CHAPTER</p><h2>${start.nextLesson ? esc(start.nextLesson.title) : "다음 학습을 준비하세요."}</h2><p>${start.nextLesson ? (start.state === "complete" ? "완료한 학습을 다시 복습할 수 있습니다." : "현재 공개 구성의 첫 미완료 차시입니다. 이전 시청 위치는 해당 원본에서 이어집니다.") : esc(start.message)}</p><button class="btn btn-primary btn-block" type="button" id="start-primary">${esc(start.primary.label)} ${icon("arrow", 18)}</button><p class="form-error" id="start-primary-error" role="alert"></p>${start.preview && !active ? `<a class="btn btn-outline btn-block" href="${esc(start.preview.href)}">무료 미리보기 · ${esc(start.preview.title)}</a>` : ""}${start.questionsHref ? `<a class="btn btn-outline btn-block" href="${esc(start.questionsHref)}">이 강의에 질문하기</a><a class="text-link" href="#/qa?courseId=${esc(c.id)}">강의 질문·답변 보기 ↗</a>` : ""}<a class="text-link" href="#/my">내 학습·수강권 확인 ↗</a><p class="form-note">안내 확인은 언제든 다시 볼 수 있습니다. 차시 완료·결제 동의·수강권 발급을 대신하지 않습니다.</p><button class="btn btn-quiet btn-sm" type="button" id="start-refresh">현재 상태 다시 확인</button></aside></div></section>`,
    bind() {
      const root = document.querySelector("#learning-start"),
        button = root.querySelector("#start-primary");
      root.querySelector("#start-refresh").onclick = () => ctx.reload();
      button.onclick = async () => {
        if (start.primary.kind === "login") {
          ctx.requireAuth();
          return;
        }
        if (start.primary.kind !== "register") {
          ctx.navigate(start.primary.href.slice(1));
          return;
        }
        if (!ctx.user) {
          ctx.requireAuth();
          return;
        }
        button.disabled = true;
        const error = root.querySelector("#start-primary-error");
        error.textContent = "";
        try {
          await ctx.api("/api/enrollments", {
            method: "POST",
            body: { courseId: c.id },
          });
          if (root.isConnected) ctx.navigate(`/start/${c.id}`);
        } catch (e) {
          if (root.isConnected) error.textContent = e.message;
        } finally {
          if (root.isConnected) button.disabled = false;
        }
      };
      const form = root.querySelector("#start-ack-form");
      if (form)
        form.onsubmit = async (event) => {
          event.preventDefault();
          if (!form.reportValidity()) return;
          const save = form.querySelector("button"),
            error = form.querySelector("[data-start-error]");
          save.disabled = true;
          error.textContent = "";
          try {
            await ctx.api(`/api/courses/${c.id}/start`, {
              method: "POST",
              body: {
                guideVersion: start.guideVersion,
                basisHash: start.basisHash,
              },
            });
            if (root.isConnected) ctx.reload();
          } catch (e) {
            if (root.isConnected) error.textContent = e.message;
          } finally {
            if (root.isConnected) save.disabled = false;
          }
        };
    },
  };
}
