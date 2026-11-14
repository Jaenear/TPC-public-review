/** Temporary, account-bound drafts. No browser storage or automatic requests. */
const MAX_CHARS = 2000000;
const sensitive =
  /password|passwd|token|secret|payment|cardnumber|cvv|cvc|otp|email|consent|agreement|approval|accepted/i;
const allowedTypes = new Set([
  "text",
  "search",
  "url",
  "tel",
  "number",
  "date",
  "time",
  "datetime-local",
  "checkbox",
  "radio",
  "range",
  "color",
  "select-one",
  "select-multiple",
  "textarea",
]);

export function shouldRecoverSession(failure, current) {
  return (
    failure.status === 401 &&
    !failure.path.startsWith("/api/auth/") &&
    Boolean(failure.context?.ownerId) &&
    failure.context.ownerId === current?.ownerId &&
    failure.context.epoch === current.epoch
  );
}

export function recoveryCopy(packet) {
  const retained = packet?.snapshots?.some((snapshot) =>
    snapshot.entries?.some((entry) => entry.dirty),
  );
  return retained
    ? {
        notice: "작성 중인 내용은 이 탭에 보관합니다. 같은 계정으로 로그인한 뒤 확인하고 저장해주세요.",
        description: "같은 계정으로 로그인하면 작성 내용을 확인하고 이어갈 수 있습니다. 보관한 내용은 이 탭에서만 사용합니다.",
        afterLogin: "다시 로그인했습니다. 작성 내용을 확인해주세요.",
      }
    : {
        notice: "다시 로그인하면 저장된 기록을 불러옵니다. 저장하지 않은 입력은 다시 작성하고, 파일은 다시 선택해주세요.",
        description: "같은 계정으로 로그인해 저장된 기록을 확인해주세요. 저장하지 않은 입력은 다시 작성하고, 파일은 다시 선택해야 합니다.",
        afterLogin: "다시 로그인했습니다. 저장된 기록을 불러왔습니다. 저장하지 않은 입력과 파일은 다시 확인해주세요.",
      };
}

export function safeDraftEntry(entry) {
  if (
    !entry ||
    typeof entry.key !== "string" ||
    sensitive.test(entry.key) ||
    !allowedTypes.has(entry.type) ||
    typeof entry.value !== "string"
  )
    return null;
  const value = entry.value.slice(0, MAX_CHARS);
  return {
    key: entry.key.slice(0, 120),
    index: Number.isInteger(entry.index) ? entry.index : 0,
    type: entry.type,
    value,
    checked: Boolean(entry.checked),
    dirty: Boolean(entry.dirty),
    label: String(entry.label || entry.key).slice(0, 100),
    custom: Boolean(entry.custom),
    ...(Array.isArray(entry.values)
      ? {
          values: entry.values
            .filter((v) => typeof v === "string")
            .slice(0, 200),
        }
      : {}),
    ...(typeof entry.displayValue === "string"
      ? { displayValue: entry.displayValue.slice(0, MAX_CHARS) }
      : {}),
  };
}

function sanitizedSnapshots(snapshots) {
  let remaining = MAX_CHARS;
  return snapshots
    .slice(0, 20)
    .map((snapshot) => {
      const entries = snapshot.entries
        .slice(0, 500)
        .map(safeDraftEntry)
        .filter(Boolean)
        .filter((entry) => {
          remaining -= entry.value.length + (entry.displayValue?.length || 0);
          return remaining >= 0;
        });
      return {
        id: snapshot.id,
        auto: Boolean(snapshot.auto),
        version: String(snapshot.version || ""),
        entries,
        omittedFiles: Boolean(snapshot.omittedFiles),
        ...(snapshot.requiresAccess
          ? {
              requiresAccess: true,
              accessFormId: String(snapshot.accessFormId || snapshot.id).slice(
                0,
                200,
              ),
            }
          : {}),
      };
    })
    .filter(
      (snapshot) =>
        snapshot.entries.some((entry) => entry.dirty) || snapshot.omittedFiles,
    );
}

export class SessionDrafts {
  constructor({ clock = () => Date.now(), ttl = 15 * 60 * 1000 } = {}) {
    this.clock = clock;
    this.ttl = ttl;
    this.pending = null;
  }
  clear() {
    this.pending = null;
  }
  peek(route) {
    if (
      this.pending &&
      (this.clock() >= this.pending.expiresAt || this.pending.route !== route)
    )
      this.clear();
    return this.pending;
  }
  capture(ownerId, route, snapshots) {
    if (!ownerId || !route.startsWith("#/")) return null;
    const previous = this.peek(route);
    const incoming = sanitizedSnapshots(snapshots);
    const byId = new Map(
      (previous?.ownerId === ownerId ? previous.snapshots : []).map((s) => [
        s.id,
        s,
      ]),
    );
    for (const snapshot of snapshots) byId.delete(snapshot.id);
    for (const snapshot of incoming) byId.set(snapshot.id, snapshot);
    this.pending = {
      ownerId,
      route,
      snapshots: [...byId.values()],
      expiresAt: this.clock() + this.ttl,
    };
    return this.pending;
  }
  take(ownerId, route) {
    const pending = this.peek(route);
    this.clear();
    if (!pending) return { reason: "unavailable", snapshots: [] };
    if (pending.ownerId !== ownerId)
      return { reason: "different-account", snapshots: [] };
    return { reason: "same-account", snapshots: pending.snapshots };
  }
}

function fieldKey(control) {
  return control.name || control.id || control.dataset.draftKey;
}
function controls(form) {
  return [...form.elements].filter((control) => {
    const key = fieldKey(control);
    return (
      key &&
      !sensitive.test(key) &&
      allowedTypes.has(control.type) &&
      !control.matches(":disabled") &&
      !control.readOnly &&
      !/(?:^|\s)(?:cc-|one-time-code|current-password|new-password)/i.test(
        control.autocomplete || "",
      )
    );
  });
}
function fieldLabel(control) {
  const label = control.labels?.[0] || control.closest("label");
  return (
    control.getAttribute("aria-label") ||
    [...(label?.childNodes || [])]
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent)
      .join(" ")
      .trim() ||
    fieldKey(control)
  );
}
function defaultValues(control) {
  const options = [...control.options],
    chosen = options.filter((option) => option.defaultSelected);
  return (
    chosen.length ? chosen : control.multiple ? [] : options.slice(0, 1)
  ).map((option) => option.value);
}
export function captureDraftForms(root = document) {
  const forms = [
    ...root.querySelectorAll("#main-content form, #app-dialog form"),
  ].filter(
    (form) =>
      form.id !== "auth-form" && !form.hasAttribute("data-no-session-draft"),
  );
  return forms.map((form, number) => {
    const counts = new Map();
    const entries = controls(form).map((control) => {
      const key = fieldKey(control),
        index = counts.get(key) || 0;
      counts.set(key, index + 1);
      const value = control.value,
        type = control.type;
      const chosen = type.startsWith("select")
        ? [...control.selectedOptions].map((o) => o.value)
        : [];
      return {
        key,
        index,
        type,
        value,
        checked: control.checked,
        label: fieldLabel(control),
        dirty:
          type === "checkbox" || type === "radio"
            ? control.checked !== control.defaultChecked
            : type.startsWith("select")
              ? JSON.stringify(chosen) !==
                JSON.stringify(defaultValues(control))
              : value !== control.defaultValue,
        ...(control.multiple ? { values: chosen } : {}),
        ...(type.startsWith("select")
          ? {
              displayValue: [...control.selectedOptions]
                .map((option) => option.textContent.trim())
                .join(", "),
            }
          : {}),
      };
    });
    const snapshot = {
      id: form.id || "dialog-form-" + number,
      auto:
        form.hasAttribute("data-session-draft") &&
        Boolean(form.id) &&
        !form.closest("#app-dialog"),
      version: form.getAttribute("data-recovery-version") || "",
      entries,
      omittedFiles: [...form.elements].some(
        (c) => c.type === "file" && c.files?.length,
      ),
      ...(form.hasAttribute("data-recovery-private")
        ? { requiresAccess: true, accessFormId: form.id }
        : {}),
    };
    form.dispatchEvent(
      new CustomEvent("tpc:capture-draft", { detail: snapshot }),
    );
    return snapshot;
  });
}

function lookupControls(form) {
  const found = new Map();
  for (const control of controls(form)) {
    const key = fieldKey(control);
    if (!found.has(key)) found.set(key, []);
    found.get(key).push(control);
  }
  return found;
}
export function restoreDraftForms(root, snapshots) {
  const restored = [],
    manual = [],
    discarded = [];
  for (const snapshot of snapshots) {
    const form = [...root.querySelectorAll("form[data-session-draft]")].find(
      (f) => f.id === snapshot.id,
    );
    const accessForm = [
      ...root.querySelectorAll("form[data-session-draft]"),
    ].find((f) => f.id === (snapshot.accessFormId || snapshot.id));
    if (
      snapshot.requiresAccess &&
      (!accessForm?.hasAttribute("data-recovery-private") ||
        !privateDraftContext(snapshot, accessForm))
    ) {
      discarded.push(snapshot.id);
      continue;
    }
    if (
      !snapshot.auto ||
      !form ||
      !snapshot.version ||
      form.getAttribute("data-recovery-version") !== snapshot.version
    ) {
      manual.push(snapshot);
      continue;
    }
    let fields = lookupControls(form);
    const contexts = (form.dataset.recoveryContext || "")
      .split(/\s+/)
      .filter(Boolean);
    const contextValid = contexts.every((key) => {
      const entry = snapshot.entries.find((e) => e.key === key);
      const control = fields.get(key)?.[0];
      return (
        !entry ||
        Boolean(
          control &&
            [...(control.options || [])].some((o) => o.value === entry.value),
        )
      );
    });
    if (!contextValid) {
      if (snapshot.requiresAccess) discarded.push(snapshot.id);
      else manual.push(snapshot);
      continue;
    }
    form.dispatchEvent(
      new CustomEvent("tpc:prepare-draft", { detail: snapshot }),
    );
    fields = lookupControls(form);
    const ordinary = snapshot.entries.filter((entry) => !entry.custom);
    const valid = ordinary.every((entry) => {
      const control = fields.get(entry.key)?.[entry.index];
      if (!control || control.type !== entry.type) return false;
      return (
        !entry.type.startsWith("select") ||
        (entry.values || [entry.value]).every((value) =>
          [...control.options].some((o) => o.value === value),
        )
      );
    });
    if (!valid) {
      form.reset();
      form.dispatchEvent(new CustomEvent("tpc:reset-draft"));
      manual.push(snapshot);
      continue;
    }
    for (const entry of ordinary) {
      const control = fields.get(entry.key)[entry.index];
      if (entry.type === "checkbox" || entry.type === "radio")
        control.checked = entry.checked;
      else if (entry.values)
        [...control.options].forEach((o) => {
          o.selected = entry.values.includes(o.value);
        });
      else control.value = entry.value;
    }
    form.dispatchEvent(
      new CustomEvent("tpc:draft-restored", { detail: snapshot }),
    );
    restored.push(snapshot);
  }
  return { restored, manual, discarded };
}

export function privateDraftContext(snapshot, form) {
  const fields = lookupControls(form);
  return (form.dataset.recoveryContext || "")
    .split(/\s+/)
    .filter(Boolean)
    .every((key) => {
      const entry = snapshot.entries.find((e) => e.key === key),
        control = fields.get(key)?.[0];
      return (
        !entry ||
        Boolean(
          control &&
            [...(control.options || [])].some(
              (option) => option.value === entry.value,
            ),
        )
      );
    });
}

export function draftText(snapshot) {
  return snapshot.entries
    .filter((entry) => entry.dirty)
    .map(
      (entry) =>
        `${entry.label}\n${entry.displayValue ?? (entry.type === "checkbox" || entry.type === "radio" ? (entry.checked ? "선택" : "해제") : (entry.values?.join(", ") ?? entry.value))}`,
    )
    .join("\n\n");
}
