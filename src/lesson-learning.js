import {
  LessonIntent,
  previewAnswer,
  completionSource,
  quizDraftEntries,
} from "./lesson-learning-model.js";
import { connectLessonContext } from "./lesson-context.js";
const stamp = (value) => {
  const date = new Date(value);
  return value && Number.isFinite(date.valueOf())
    ? date.toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "시각 미확인";
};
const href = (course) => `#/start/${encodeURIComponent(course.id)}`;
export function lessonChecks(lesson, course, ctx) {
  const e = ctx.esc,
    l = lesson.learning || {},
    count = l.quizAttemptCount;
  return `${lesson.quiz ? `<form class="m-quiz ll-quiz" id="lesson-quiz" data-session-draft data-recovery-version="${e(lesson.version + ":" + l.quizHash)}"><p class="eyebrow">QUICK CHECK</p><h2>이해한 내용을 확인해 볼까요?</h2><p class="ll-note">${l.canRecord ? "서버에서 답안을 확인하고 나의 풀이 기록에 저장합니다." : "공개 풀이 체험입니다. 결과는 저장되지 않습니다."}</p><fieldset><legend>${e(lesson.quiz.question)}</legend>${lesson.quiz.options.map((option, index) => `<label class="m-quiz-option"><input type="radio" name="answer" value="${index}" required><span class="m-option-key">${String.fromCharCode(65 + index)}</span><span>${e(option)}</span></label>`).join("")}</fieldset><button class="btn btn-outline btn-sm" id="quiz-submit" type="submit">${l.canRecord ? "답안 확인·기록 저장" : "체험 풀이 확인 · 미저장"}</button><div class="m-quiz-result" id="quiz-result" role="status" hidden></div><button type="button" class="btn btn-outline btn-sm" data-learning-retry="quiz" hidden>같은 답안 저장 다시 확인</button></form>` : ""}<section class="ll-records" id="lesson-records" aria-label="나의 차시 학습 기록"><div class="ll-record-head"><div><p class="eyebrow">MY LEARNING RECORD</p><h2>한 차시, 서로 다른 학습 기록.</h2></div><span class="pill">${l.canRecord ? "개인 기록 저장 가능" : "현재는 풀이 체험"}</span></div><p class="ll-note">퀴즈 풀이, 학습 완료 표시, 영상 재생 위치는 각각 따로 저장합니다.</p><div class="ll-facts"><div><small>나의 퀴즈 시도</small><strong id="learning-attempt-count">${count == null ? "—" : count + "회"}</strong></div><div><small>차시 완료 표시</small><strong id="learning-complete-state">${l.completion?.completed ? "완료" : "미완료"}</strong></div><div><small>영상 이어보기</small><strong>${lesson.videoAssetId ? "플레이어에서 확인" : "영상 없는 차시"}</strong></div></div>${l.canRecord ? '<button class="btn btn-outline btn-sm" id="learning-history-open">나의 차시 기록 보기</button>' : `<p class="ll-note">${ctx.user ? "기록 저장과 풀이 이력은 현재 수강권이 있을 때 이용할 수 있습니다." : "로그인하고 무료 강의를 등록하면 개인별 기록을 남길 수 있습니다."}</p>${!ctx.user ? '<button class="btn btn-outline btn-sm" data-learning-login>로그인하고 학습 기록 남기기</button>' : ""}<a class="text-link" href="${href(course)}">수강 시작·나의 수강권 확인 ↗</a>`}<div id="learning-history" hidden><div class="ll-history-tabs" role="group" aria-label="학습 기록 종류"><button type="button" class="btn btn-quiet btn-sm" data-history-kind="quiz" aria-pressed="true">퀴즈 시도</button><button type="button" class="btn btn-quiet btn-sm" data-history-kind="completion" aria-pressed="false">완료 기록</button><button type="button" class="btn btn-quiet btn-sm" id="history-latest">최신 기록 새로 보기</button></div><div id="history-content" aria-live="polite"></div><div class="ll-pager"><button type="button" class="btn btn-outline btn-sm" id="history-prev" disabled>이전 기록 페이지</button><span id="history-page"></span><button type="button" class="btn btn-outline btn-sm" id="history-next" disabled>다음 기록 페이지</button></div></div><p class="ll-feedback" id="learning-feedback" role="status"></p><button type="button" class="btn btn-outline btn-sm" id="learning-refresh" hidden>최신 수업·학습 상태 확인</button></section>`;
}
export function lessonCompletion(lesson, course, ctx) {
  const l = lesson.learning || {},
    completed = Boolean(l.completion?.completed);
  return `<div id="completion-learning"><p class="form-note" id="completion-source">${ctx.esc(completionSource(l.completion))}</p>${l.canRecord ? `<button type="button" class="btn ${completed ? "btn-outline" : "btn-primary"}" id="complete-lesson">${completed ? "✓ 학습 완료 · 완료 취소" : "학습 완료하기 ✓"}</button><p class="ll-note">풀이 결과나 영상 시청만으로 완료 처리하지 않습니다.</p><p id="completion-feedback" role="status" class="ll-feedback"></p><button type="button" class="btn btn-outline btn-sm" data-learning-retry="completion" hidden>같은 완료 요청 다시 확인</button>` : `<p class="ll-note">완료 표시는 수강 등록 후 현재 계정에서 직접 저장할 수 있습니다.</p><a class="btn btn-outline" href="${href(course)}">수강 시작 안내 →</a>`}</div>`;
}
export function bindLessonLearning(lesson, course, ctx) {
  const root = document.querySelector(".m-reader");
  if (!root) return;
  const e = ctx.esc,
    ownerId = ctx.user?.id || null,
    route = location.hash;
  const intent = new LessonIntent({
    ownerId,
    courseId: course.id,
    lessonId: lesson.id,
    learning: lesson.learning || {},
  });
  const base = `/api/courses/${encodeURIComponent(course.id)}/lessons/${encodeURIComponent(lesson.id)}`;
  const active = () => root.isConnected && location.hash === route;
  const states = {
    quiz: { busy: false, pending: null, uncertain: false },
    completion: { busy: false, pending: null, uncertain: false },
  };
  let blocked = false,
    historyKind = "quiz",
    historyPage = 1,
    through = null,
    historyBusy = false,
    historyMore = false;
  const notice = (message, kind = null, error = false) => {
    if (!active()) return;
    const node = root.querySelector(
      kind === "completion" ? "#completion-feedback" : "#learning-feedback",
    );
    if (node) {
      node.textContent = message;
      node.classList.toggle("is-error", error);
    }
  };
  function controls() {
    for (const [kind, state] of Object.entries(states)) {
      const disabled = blocked || state.busy || state.uncertain;
      const button = root.querySelector(
        kind === "quiz" ? "#quiz-submit" : "#complete-lesson",
      );
      if (button) button.disabled = disabled;
      if (kind === "quiz")
        root
          .querySelectorAll("#lesson-quiz input")
          .forEach((input) => (input.disabled = disabled));
      const retry = root.querySelector(`[data-learning-retry="${kind}"]`);
      if (retry) {
        retry.hidden = !state.uncertain;
        retry.disabled = state.busy || blocked;
      }
    }
  }
  const informUnavailable = connectLessonContext(
    root,
    { ownerId, courseId: course.id, lessonId: lesson.id },
    (message) => {
      blocked = true;
      root.querySelector("#learning-refresh").hidden = false;
      notice(message + " 현재 계정·수업을 확인해주세요.", null, true);
      controls();
    },
  );
  function update(result, request = null) {
    if (!intent.isSnapshot(result))
      throw new Error("현재 수업의 학습 상태를 확인할 수 없습니다.");
    if (request && !intent.confirms(request.kind, request.body, result))
      throw new Error(
        "저장 영수증이 요청과 일치하지 않습니다. 같은 요청으로 다시 확인해주세요.",
      );
    const applied = intent.accept(result);
    if (!applied && !request) return false;
    const l = intent.learning;
    root.querySelector("#learning-attempt-count").textContent =
      l.quizAttemptCount == null ? "—" : l.quizAttemptCount + "회";
    root.querySelector("#learning-complete-state").textContent = l.completion
      .completed
      ? "완료"
      : "미완료";
    root.querySelector("#completion-source").textContent = completionSource(
      l.completion,
    );
    const button = root.querySelector("#complete-lesson");
    if (button) {
      button.textContent = l.completion.completed
        ? "✓ 학습 완료 · 완료 취소"
        : "학습 완료하기 ✓";
      button.classList.toggle("btn-primary", !l.completion.completed);
      button.classList.toggle("btn-outline", l.completion.completed);
    }
    if (applied && result.enrollment) {
      const done = new Set(
        result.enrollment.completedLessons.filter((id) =>
          course.lessons.some((item) => item.id === id),
        ),
      );
      const sidebar = document.querySelector(".m-curriculum");
      sidebar?.querySelectorAll("[data-lesson-id]").forEach((link, index) => {
        const number = link.querySelector(".m-lesson-number"),
          completed = done.has(link.dataset.lessonId);
        number.classList.toggle("is-done", completed);
        number.textContent = completed
          ? "✓"
          : String(index + 1).padStart(2, "0");
      });
      const percent = course.lessons.length
        ? Math.round((done.size / course.lessons.length) * 100)
        : 0;
      const label = sidebar?.querySelector(".m-progress-label strong"),
        bar = sidebar?.querySelector(".m-progress > span");
      if (label) label.textContent = percent + "%";
      if (bar) bar.style.width = percent + "%";
    }
    if (
      l.lessonVersion !== lesson.version ||
      l.quizHash !== (lesson.learning?.quizHash || null)
    ) {
      blocked = true;
      root.querySelector("#learning-refresh").hidden = false;
      notice(
        "현재 수업이 변경됐습니다. 저장 영수증과 현재 상태를 확인한 뒤 최신 수업을 열어주세요.",
      );
      informUnavailable("현재 수업이 변경됐습니다.");
    }
    return applied;
  }
  function quizResult(attempt, saved) {
    const node = root.querySelector("#quiz-result");
    if (!node) return;
    node.hidden = false;
    node.className = `m-quiz-result ${attempt.correct ? "is-correct" : "is-retry"}`;
    node.innerHTML = `<strong>${attempt.correct ? "정답입니다." : "다시 한번 생각해 보세요."}</strong><p>${e(attempt.quiz?.explanation ?? attempt.explanation)}</p><small>${saved ? `나의 풀이에 저장됨 · 수업 v${e(attempt.lessonVersion)}${attempt.currentQuiz ? "" : " · 현재 문제와 다름"}` : "공개 풀이 체험 · 저장되지 않은 결과"}</small>`;
  }
  async function submit(kind, value) {
    const state = states[kind];
    if (!active() || blocked || state.busy) return;
    if (state.uncertain && !state.pending) return;
    state.pending ||= intent.prepare(kind, value);
    const body = state.pending;
    state.busy = true;
    controls();
    notice("저장 결과 확인 중…", kind);
    try {
      const result = await ctx.api(
        kind === "quiz" ? base + "/quiz-attempts" : "/api/progress",
        { method: kind === "quiz" ? "POST" : "PUT", ownerId, body },
      );
      if (!active()) return;
      update(result, { kind, body });
      if (kind === "quiz")
        quizResult(
          {
            ...result.attempt,
            currentQuiz: result.attempt.quizHash === intent.learning.quizHash,
          },
          true,
        );
      const oldReceipt =
        kind === "completion" &&
        result.receipt.completed !== intent.learning.completion.completed;
      notice(
        blocked
          ? "요청의 저장 기록은 확인했습니다. 수업이 변경되어 최신 수업 확인이 필요합니다."
          : oldReceipt
            ? "이 요청은 이전에 처리됐습니다. 더 최근의 완료 상태를 유지합니다."
            : result.replayed
              ? "같은 요청의 저장 기록을 확인했습니다."
              : kind === "quiz"
                ? "답안을 저장했습니다. 학습 완료 표시는 별도로 할 수 있습니다."
                : "완료 상태를 저장했습니다.",
        kind,
      );
      state.pending = null;
      state.uncertain = false;
    } catch (error) {
      if (!active()) return;
      if ([400, 401, 403, 404, 409].includes(error.status)) {
        blocked = true;
        state.pending = null;
        state.uncertain = false;
        root.querySelector("#learning-refresh").hidden = false;
        notice(
          error.message + " 현재 계정·수업의 저장 상태를 확인해주세요.",
          kind,
          true,
        );
        if ([401, 403, 404].includes(error.status))
          informUnavailable(error.message);
      } else {
        state.uncertain = true;
        notice(
          "저장 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인할 수 있습니다.",
          kind,
          true,
        );
      }
    } finally {
      state.busy = false;
      if (active()) controls();
    }
  }
  root
    .querySelector("#lesson-quiz")
    ?.addEventListener("tpc:capture-draft", (event) => {
      const radios = [
        ...event.currentTarget.querySelectorAll('input[name="answer"]'),
      ];
      if (!radios.some((input) => input.disabled)) return;
      const selected = radios.findIndex((input) => input.checked);
      event.detail.entries = event.detail.entries
        .filter((entry) => entry.key !== "answer")
        .concat(quizDraftEntries(lesson.quiz, selected));
    });
  root.querySelector("#lesson-quiz")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = new FormData(event.currentTarget).get("answer");
    if (input == null) return;
    const answer = Number(input);
    if (!intent.learning.canRecord) {
      quizResult(previewAnswer(lesson.quiz, answer), false);
      return;
    }
    submit("quiz", answer);
  });
  root
    .querySelector("#complete-lesson")
    ?.addEventListener("click", () =>
      submit("completion", !intent.learning.completion.completed),
    );
  root
    .querySelectorAll("[data-learning-retry]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        submit(button.dataset.learningRetry),
      ),
    );
  root
    .querySelectorAll("[data-learning-login]")
    .forEach((button) =>
      button.addEventListener("click", () => ctx.requireAuth()),
    );
  root
    .querySelector("#learning-refresh")
    ?.addEventListener("click", () => ctx.reload());
  function historyRows(items) {
    return (
      items
        .map((item) =>
          historyKind === "quiz"
            ? `<article class="ll-history-item"><div class="ll-history-meta"><strong>${item.correct ? "정답" : "다시 보기"}</strong><span>${e(stamp(item.createdAt))} · 수업 v${e(item.lessonVersion)}</span></div><p>${e(item.quiz.question)}</p><p class="ll-note">내 답안: ${e(item.quiz.options[item.answer])} · ${item.currentQuiz ? "현재 문제와 동일" : "이후 문제가 변경됨"}</p><details><summary>당시 문제·해설 보기</summary><ol>${item.quiz.options.map((o) => `<li>${e(o)}</li>`).join("")}</ol><p>${e(item.quiz.explanation)}</p></details></article>`
            : `<article class="ll-history-item"><div class="ll-history-meta"><strong>${item.completed ? "학습 완료 표시" : "학습 완료 취소"}</strong><span>${e(stamp(item.createdAt))}</span></div><p class="ll-note">${item.changed ? "완료 상태 변경" : "기존 상태 유지 확인"} · 요청 당시 수업 v${e(item.lessonVersion)}</p></article>`,
        )
        .join("") ||
      '<p class="ll-note">저장된 기록이 없습니다. 이전 기록을 추정해 채우지 않습니다.</p>'
    );
  }
  async function loadHistory(page = 1, latest = false) {
    if (historyBusy || !active()) return;
    if (latest) through = null;
    historyBusy = true;
    root
      .querySelectorAll("#learning-history button")
      .forEach((button) => (button.disabled = true));
    const kind = historyKind;
    const query = new URLSearchParams({ page: String(page) });
    if (through) query.set("through", through);
    try {
      const response = await ctx.api(
        base +
          (kind === "quiz" ? "/quiz-attempts?" : "/completion-events?") +
          query,
        { ownerId },
      );
      if (!active() || kind !== historyKind) return;
      const applied = update(response);
      historyPage = response.page;
      through = response.through;
      historyMore = response.hasMore;
      root.querySelector("#history-content").innerHTML = historyRows(
        response.items,
      );
      root.querySelector("#history-page").textContent =
        `${historyPage}페이지 · ${response.total}건`;
      if (!applied && !blocked)
        notice(
          "현재 학습 상태는 유지했습니다. 새로 저장한 내용은 최신 기록 다시 보기에서 확인해주세요.",
        );
    } catch (error) {
      if (active()) {
        if ([401, 403, 404].includes(error.status))
          informUnavailable(error.message);
        root.querySelector("#history-content").textContent = error.message;
        notice(
          "학습 이력을 확인하지 못했습니다. 현재 수강권을 확인하고 직접 다시 조회해주세요.",
          null,
          true,
        );
      }
    } finally {
      historyBusy = false;
      if (active()) {
        root
          .querySelectorAll("#learning-history button")
          .forEach((button) => (button.disabled = false));
        root.querySelector("#history-prev").disabled = historyPage <= 1;
        root.querySelector("#history-next").disabled = !historyMore;
      }
    }
  }
  root
    .querySelector("#learning-history-open")
    ?.addEventListener("click", () => {
      root.querySelector("#learning-history").hidden = false;
      loadHistory(1, true);
    });
  root.querySelectorAll("[data-history-kind]").forEach((button) =>
    button.addEventListener("click", () => {
      historyKind = button.dataset.historyKind;
      root
        .querySelectorAll("[data-history-kind]")
        .forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      loadHistory(1, true);
    }),
  );
  root
    .querySelector("#history-latest")
    ?.addEventListener("click", () => loadHistory(1, true));
  root
    .querySelector("#history-prev")
    ?.addEventListener("click", () => loadHistory(historyPage - 1));
  root
    .querySelector("#history-next")
    ?.addEventListener("click", () => loadHistory(historyPage + 1));
}
