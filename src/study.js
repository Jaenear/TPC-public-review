import { HANDS, EXAMPLE_RANGE, parseRange, rangeStats } from "./ranges.js";
import { esc, icon } from "./ui.js";
const workspaceDrafts = new Map();
export function clearWorkspaceDrafts() {
  workspaceDrafts.clear();
}
// Move the previous browser draft cache to memory once, then remove its keys.
try {
  for (const key of Object.keys(sessionStorage).filter((key) =>
    key.startsWith("tpc-study-draft:"),
  )) {
    try {
      const draft = JSON.parse(sessionStorage.getItem(key));
      if (draft && typeof draft.title === "string")
        workspaceDrafts.set(key, draft);
    } catch {}
    sessionStorage.removeItem(key);
  }
} catch {}

export function rangeGrid(selected, interactive = false) {
  const chosen = new Set(selected);
  return `<div class="range-matrix ${interactive ? "range-editable" : ""}" ${interactive ? 'role="group" aria-label="169개 핸드 클래스 선택"' : 'aria-hidden="true"'}>${HANDS.map((hand, index) => (interactive ? `<button type="button" class="range-cell ${chosen.has(hand) ? "is-selected" : ""} ${index % 14 === 0 ? "is-pair" : ""}" data-hand="${hand}" tabindex="${index === 0 ? 0 : -1}" aria-pressed="${chosen.has(hand)}" aria-label="${hand}">${hand}</button>` : `<span class="range-cell ${chosen.has(hand) ? "is-selected" : ""} ${index % 14 === 0 ? "is-pair" : ""}">${hand}</span>`)).join("")}</div>`;
}

export function studyPreview(compact = false) {
  return `<div class="study-window ${compact ? "study-window-compact" : ""}" aria-label="TPC 레인지 스터디 화면 미리보기"><div class="window-bar"><div class="window-brand">${icon("layers", 15)} TPC <span>STUDY</span></div><span class="window-note-label"><i></i> 레인지 노트</span><span class="window-dots">•••</span></div><div class="window-tabs"><span class="selected">프리플랍</span><span>핸드 노트</span><span>학습 기록</span></div><div class="window-parameters"><span>NL Hold'em</span><span>6-max</span><span>100 <small>BB</small></span><span class="parameter-position">BTN</span></div><div class="window-body"><div class="window-range">${rangeGrid(EXAMPLE_RANGE)}<div class="window-legend"><span><i></i>선택한 핸드</span><span>총 1,326조합</span></div></div><div class="window-side"><div class="window-hand"><span>A<small>♠</small></span><span>K<small>♠</small></span></div><span class="window-side-label">HAND CLASS</span><strong>AKs</strong><p>수딧 브로드웨이</p><div class="window-stat"><span>조합 수</span><strong>4</strong></div><div class="window-stat"><span>표기</span><strong>Suited</strong></div><div class="window-insight">${icon("book", 15)}<span>가능한 핸드를 묶어<br>생각하는 연습.</span></div></div></div><div class="window-footer"><span>직접 편집하는 학습용 예시</span><a href="#/study">스터디 열기 ${icon("external", 15)}</a></div></div>`;
}

export async function renderStudyPage(path, params, ctx) {
  if (path === "/study") return renderWorkspace(params, ctx);
  if (path === "/practice") return renderPractice(ctx);
  if (path === "/notes") return renderNotes(ctx);
  return null;
}

async function renderWorkspace(params, ctx) {
  let saved = [],
    current;
  if (ctx.user) {
    saved = (await ctx.api("/api/study-notes")).notes;
    current = saved.find((note) => note.id === params.get("note"));
    if (params.get("note") && !current)
      return {
        html: `<section class="container m-page"><div class="empty-state"><h1>노트를 찾을 수 없습니다.</h1><p>내 계정의 노트 목록을 확인해주세요.</p><a class="btn btn-primary" href="#/notes">내 스터디 노트</a></div></section>`,
      };
  } else if (params.has("note")) return authGate(ctx);
  const draftKey = `tpc-study-draft:${ctx.user?.id || "guest"}`;
  const draft = workspaceDrafts.get(draftKey);
  const values = current ||
    (params.has("new") ? null : draft) || {
      title: "나의 프리플랍 노트",
      format: "캐시",
      position: "BTN",
      stack: 100,
      selected: [],
      notes: "",
    };
  let selected = new Set(
    values.selected.filter((hand) => HANDS.includes(hand)),
  );
  const stats = rangeStats([...selected]);
  return {
    html: `<section class="container study-page"><div class="workspace-heading"><div><p class="eyebrow">STUDY YOUR GAME</p><h1>판단을 정리하는 <span>나만의 스터디.</span></h1><p>레인지를 직접 만들고, 상황과 생각을 한곳에 기록하세요.</p></div><a href="#/notes" class="btn btn-outline btn-sm">${icon("book", 16)} 내 노트 ${saved.length ? `<span class="count-badge">${saved.length}</span>` : ""}</a></div><div class="study-workspace"><aside class="study-settings"><div class="panel-heading"><span>${icon("layers", 17)} 스팟 설정</span><span class="pill">PREFLOP</span></div><form id="spot-form" data-session-draft data-recovery-version="${esc(current ? JSON.stringify(current) : "new")}"><label class="field">노트 제목<input name="title" value="${esc(values.title)}" required minlength="2" maxlength="100"/></label><label class="field">게임 유형<select name="format">${["캐시", "토너먼트"].map((v) => `<option ${v === values.format ? "selected" : ""}>${v}</option>`).join("")}</select></label><div class="field"><label for="study-position">내 포지션</label><select id="study-position" name="position">${["UTG", "HJ", "CO", "BTN", "SB", "BB"].map((v) => `<option ${v === values.position ? "selected" : ""}>${v}</option>`).join("")}</select></div><label class="field">유효 스택 (BB)<input name="stack" type="number" value="${esc(values.stack)}" min="0.1" max="20000" step="any" required/></label><div class="study-divider"></div><p class="settings-label">선택 도구</p><button class="study-tool" id="sample-range" type="button">${icon("layers", 15)} 표기 연습용 예시 불러오기</button><button class="study-tool" id="all-range" type="button">${icon("check", 15)} 전체 선택</button><button class="study-tool" id="clear-range" type="button">${icon("close", 15)} 선택 초기화</button><p class="settings-footnote">예시는 표기·편집 연습용입니다.<br>포지션별 권장 전략이 아닙니다.</p></form></aside><div class="study-editor"><div class="panel-heading"><span>핸드 레인지 <small>13 × 13</small></span><span class="matrix-help">클릭하여 선택 · 방향키로 이동</span></div><div class="matrix-wrap">${rangeGrid([...selected], true)}</div><div class="range-stats"><div><span>선택 핸드</span><strong id="range-classes">${stats.classes}<small> / 169</small></strong></div><div><span>가능한 조합</span><strong id="range-combos">${stats.combinations}<small> / 1,326</small></strong></div><div><span>전체 대비</span><strong id="range-percent">${stats.percentage.toFixed(1)}<small>%</small></strong></div></div><form id="notation-form" class="notation-form"><label for="range-notation">핸드 표기로 입력</label><div><input id="range-notation" name="notation" placeholder="예: 88+, ATs+, KQs, AJo+" autocomplete="off"/><button class="btn btn-quiet btn-sm" type="submit">적용</button></div><p>페어 6조합 · 수딧 4조합 · 오프수트 12조합</p><p id="notation-error" class="form-error" role="alert"></p></form><div class="range-editor-footer"><span><i class="legend-selected"></i> 내가 선택한 핸드</span><button type="button" class="btn-text" id="export-range">${icon("download", 14)} JSON 내보내기</button></div></div><aside class="study-side"><div class="study-note-panel"><div class="panel-heading"><span>${icon("book", 17)} 학습 메모</span><span id="note-save-status">${current ? "저장된 노트" : "작성 중"}</span></div><label for="study-notes" class="sr-only">레인지 학습 메모</label><textarea id="study-notes" form="spot-form" name="notes" maxlength="10000" placeholder="어떤 상황을 가정했나요?&#10;선택한 핸드와 그 이유, 확인하고 싶은 질문을 적어보세요.">${esc(values.notes)}</textarea><div class="study-save-area"><button class="btn btn-primary btn-block" id="save-note">${icon("book", 16)} ${current ? "변경사항 저장" : "내 스터디에 저장"}</button><p>${ctx.user ? "이 계정에만 보이는 나의 학습 노트입니다." : "로그인하면 계정에 노트를 저장할 수 있어요."}</p></div></div><div class="pot-odds-panel"><div class="panel-heading"><span>${icon("calculator", 17)} 팟오즈 계산</span></div><div class="field-row"><label class="field">베팅 전 팟<input id="odds-pot" type="number" min="0" max="1000000000" step="any" value="100"/></label><label class="field">상대 베팅<input id="odds-bet" type="number" min="0.01" max="1000000000" step="any" value="50"/></label></div><div class="bet-presets">${[33, 50, 75, 100].map((p) => `<button type="button" data-bet-percent="${p}">${p}%</button>`).join("")}</div><div class="pot-odds-result"><span>단순 손익분기 지분</span><strong id="odds-result">25.0<small>%</small></strong></div><p id="odds-formula">50 ÷ (100 + 50 + 50)</p><p class="odds-caveat">추가 액션·레이크가 없다는 가정의 수학적 기준입니다. 실제 승률이나 권장 액션을 계산하지 않습니다.</p></div><a href="#/review" class="study-review-link">이 스팟, 코치에게 질문하기 ${icon("arrow", 17)}</a></aside></div><p class="workspace-disclaimer">${icon("info", 15)} 직접 작성하는 레인지 노트입니다. 솔버 전략·EV 계산은 추후 연결됩니다.</p></section>`,
    bind() {
      const $ = (s) => document.querySelector(s),
        form = $("#spot-form");
      function snapshot() {
        const data = Object.fromEntries(new FormData(form));
        return {
          ...data,
          stack: Number(data.stack),
          selected: HANDS.filter((hand) => selected.has(hand)),
          notes: $("#study-notes").value,
        };
      }
      function markDirty() {
        $("#note-save-status").textContent = "저장하지 않은 변경";
        workspaceDrafts.set(draftKey, snapshot());
      }
      function update() {
        const stats = rangeStats([...selected]);
        document.querySelectorAll("[data-hand]").forEach((button) => {
          const active = selected.has(button.dataset.hand);
          button.classList.toggle("is-selected", active);
          button.setAttribute("aria-pressed", String(active));
        });
        $("#range-classes").innerHTML = `${stats.classes}<small> / 169</small>`;
        $("#range-combos").innerHTML =
          `${stats.combinations}<small> / 1,326</small>`;
        $("#range-percent").innerHTML =
          `${stats.percentage.toFixed(1)}<small>%</small>`;
        markDirty();
      }
      form.addEventListener("tpc:capture-draft", (event) => {
        const hands = HANDS.filter((hand) => selected.has(hand));
        event.detail.entries.push({
          key: "selectedRange",
          index: 0,
          type: "text",
          custom: true,
          label: "선택 레인지",
          value: hands.join(", "),
          dirty:
            JSON.stringify(hands) !==
            JSON.stringify(
              HANDS.filter((hand) => values.selected.includes(hand)),
            ),
        });
      });
      form.addEventListener("tpc:draft-restored", (event) => {
        const entry = event.detail.entries.find(
          (entry) => entry.key === "selectedRange" && entry.custom,
        );
        if (entry)
          selected = new Set(
            entry.value.split(/,\s*/).filter((hand) => HANDS.includes(hand)),
          );
        update();
      });
      document.querySelectorAll("[data-hand]").forEach((button, index) => {
        button.onclick = () => {
          document
            .querySelectorAll("[data-hand]")
            .forEach((cell) => (cell.tabIndex = cell === button ? 0 : -1));
          const hand = button.dataset.hand;
          selected.has(hand) ? selected.delete(hand) : selected.add(hand);
          update();
        };
        button.onkeydown = (event) => {
          const shift = {
            ArrowRight: 1,
            ArrowLeft: -1,
            ArrowDown: 13,
            ArrowUp: -13,
          }[event.key];
          if (shift) {
            event.preventDefault();
            const cells = document.querySelectorAll("[data-hand]");
            const next = cells[Math.max(0, Math.min(168, index + shift))];
            cells.forEach((cell) => (cell.tabIndex = cell === next ? 0 : -1));
            next.focus();
          }
        };
      });
      $("#sample-range").onclick = () => {
        selected = new Set(EXAMPLE_RANGE);
        update();
      };
      $("#all-range").onclick = () => {
        selected = new Set(HANDS);
        update();
      };
      $("#clear-range").onclick = () => {
        selected.clear();
        update();
      };
      $("#notation-form").onsubmit = (event) => {
        event.preventDefault();
        try {
          selected = new Set(parseRange($("#range-notation").value));
          $("#notation-error").textContent = "";
          update();
        } catch (error) {
          $("#notation-error").textContent = error.message;
        }
      };
      form.oninput = markDirty;
      form.onchange = markDirty;
      $("#study-notes").oninput = markDirty;
      $("#export-range").onclick = () => {
        const data = snapshot();
        const blob = new Blob(
          [
            JSON.stringify(
              {
                ...data,
                source: "TPC 사용자 작성 노트",
                statistics: rangeStats(data.selected),
              },
              null,
              2,
            ),
          ],
          { type: "application/json" },
        );
        const url = URL.createObjectURL(blob),
          link = document.createElement("a");
        link.href = url;
        link.download = "tpc-range-note.json";
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        ctx.toast("레인지 노트를 내보냈습니다.");
      };
      $("#save-note").onclick = () => {
        if (!form.reportValidity()) return;
        const data = snapshot();
        async function save() {
          const button = $("#save-note");
          if (button) button.disabled = true;
          try {
            const result = await ctx.api(
              `/api/study-notes${current ? `/${encodeURIComponent(current.id)}` : ""}`,
              { method: current ? "PUT" : "POST", body: data },
            );
            workspaceDrafts.delete(draftKey);
            ctx.toast("스터디 노트를 저장했습니다.");
            ctx.navigate(`/study?note=${result.note.id}`);
          } catch (error) {
            ctx.toast(error.message, "error");
            if (button) button.disabled = false;
          }
        }
        if (ctx.requireAuth(save)) save();
      };
      function calculate() {
        const pot = Number($("#odds-pot").value),
          bet = Number($("#odds-bet").value),
          valid =
            $("#odds-pot").value !== "" &&
            $("#odds-bet").value !== "" &&
            Number.isFinite(pot) &&
            Number.isFinite(bet) &&
            pot >= 0 &&
            bet > 0 &&
            pot <= 1e9 &&
            bet <= 1e9;
        $("#odds-result").innerHTML = valid
          ? `${((bet / (pot + bet * 2)) * 100).toFixed(1)}<small>%</small>`
          : "—";
        $("#odds-formula").textContent = valid
          ? `${bet.toLocaleString()} ÷ (${pot.toLocaleString()} + ${bet.toLocaleString()} + ${bet.toLocaleString()})`
          : "팟은 0 이상, 베팅은 0보다 큰 숫자를 입력해주세요.";
      }
      $("#odds-pot").oninput = calculate;
      $("#odds-bet").oninput = calculate;
      document.querySelectorAll("[data-bet-percent]").forEach(
        (b) =>
          (b.onclick = () => {
            const pot = Number($("#odds-pot").value);
            $("#odds-bet").value = String(
              Math.round(pot * Number(b.dataset.betPercent)) / 100,
            );
            calculate();
          }),
      );
    },
  };
}

function authGate(ctx) {
  return {
    html: `<section class="container m-page"><div class="m-auth-gate"><span class="m-gate-mark">${icon("book", 44)}</span><p class="eyebrow">YOUR PERSONAL STUDY SPACE</p><h1>나의 생각을, 이어가세요.</h1><p>로그인하면 레인지 노트와 트레이닝 기록을<br>언제든 다시 살펴볼 수 있습니다.</p><button class="btn btn-primary" id="notes-login">로그인하고 확인하기 ${icon("arrow", 17)}</button><a class="btn btn-outline" href="#/study">레인지 노트 먼저 만들기</a></div></section>`,
    bind() {
      document.querySelector("#notes-login").onclick = () => ctx.requireAuth();
    },
  };
}

async function renderNotes(ctx) {
  if (!ctx.user) return authGate(ctx);
  const data = await ctx.api("/api/me"),
    notes = data.studyNotes || [],
    practice = data.practice || [];
  return {
    html: `<section class="container notes-page"><div class="workspace-heading"><div><p class="eyebrow">YOUR STUDY SPACE</p><h1>쌓이는 기록, <span>선명해지는 판단.</span></h1><p>직접 작성한 레인지 노트와 개념 퀴즈 기록입니다.</p></div><a class="btn btn-primary btn-sm" href="#/study?new=1">새 노트 만들기 ${icon("external", 17)}</a></div><nav class="study-subnav"><a href="#/my">${icon("book", 16)} 내 강의</a><a class="active" href="#/notes">${icon("layers", 16)} 스터디 기록</a><a href="#/my?tab=reviews">${icon("message", 16)} 신청 내역</a></nav><div class="section-heading notes-section-heading"><h2>내 레인지 노트 <span>${notes.length}</span></h2></div>${
      notes.length
        ? `<div class="notes-grid">${notes
            .map((note) => {
              const stats = rangeStats(note.selected);
              return `<article class="saved-note"><a href="#/study?note=${esc(note.id)}"><div class="saved-note-preview">${rangeGrid(note.selected)}<span>${esc(note.position)} · ${esc(note.stack)} BB</span></div><div class="saved-note-body"><h3>${esc(note.title)}</h3><p>${esc(note.format)} · ${stats.classes}개 핸드 · ${stats.percentage.toFixed(1)}%</p><span>${new Date(note.updatedAt).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })} 수정 ${icon("external", 16)}</span></div></a><button class="note-delete" data-delete-note="${esc(note.id)}" aria-label="${esc(note.title)} 삭제">${icon("close", 15)}</button></article>`;
            })
            .join("")}</div>`
        : `<div class="empty-state"><span class="m-empty-mark">${icon("layers", 35)}</span><h2>첫 번째 스터디 노트를 만들어보세요.</h2><p>핸드 레인지를 직접 선택하고, 고민한 이유를 남겨보세요.</p><a class="btn btn-primary" href="#/study?new=1">레인지 노트 만들기 ${icon("arrow", 17)}</a></div>`
    }<div class="section-heading notes-section-heading"><h2>개념 퀴즈 기록 <span>${practice.length}</span></h2><a class="text-link" href="#/practice">트레이닝 시작 ${icon("arrow", 17)}</a></div>${practice.length ? `<div class="practice-history">${practice.map((attempt) => `<div><span class="practice-history-icon">${icon("target", 20)}</span><div><strong>포커 개념 체크</strong><p>${new Date(attempt.createdAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}</p></div><span>${attempt.correct} / ${attempt.total} 정답</span><b>${Math.round((attempt.correct / attempt.total) * 100)}<small>%</small></b></div>`).join("")}</div>` : `<div class="empty-state"><h2>학습한 내용을 가볍게 확인해보세요.</h2><p>프리플랍부터 핸드 복기까지, 짧은 퀴즈로 개념을 점검합니다.</p><a class="btn btn-outline" href="#/practice">퀴즈 시작하기 ${icon("arrow", 17)}</a></div>`}</section>`,
    bind() {
      document.querySelectorAll("[data-delete-note]").forEach(
        (button) =>
          (button.onclick = () => {
            const note = notes.find((n) => n.id === button.dataset.deleteNote);
            ctx.modal(
              `<p class="eyebrow">DELETE NOTE</p><h2>이 노트를 삭제할까요?</h2><p class="dialog-description">${esc(note.title)}<br>삭제하면 복구할 수 없습니다. 필요하면 편집 화면에서 JSON으로 먼저 내보내세요.</p><div class="dialog-actions"><button class="btn btn-outline" id="cancel-delete">돌아가기</button><button class="btn btn-danger" id="confirm-delete">노트 삭제</button></div>`,
              (dialog) => {
                dialog.querySelector("#cancel-delete").onclick = () =>
                  dialog.close();
                dialog.querySelector("#confirm-delete").onclick = async (e) => {
                  e.target.disabled = true;
                  try {
                    await ctx.api(
                      `/api/study-notes/${encodeURIComponent(note.id)}`,
                      { method: "DELETE" },
                    );
                    dialog.close();
                    ctx.toast("노트를 삭제했습니다.");
                    ctx.reload();
                  } catch (error) {
                    ctx.toast(error.message, "error");
                    e.target.disabled = false;
                  }
                };
              },
            );
          }),
      );
    },
  };
}

async function renderPractice(ctx) {
  const publicQuestions = (await ctx.api("/api/practice/questions")).questions;
  const loaded = publicQuestions
    .map((question) => ({
      course: ctx.catalog.courses.find(
        (course) => course.id === question.courseId,
      ),
      lesson: {
        id: question.lessonId,
        title: question.title,
        quiz: question.quiz,
      },
    }))
    .filter((entry) => entry.course);
  let questions = loaded.filter((entry) => entry.lesson.quiz),
    round = [],
    index = 0,
    answers = [],
    answered = false;
  return {
    html: `<section class="container practice-page"><div class="centered-heading"><span class="section-pill">PRACTICE</span><h1>알고 있는 것을,<br><span>나의 판단으로.</span></h1><p>짧은 퀴즈로 핵심 개념을 확인하고, 필요한 레슨으로 돌아가세요.</p></div><div class="practice-shell" id="practice-shell"><div class="practice-intro"><div class="practice-intro-graphic">${icon("target", 55)}<span>THINK. CHOOSE. LEARN.</span></div><p class="eyebrow">A LITTLE PRACTICE, EVERY DAY</p><h2>오늘의 포커 개념 체크</h2><p>정답을 고르고, 이유를 이해하는 시간.<br>공개 레슨에서 ${Math.min(5, questions.length)}문제를 골라 연습합니다.</p><div class="practice-topics"><span>프리플랍</span><span>팟오즈</span><span>핸드 리뷰</span><span>학습 루틴</span></div><button id="start-practice" class="btn btn-primary">트레이닝 시작하기 ${icon("arrow", 18)}</button><small>로그인 없이 연습 가능 · 로그인하면 결과 저장</small></div></div><p class="practice-footnote">공개 레슨의 개념을 확인하는 퀴즈입니다. 솔버 액션이나 EV를 평가하지 않습니다.</p></section>`,
    bind() {
      const shell = document.querySelector("#practice-shell");
      function start() {
        round = [...questions];
        for (let i = round.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [round[i], round[j]] = [round[j], round[i]];
        }
        round = round.slice(0, 5);
        index = 0;
        answers = [];
        show();
      }
      function show() {
        answered = false;
        const entry = round[index],
          quiz = entry.lesson.quiz;
        shell.innerHTML = `<div class="practice-question"><div class="practice-question-top"><span class="pill">${esc(entry.course.category)}</span><span>${String(index + 1).padStart(2, "0")} <small>/ ${String(round.length).padStart(2, "0")}</small></span></div><div class="practice-progress"><span style="width:${(index / round.length) * 100}%"></span></div><p class="eyebrow">THINK IT THROUGH</p><h2>${esc(quiz.question)}</h2><form id="practice-answer"><fieldset><legend class="sr-only">답을 선택해주세요</legend>${quiz.options.map((option, i) => `<label class="practice-option"><input name="answer" type="radio" value="${i}" required/><span>${String.fromCharCode(65 + i)}</span><strong>${esc(option)}</strong></label>`).join("")}</fieldset><button class="btn btn-primary btn-block" type="submit">답 확인하기 ${icon("arrow", 18)}</button></form><div id="practice-feedback" aria-live="polite"></div></div>`;
        shell.querySelector("form").onsubmit = (e) => {
          e.preventDefault();
          if (answered) return;
          const choice = Number(new FormData(e.target).get("answer"));
          answered = true;
          answers.push({
            courseId: entry.course.id,
            lessonId: entry.lesson.id,
            answer: choice,
          });
          const correct = choice === quiz.answer;
          shell
            .querySelectorAll("input")
            .forEach((input) => (input.disabled = true));
          e.target.querySelector("button").hidden = true;
          shell.querySelector("#practice-feedback").innerHTML =
            `<div class="practice-feedback ${correct ? "correct" : "incorrect"}"><span>${icon(correct ? "check" : "info", 22)}</span><div><strong>${correct ? "좋은 판단입니다." : "이렇게 생각해볼까요?"}</strong><p>${esc(quiz.explanation)}</p><a href="#/learn/${esc(entry.course.id)}/${esc(entry.lesson.id)}">관련 레슨 복습하기 ${icon("external", 14)}</a></div></div><button class="btn btn-primary btn-block" id="next-practice">${index + 1 === round.length ? "트레이닝 결과 보기" : "다음 문제"} ${icon("arrow", 18)}</button>`;
          shell.querySelector("#next-practice").onclick = () => {
            index++;
            index === round.length ? finish() : show();
          };
        };
      }
      function finish() {
        const correct = answers.filter(
          (answer, i) => answer.answer === round[i].lesson.quiz.answer,
        ).length;
        const submitted = [...answers];
        let saved = false;
        shell.innerHTML = `<div class="practice-result"><div class="result-ring" style="--result:${(correct / round.length) * 100}%"><div><strong>${Math.round((correct / round.length) * 100)}<small>%</small></strong><span>정답률</span></div></div><p class="eyebrow">ONE STEP FORWARD</p><h2>오늘의 학습을 완성했어요.</h2><p>${round.length}문제 중 ${correct}문제 정답.<br>틀린 문제의 개념도 레슨에서 다시 확인해보세요.</p><div class="practice-results-list">${round.map((entry, i) => `<a href="#/learn/${esc(entry.course.id)}/${esc(entry.lesson.id)}"><span class="${submitted[i].answer === entry.lesson.quiz.answer ? "is-correct" : "is-incorrect"}">${icon(submitted[i].answer === entry.lesson.quiz.answer ? "check" : "book", 16)}</span>${esc(entry.lesson.title)}${icon("chevron", 16)}</a>`).join("")}</div><button class="btn btn-primary btn-block" id="save-practice">학습 기록에 저장 ${icon("book", 17)}</button><button class="btn btn-outline btn-block" id="restart-practice">한 번 더 연습하기 ${icon("arrow", 17)}</button><a class="text-link" href="#/notes">내 스터디 기록 보기 ${icon("external", 16)}</a></div>`;
        shell.querySelector("#restart-practice").onclick = start;
        shell.querySelector("#save-practice").onclick = () => {
          async function save() {
            if (saved) return;
            const button = shell.querySelector("#save-practice");
            if (button) button.disabled = true;
            try {
              await ctx.api("/api/practice/results", {
                method: "POST",
                body: { answers: submitted },
              });
              saved = true;
              ctx.toast("트레이닝 기록을 저장했습니다.");
              ctx.navigate("/notes");
            } catch (error) {
              ctx.toast(error.message, "error");
              if (button) button.disabled = false;
            }
          }
          if (ctx.requireAuth(save)) save();
        };
      }
      document.querySelector("#start-practice").onclick = start;
    },
  };
}
