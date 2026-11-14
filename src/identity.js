import { identityRoutes, sameIdentityContext, newIdentityIntent, identityFailure, identityRemoteLinkStates, identityLinkDeadline } from "./identity-state.js";
import { identityShell, accountBody, recoveryBody, linkBody, accountLoginBody } from "./identity-view.js";

const endpoints = {
  profile: "/api/me/profile",
  verify: "/api/account/identity/verification-requests",
  change: "/api/account/identity/email-change-requests",
  recover: "/api/auth/recovery/requests",
};

export async function renderIdentity(path, params, ctx, memory, timing = {}) {
  const account = path === "/account", recover = path === "/account/recover", route = identityRoutes[path];
  if (!account && !recover && !route) return null;
  const session = () => ctx.sessionIdentity();
  const clock = timing.clock || (() => performance.now()), wallClock = timing.wallClock || (() => Date.now());
  const schedule = timing.schedule || ((callback, milliseconds) => setTimeout(callback, milliseconds));
  const cancel = timing.cancel || (handle => clearTimeout(handle));
  let context = session(), value = null, link = null, stage = "loading", failure = "";
  let linkDeadline = null, observedMono = null, observedWall = null;
  let record = memory.forPath(path);
  const initialCurrent = () => sameIdentityContext(context, session());
  async function inspect(signal) {
    const started = clock();
    const result = await ctx.api(route.inspection, { method: "POST", body: { token: record.token }, signal, ownerId: context.ownerId });
    if (!initialCurrent()) throw Object.assign(new Error(), { sessionChanged: true });
    if (result.link?.purpose !== route.purpose || !identityRemoteLinkStates.includes(result.link.state)) throw new Error("Invalid link view");
    const received = clock();
    const deadline = identityLinkDeadline(result.serverTime, result.link.expiresAt, started, received);
    observedMono = received; observedWall = wallClock();
    linkDeadline = result.link.state === "ready" ? deadline : null;
    return result.link;
  }
  const inspectedStage = () => link.state === "ready" && linkDeadline <= clock() ? "elapsed" : link.state;
  if (account && ctx.user) {
    value = await ctx.api(endpoints.profile, { ownerId: ctx.user.id });
    if (!initialCurrent() || value.profile.id !== ctx.user.id) throw Object.assign(new Error(), { status: 409, sessionChanged: true });
  } else if (route) {
    if (record?.completed) stage = "done";
    else if (!record?.token) stage = "invalid";
    else if (route.authenticated && !ctx.user) stage = "login";
    else {
      try { link = await inspect(); stage = inspectedStage(); }
      catch (error) { stage = error.status === 400 ? "invalid" : error.status === 401 ? "login" : "error"; failure = identityFailure(error); }
    }
  }
  const title = account ? "나의 계정" : recover ? "비밀번호 찾기" : route.title;
  const description = account ? "프로필과 이메일을 관리하고, 쌓아온 학습을 이어가세요." : recover ? "이메일로 계정을 확인하고 학습을 계속하세요." : "요청한 내용을 확인한 뒤 직접 완료해주세요.";
  const body = account ? value ? accountBody(value) : accountLoginBody() : recover ? recoveryBody() : linkBody(route, stage, link, failure, ctx.user);
  let dispose = () => {};
  return {
    html: identityShell(title, description, body, { compact: !account || !value, account }),
    bind() {
      const root = document.querySelector("[data-identity-page]"), content = root.querySelector("[data-id-body]");
      const capturedRoute = location.hash, abort = new AbortController(), intents = new Map(), deadlines = new Map();
      let disposed = false, busy = false, currentUser = ctx.user, timer = null;
      let expiryTimer = null, expiryDue = false, uncertainCompletion = false, wasHidden = document.visibilityState === "hidden";
      const present = () => !disposed && root.isConnected && location.hash === capturedRoute;
      const active = () => present() && sameIdentityContext(context, session());
      const paint = (html) => { content.innerHTML = html; };
      const resetInputs = () => root.querySelectorAll("form").forEach(form => form.reset());
      function stopExpiry() {
        if (expiryTimer !== null) cancel(expiryTimer);
        expiryTimer = null;
      }
      function needsRecheck() {
        if (!route || !Number.isFinite(linkDeadline)) return false;
        const elapsed = clock() - observedMono, wallElapsed = wallClock() - observedWall;
        return expiryDue || clock() >= linkDeadline || !Number.isFinite(wallElapsed) || Math.abs(wallElapsed - elapsed) > 2000;
      }
      function reconcileExpiry() {
        if (!active() || stage !== "ready" || !needsRecheck()) return;
        expiryDue = true;
        if (busy) return;
        stopExpiry(); resetInputs();
        if (uncertainCompletion && intents.has("complete")) stage = "uncertain";
        else { intents.delete("complete"); stage = "elapsed"; }
        paint(linkBody(route, stage, link)); updateDisabled();
        feedback("page", "최신 상태를 확인하거나 새로운 메일을 요청해주세요.");
      }
      function armExpiry() {
        stopExpiry(); expiryDue = false;
        if (!route || stage !== "ready" || !Number.isFinite(linkDeadline)) return;
        expiryTimer = schedule(() => {
          expiryTimer = null;
          if (!active() || stage !== "ready") return;
          if (clock() < linkDeadline) { armExpiry(); return; }
          expiryDue = true; reconcileExpiry();
        }, Math.min(2147483647, Math.max(0, linkDeadline - clock())));
      }
      function onReturn() { if (active()) reconcileExpiry(); }
      function onVisibility() {
        if (document.visibilityState === "hidden") { wasHidden = true; return; }
        if (wasHidden) { wasHidden = false; expiryDue = true; }
        onReturn();
      }
      function feedback(action, text, error = false) {
        const target = root.querySelector(`[data-id-message="${action}"]`);
        if (!target) return;
        target.textContent = text; target.classList.toggle("is-error", error);
        target.focus({ preventScroll: false });
      }
      function updateDisabled() {
        for (const fieldset of root.querySelectorAll("fieldset")) fieldset.disabled = busy;
        for (const button of root.querySelectorAll("button")) {
          const action = button.dataset.idRetry || button.closest("[data-id-form]")?.dataset.idForm;
          button.disabled = busy || (action && (deadlines.get(action) || 0) > Date.now());
        }
        root.setAttribute("aria-busy", String(busy));
      }
      function cooldown(action, seconds) {
        if (!Number.isFinite(seconds)) return;
        deadlines.set(action, Date.now() + Math.max(1, seconds) * 1000);
        if (!timer) timer = setInterval(() => {
          if (!active()) return;
          updateDisabled();
          if (![...deadlines.values()].some(t => t > Date.now())) { clearInterval(timer); timer = null; }
        }, 1000);
      }
      async function syncSession() {
        const result = await ctx.refreshIdentitySession(context);
        if (!present() || !result.applied) return false;
        context = session(); currentUser = result.user;
        return true;
      }
      async function refresh() {
        if (!active() || busy || (route && uncertainCompletion)) return;
        if (route) { stopExpiry(); failure = ""; }
        busy = true; updateDisabled();
        try {
          if (account) {
            const result = await ctx.api(endpoints.profile, { ownerId: context.ownerId, signal: abort.signal });
            if (!active()) return;
            value = result; intents.clear(); paint(accountBody(value)); feedback("page", "현재 계정 상태를 확인했습니다.");
          } else {
            link = await inspect(abort.signal);
            if (!active()) return;
            stage = inspectedStage(); paint(linkBody(route, stage, link)); armExpiry();
          }
        } catch (error) {
          if (active()) {
            if (route) {
              stage = error.status === 400 ? "invalid" : error.status === 401 ? "login" : "error";
              paint(linkBody(route, stage, null, identityFailure(error)));
            } else feedback("page", identityFailure(error), true);
          }
        }
        finally { if (active()) { busy = false; reconcileExpiry(); updateDisabled(); } }
      }
      async function execute(intent) {
        const action = intent.action;
        if (!active() || busy) return;
        const originalUncertain = action === "complete" && uncertainCompletion && intents.get(action) === intent;
        if (action === "complete" && !originalUncertain && (stage !== "ready" || needsRecheck())) { reconcileExpiry(); return; }
        if ((deadlines.get(action) || 0) > Date.now()) return;
        busy = true; updateDisabled(); feedback(action, "처리하고 있습니다. 잠시 기다려주세요.");
        try {
          const result = await ctx.api(action === "complete" ? route.completion : endpoints[action], {
            method: action === "profile" ? "PATCH" : "POST", body: intent.body,
            ownerId: context.ownerId, signal: abort.signal,
          });
          if (!active()) return;
          if (action === "complete") {
            stopExpiry(); uncertainCompletion = false;
            memory.complete(path, result); record = memory.forPath(path); stage = "done";
            intents.clear(); resetInputs();
            try {
              if (!(await syncSession())) return;
              memory.complete(path, result); record = memory.forPath(path);
              paint(linkBody(route, stage, null, "", currentUser));
            } catch (sessionError) {
              if (active()) paint(linkBody(route, stage, null, "", null, true));
            }
          } else if (action === "recover") {
            intents.clear(); paint(recoveryBody(true)); feedback("recover", "요청을 접수했습니다. 메일을 확인하고 가장 최근 링크를 열어주세요.");
          } else {
            const fresh = action === "profile" ? result : await ctx.api(endpoints.profile, { ownerId: context.ownerId, signal: abort.signal });
            if (!active()) return;
            if (action === "profile" && !(await syncSession())) return;
            if (!currentUser || fresh.profile.id !== currentUser.id) { value = null; intents.clear(); paint(accountLoginBody()); return; }
            value = fresh; intents.clear(); paint(accountBody(value));
            feedback(action, action === "profile" ? "프로필을 저장했습니다. 현재 저장된 이름을 표시합니다." : "요청을 접수했습니다. 메일 도착과 인증 완료는 별도로 확인해주세요.");
          }
        } catch (error) {
          if (!active()) return;
          if (action === "complete") {
            stopExpiry(); resetInputs();
            if (error.sessionChanged || [401, 403].includes(error.status)) {
              intents.delete(action); uncertainCompletion = false;
              stage = error.status === 401 ? "login" : "error";
              paint(linkBody(route, stage, null, identityFailure(error)));
            } else if (!error.status || error.status >= 500 || (originalUncertain && error.status === 429)) {
              uncertainCompletion = true; stage = "uncertain";
              paint(linkBody(route, stage, link)); feedback("page", identityFailure(error), true);
              if (error.status === 429) cooldown(action, error.retryAfterSeconds);
            } else {
              intents.delete(action); uncertainCompletion = false;
              if ([400, 409].includes(error.status)) {
                failure = "";
                try {
                  link = await inspect(abort.signal);
                  if (!active()) return;
                  stage = inspectedStage();
                } catch (inspectionError) {
                  if (!active()) return;
                  stage = inspectionError.status === 400 ? "invalid" : inspectionError.status === 401 ? "login" : "error";
                  failure = identityFailure(inspectionError);
                }
              } else stage = needsRecheck() ? "elapsed" : "ready";
              paint(linkBody(route, stage, link, failure)); armExpiry();
              feedback(stage === "ready" ? "complete" : "page", identityFailure(error), true);
              if (error.status === 429) cooldown(action, error.retryAfterSeconds);
            }
          } else if (action === "profile" && error.status === 409 && !error.sessionChanged) {
            try {
              const fresh = await ctx.api(endpoints.profile, { ownerId: context.ownerId, signal: abort.signal });
              if (!active()) return;
              value = fresh; intents.clear(); paint(accountBody(value, intent.body.name));
              feedback(action, `현재 저장된 이름은 ‘${value.profile.name}’입니다. 입력한 이름과 비교한 뒤 직접 다시 저장해주세요.`, true);
            } catch (freshError) { if (active()) feedback(action, identityFailure(freshError), true); }
          } else {
            feedback(action, identityFailure(error), true);
            if (error.status === 429) cooldown(action, error.retryAfterSeconds);
            const button = root.querySelector(`[data-id-retry="${action}"]`);
            if (button) button.hidden = Boolean(error.sessionChanged || (error.status && error.status < 500 && error.status !== 429));
          }
        } finally { if (active()) { busy = false; reconcileExpiry(); updateDisabled(); } }
      }
      function submit(event) {
        const form = event.target.closest("[data-id-form]");
        if (!form) return;
        event.preventDefault();
        if (!active() || busy || !form.reportValidity()) return;
        const action = form.dataset.idForm;
        if (action === "complete" && (stage !== "ready" || needsRecheck())) { reconcileExpiry(); return; }
        const fields = Object.fromEntries(new FormData(form));
        let values;
        if (action === "profile") values = { expectedVersion: value.profile.version, name: fields.name };
        else if (action === "verify") values = { identityVersion: value.identity.identityVersion };
        else if (action === "change") values = { identityVersion: value.identity.identityVersion, candidateEmail: fields.candidateEmail, currentPassword: fields.currentPassword };
        else if (action === "recover") values = { email: fields.email };
        else {
          if (route.purpose === "reset_password" && fields.newPassword !== fields.confirmation) { feedback("complete", "새 비밀번호와 확인 입력이 일치하지 않습니다.", true); return; }
          values = { token: record?.token, ...(route.purpose === "reset_password" ? { newPassword: fields.newPassword } : {}) };
          uncertainCompletion = false;
        }
        const intent = newIdentityIntent(action, values); intents.set(action, intent); execute(intent);
      }
      function click(event) {
        const button = event.target.closest("button");
        if (!button || !active()) return;
        if (button.hasAttribute("data-id-login")) {
          if (session().ownerId) ctx.navigate("/account"); else ctx.requireAuth();
        } else if (button.hasAttribute("data-id-session-check")) {
          if (busy) return;
          busy = true; updateDisabled();
          syncSession().then(applied => {
            if (!applied || !active()) return;
            memory.complete(path, { requiresLogin: route.purpose !== "verify_email" }); record = memory.forPath(path);
            paint(linkBody(route, "done", null, "", currentUser));
          }).catch(error => { if (active()) feedback("page", identityFailure(error), true); })
            .finally(() => { if (active()) { busy = false; updateDisabled(); } });
        } else if (button.hasAttribute("data-id-refresh")) refresh();
        else if (button.dataset.idRetry) { const intent = intents.get(button.dataset.idRetry); if (intent) execute(intent); }
      }
      function input(event) {
        const form = event.target.closest("[data-id-form]");
        if (!form || busy) return;
        intents.delete(form.dataset.idForm); const button = form.querySelector("[data-id-retry]"); if (button) button.hidden = true;
      }
      root.addEventListener("submit", submit); root.addEventListener("click", click); root.addEventListener("input", input);
      document.addEventListener("visibilitychange", onVisibility); window.addEventListener("focus", onReturn);
      armExpiry();
      dispose = () => {
        disposed = true; abort.abort(); stopExpiry(); intents.clear(); deadlines.clear(); if (timer) clearInterval(timer);
        root.querySelectorAll("form").forEach(form => form.reset()); value = null; record = null;
        root.removeEventListener("submit", submit); root.removeEventListener("click", click); root.removeEventListener("input", input);
        document.removeEventListener("visibilitychange", onVisibility); window.removeEventListener("focus", onReturn);
      };
    },
    dispose() { dispose(); },
  };
}
