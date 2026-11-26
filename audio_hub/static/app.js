(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    csrf: $('meta[name="csrf-token"]').content,
    products: [],
    categories: [],
    devices: [],
    deviceRequest: 0,
    deviceListSignature: "",
    devicesLoaded: false,
    selectedDevice: null,
    visibleDevices: [],
    factory: null,
    view: "devices",
    audio: { product: "", category: "animal", library: null, request: 0, uploading: false },
    voice: { keys: [], devices: [], limit: 5, snapshot: null, run: null, request: 0 },
    queriedFlashIp: "",
  };
  const titles = {
    devices: "设备工作台",
    audio: "音频库",
    voice: "Key 库",
    system: "维护设置",
  };
  const keyStates = { applied: "已写入", pending: "待确认", clearing: "待清除" };
  const fileLimit = 32 * 1024 * 1024;
  const buttonStates = new WeakMap();
  let viewLoad = 0;
  let catalogRequest = null;

  function icon(id) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.appendChild(use);
    return svg;
  }

  function node(tag, value, className = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== null) element.textContent = value;
    return element;
  }

  function action(label, handler, variant = "ghost") {
    const button = node("button", label, `button button--${variant} button--small`);
    button.type = "button";
    button.addEventListener("click", () => handler(button));
    return button;
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    const units = ["KB", "MB", "GB"];
    let size = value / 1024;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[index]}`;
  }

  const shortDate = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });

  function formatTime(value) {
    if (!value) return "从未上报";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "—";
    const delta = Date.now() - date.valueOf();
    if (delta < 60_000) return "刚刚";
    if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`;
    if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`;
    return shortDate.format(date);
  }

  function productById(id) {
    return state.products.find((product) => product.id === id) || {
      name: id, short_name: id, emoji: "⌁", color: "#71827b",
    };
  }

  async function api(url, { timeout, ...options } = {}) {
    const settings = { cache: "no-store", ...options };
    const reading = ["GET", "HEAD"].includes(settings.method || "GET");
    settings.headers = new Headers(settings.headers);
    settings.headers.set("Accept", "application/json");
    if (!reading) settings.headers.set("X-CSRF-Token", state.csrf);
    if (settings.body && !(settings.body instanceof FormData)) {
      settings.headers.set("Content-Type", "application/json");
      settings.body = JSON.stringify(settings.body);
    }

    const controller = new AbortController();
    settings.signal = controller.signal;
    const timer = setTimeout(() => controller.abort(), timeout ?? (reading ? 12_000 : 30_000));
    try {
      const response = await fetch(url, settings);
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401 && payload.code === "authentication_required") {
        window.location.replace("/login");
        throw new Error("登录已过期");
      }
      if (!response.ok) throw new Error(payload.error || `服务端返回 HTTP ${response.status}`);
      return payload;
    } catch (error) {
      if (controller.signal.aborted) throw new Error("请求超时，请刷新状态后再试");
      if (error instanceof TypeError) throw new Error("连接失败，请检查局域网和 Audio Hub");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function operationStatus(target, text, tone = "") {
    const element = typeof target === "string" ? document.getElementById(target) : target;
    element.textContent = text;
    element.hidden = !text;
    if (tone) element.dataset.tone = tone;
    else delete element.dataset.tone;
  }

  function revealIfClipped(element) {
    const rect = element.getBoundingClientRect();
    if (!rect.height) return;
    const dialog = element.closest("dialog");
    const frame = dialog?.getBoundingClientRect();
    const top = frame ? frame.top + 85 : 108;
    const bottom = frame ? frame.bottom - 12 : window.innerHeight - 16;
    if (rect.top < top || rect.bottom > bottom) element.scrollIntoView({ block: "center" });
  }

  function feedback(id, title, message = "", tone = "success", reveal = true) {
    const region = document.getElementById(id);
    const item = node("div", null, "feedback");
    item.dataset.tone = tone;
    const copy = node("div", null);
    copy.append(node("strong", title));
    if (message) copy.append(node("p", message));
    const close = node("button", "×", "icon-button");
    close.type = "button";
    close.setAttribute("aria-label", "关闭通知");
    close.addEventListener("click", () => item.remove());
    item.append(copy, close);
    region.replaceChildren(item);
    if (reveal) revealIfClipped(item);
  }

  function setLoading(button, loading, label = "处理中…") {
    if (loading) {
      if (buttonStates.has(button)) return;
      buttonStates.set(button, { children: [...button.childNodes], disabled: button.disabled });
      button.textContent = label;
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
    } else {
      const previous = buttonStates.get(button);
      if (!previous) return;
      button.replaceChildren(...previous.children);
      button.disabled = previous.disabled;
      button.removeAttribute("aria-busy");
      buttonStates.delete(button);
    }
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (!dialog || dialog.open) return;
    const title = dialog.querySelector("h2");
    if (title) title.tabIndex = -1;
    dialog.showModal();
    dialog.scrollTop = 0;
    title?.focus({ preventScroll: true });
  }

  function closeDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog?.open) dialog.close();
  }

  function decide({ title, message = "", label = "确认", field = "", value = "", code = "", danger = false }) {
    const dialog = $("#decision-modal");
    if (dialog.open) return Promise.resolve(null);
    dialog.returnValue = "";
    $("#decision-title").textContent = title;
    const detail = $("#decision-message");
    detail.textContent = message;
    detail.hidden = !message;
    const input = $("#decision-value");
    $("#decision-field").hidden = !field;
    $("#decision-label").textContent = field;
    input.value = value;
    input.required = Boolean(field);
    const submit = $("#decision-submit");
    submit.textContent = label;
    submit.className = `button ${danger ? "button--danger" : "button--primary"}`;
    submit.disabled = Boolean(code);
    input.oninput = () => {
      submit.disabled = Boolean(code) && input.value.trim() !== code;
      input.setCustomValidity(!code && new TextEncoder().encode(input.value.trim()).length > 48
        ? "名称最多 48 个 UTF-8 字节" : "");
    };
    input.oninput();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => {
        const result = dialog.returnValue === "confirm" ? (field ? input.value.trim() : true) : null;
        input.value = "";
        resolve(result);
      }, { once: true });
      dialog.showModal();
      if (field) input.focus();
      else $(".button--ghost", dialog).focus();
    });
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
    try {
      localStorage.setItem("audio-hub-theme", document.documentElement.dataset.theme);
    } catch {
      // Theme still works for this page when storage is unavailable.
    }
  }

  function navigate(view, replaceHistory = false) {
    if (!Object.hasOwn(titles, view)) view = "devices";
    const changed = state.view !== view;
    if (changed) {
      $("#page-feedback").replaceChildren();
      $("#page-load").hidden = true;
      viewLoad += 1;
    }
    if (state.view === "audio" && view !== "audio") stopAudio();
    state.view = view;
    for (const panel of $$("[data-view-panel]")) {
      panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
    }
    for (const button of $$("[data-view]")) {
      const current = button.dataset.view === view;
      button.classList.toggle("is-active", current);
      if (current) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    $("#page-title").textContent = titles[view];
    document.title = `${titles[view]} · Audio Hub`;
    $("#refresh-button").hidden = view === "system";
    const fragment = `#${view}`;
    if (location.hash !== fragment) {
      if (replaceHistory) history.replaceState(null, "", fragment);
      else history.pushState(null, "", fragment);
    }
    if (changed) window.scrollTo(0, 0);
    return refreshView(view);
  }

  async function refreshView(view = state.view, quiet = false) {
    const request = !quiet && view === state.view && view !== "system" ? ++viewLoad : 0;
    if (request) operationStatus("page-load", "正在加载…");
    try {
      await loadCatalog();
      if (view === "devices") await loadDevices();
      if (view === "audio") await loadAudio();
      if (view === "voice") await loadVoiceDashboard();
      return true;
    } catch (error) {
      if (view === "devices" && !state.devicesLoaded) $("#device-empty").textContent = error.message;
      if (!quiet && view === state.view) feedback("page-feedback", "加载失败", error.message, "error", false);
      return false;
    } finally {
      if (request && request === viewLoad) operationStatus("page-load", "");
    }
  }

  function statusBadge(device) {
    if (device.status === "pending") return node("span", "待激活", "badge badge--waiting");
    if (device.status === "disabled") return node("span", "已停用", "badge badge--quiet");
    return device.online ? node("span", "近期上报", "badge badge--ready") : null;
  }

  async function loadDevices() {
    const request = ++state.deviceRequest;
    const data = await api("/api/admin/devices");
    if (request !== state.deviceRequest) return;
    const signature = JSON.stringify(data.devices.map(device => [device, formatTime(device.last_seen_at)]));
    if (state.devicesLoaded && signature === state.deviceListSignature) return;
    state.deviceListSignature = signature;
    state.devices = data.devices;
    state.devicesLoaded = true;
    renderDevices();
  }

  function renderDevices() {
    const query = $("#device-search").value.trim().toLowerCase();
    const productFilter = $("#device-product-filter").value;
    const statusFilter = $("#device-status-filter").value;
    const devices = state.devices.filter((device) => {
      const terms = `${device.name} ${device.device_uid} ${device.ip_address || ""}`.toLowerCase();
      if (!terms.includes(query) || (productFilter && device.product_id !== productFilter)) return false;
      if (statusFilter === "online") return device.online;
      if (statusFilter === "offline") return device.status === "active" && !device.online;
      if (statusFilter === "key") return device.voice_key?.state === "applied";
      if (statusFilter === "unkeyed") return device.status === "active" && device.voice_key?.state !== "applied";
      return !statusFilter || device.status === statusFilter;
    });

    state.visibleDevices = devices;
    renderFactoryNavigation();
    $("#device-count").textContent = !state.devicesLoaded ? "—" : `${devices.length} 台`;
    const empty = $("#device-empty");
    empty.hidden = devices.length > 0;
    if (state.devicesLoaded) empty.textContent = state.devices.length
      ? "没有匹配的设备。试试其他关键词或筛选条件。"
      : "正在等待同一 Wi-Fi 的设备自动接入。";
    const body = $("#device-list");
    body.replaceChildren();

    for (const device of devices) {
      const product = productById(device.product_id);
      const row = body.insertRow();
      const idCell = row.insertCell();
      const identity = node("div", null, "device-identity");
      const avatar = node("span", product.emoji, "device-avatar");
      avatar.style.setProperty("--device-color", product.color);
      avatar.setAttribute("aria-hidden", "true");
      const text = node("div", null, "device-name");
      const button = node("button", device.name, "name-link");
      button.type = "button";
      button.addEventListener("click", () => openDevice(device));
      text.append(button, node("small", product.name));
      identity.append(avatar, text);
      idCell.append(identity);

      const network = row.insertCell();
      network.className = "device-network";
      const reporting = node("div", null, "reporting");
      reporting.append(node("span", formatTime(device.last_seen_at)));
      const badge = statusBadge(device);
      if (badge) reporting.append(badge);
      network.append(reporting, node("small", device.ip_address || "尚无局域网地址", "device-ip"));

      const key = row.insertCell();
      key.className = "device-key";
      if (device.voice_key) {
        key.append(node("strong", device.voice_key.label), node("small", keyStates[device.voice_key.state] || "待确认"));
      } else {
        key.append(node("span", "未分配", "muted"));
      }

      const operation = row.insertCell();
      operation.className = "device-actions";
      operation.append(action("配置", () => openDevice(device), "primary"));
    }
  }

  function populateProductSelects() {
    for (const id of ["device-product-filter", "add-device-product", "edit-device-product"]) {
      const select = document.getElementById(id);
      for (const product of state.products) select.add(new Option(product.name, product.id));
    }
  }

  function openDevice(device) {
    state.selectedDevice = device;
    const form = $("#device-form");
    $("#device-modal-title").textContent = device.name;
    form.elements.product_id.value = device.product_id;
    form.elements.status.value = device.status;
    form.elements.status.disabled = device.status === "pending";
    $("#device-advanced").open = false;
    const link = $("#open-device-developer");
    link.hidden = !device.developer_url;
    if (device.developer_url) link.href = device.developer_url;
    else link.removeAttribute("href");
    const fields = [
      ["产品型号", productById(device.product_id).name],
      ["设备 ID", device.device_uid],
      ["局域网 IP", device.ip_address || "—"],
      ["最近上报", formatTime(device.last_seen_at)],
      ["固件", device.firmware_version || "—"],
      ["电量", device.battery_level == null ? "—" : `${device.battery_level}%`],
    ];
    const strip = $("#device-detail-strip");
    strip.replaceChildren();
    for (const [label, value] of fields) {
      const item = node("div", null);
      item.append(node("small", label), node("strong", value));
      strip.append(item);
    }
    const hint = $("#device-developer-hint");
    hint.hidden = Boolean(device.developer_url);
    if (device.status === "pending") {
      hint.textContent = device.activation_code
        ? `激活码 ${device.activation_code}。请在设备列表完成绑定。` : "设备待激活。";
    } else if (device.status === "disabled") {
      hint.textContent = "设备已停用，可在「设备信息与令牌」中启用。";
    } else {
      hint.textContent = "等设备连上工厂 Wi-Fi，局域网地址会自动更新。";
    }
    $("#factory-device-meta").textContent = [productById(device.product_id).name, device.ip_address,
      device.firmware_version].filter(Boolean).join(" · ");
    state.voice.snapshot = null;
    operationStatus("voice-operation-status", "先读取此设备的音色列表。");
    operationStatus("device-key-feedback", "");
    operationStatus("registration-feedback", "");
    $("#reload-device-keys").hidden = true;
    const factory = { device, busy: false, volume: null, audio: null, selected: new Set(), selectedKey: "" };
    state.factory = factory;
    $("#factory-volume-value").textContent = "—";
    operationStatus("factory-volume-status", "");
    operationStatus("factory-audio-status", "检查后可选择要写入的文件。");
    $("#factory-audio-count").textContent = "";
    openDialog("device-modal");
    $("#device-modal").scrollTop = 0;
    renderFactoryNavigation();
    renderFactoryAudio();
    renderFactoryActions();
    refreshDeviceKeys(factory);
    if (device.developer_url) {
      readFactoryVolume().then(() => {
        if (factoryActive(factory)) factoryAudioAction("status");
      });
    }
  }

  function showToken(token) {
    operationStatus("token-feedback", "");
    $("#device-token").textContent = token;
    openDialog("token-modal");
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const input = document.createElement("textarea");
    input.value = value;
    input.readOnly = true;
    input.className = "clipboard-buffer";
    $("#token-modal").append(input);
    input.select();
    try {
      if (!document.execCommand("copy")) throw new Error("请长按令牌复制");
    } finally {
      input.remove();
    }
  }

  function voiceDevice() {
    return state.voice.devices.find((device) => device.id === state.selectedDevice?.id);
  }

  function canReachVoiceDevice(device) {
    return Boolean(device?.developer_url && device.voice_identifiable);
  }

  async function loadVoiceDashboard() {
    const voice = state.voice;
    const request = ++voice.request;
    const data = await api("/api/admin/voice");
    if (request !== voice.request) return;
    voice.keys = data.keys;
    voice.devices = data.devices;
    voice.limit = data.key_limit;
    $("#voice-key-limit").textContent = `每个 Key 最多 ${voice.limit} 台`;
    $("#voice-key-empty").hidden = voice.keys.length > 0;
    const keyList = $("#voice-key-list");
    keyList.replaceChildren();
    for (const key of voice.keys) {
      const card = node("article", null, "key-card");
      const heading = node("div", null, "key-card-heading");
      const label = node("div", null);
      label.append(node("strong", key.label), node("small", `•••• ${key.suffix}`));
      heading.append(label, node("span", `${key.count} / ${voice.limit} 台`, "key-usage"));
      const meter = document.createElement("progress");
      meter.max = voice.limit;
      meter.value = key.count;
      meter.setAttribute("aria-label", `${key.label} 已占名额`);
      const assigned = voice.devices.filter((device) => device.key_id === key.id);
      const names = node("div", null, "assigned-devices");
      for (const device of assigned) {
        const link = node("button", device.name, "name-link");
        link.type = "button";
        link.title = keyStates[device.key_state] || "";
        link.addEventListener("click", () => openDevice(device));
        names.append(link);
      }
      const actions = node("div", null, "button-row");
      const status = node("p", "", "operation-status");
      status.setAttribute("role", "status");
      status.hidden = true;
      const remove = action("删除", async (button) => {
        if (!await decide({ title: "删除百炼 Key", message: key.label, label: "删除", danger: true })) return;
        setLoading(button, true);
        operationStatus(status, "正在删除 Key…", "pending");
        try {
          await api(`/api/admin/voice/keys/${key.id}`, { method: "DELETE" });
          feedback("key-library-feedback", "Key 已删除", key.label);
          await refreshView("voice");
        } catch (error) {
          operationStatus(status, `删除失败 · ${error.message}`, "error");
          revealIfClipped(status);
        } finally {
          setLoading(button, false);
        }
      }, "danger-ghost");
      remove.disabled = key.count > 0;
      remove.title = key.count > 0 ? "先从设备清除已分配的 Key" : "删除 Key";
      actions.append(action("检查 Key", async (button) => {
        setLoading(button, true, "检查中…");
        operationStatus(status, "正在检查百炼音色服务…", "pending");
        try {
          await api(`/api/admin/voice/keys/${key.id}/check`, { method: "POST" });
          operationStatus(status, "Key 可访问百炼音色服务。", "success");
        } catch (error) {
          operationStatus(status, error.message, "error");
        } finally {
          setLoading(button, false);
          revealIfClipped(status);
        }
      }), remove);
      card.append(heading, meter, names, actions, status);
      keyList.append(card);
    }
    if ($("#device-modal").open) renderVoiceDevice();
  }

  function renderVoiceDevice() {
    const device = voiceDevice();
    const voice = state.voice;
    const busy = Boolean(voice.run || state.factory?.busy || state.factory?.audio?.running);
    if (!device) {
      $("#voice-binding-status").textContent = "等待读取登记和 Key";
      $("#voice-key-choice").replaceChildren(new Option("等待读取 Key", ""));
      $("#assign-voice-key").disabled = true;
      $("#clear-voice-key").hidden = true;
      renderDeviceCatalog();
      return;
    }
    const reachable = canReachVoiceDevice(device);
    const binding = voice.keys.find((key) => key.id === device.key_id);
    const status = binding ? `${binding.label} · ${keyStates[device.key_state] || "待确认"}` : "未分配 Audio Hub Key";
    const hint = device.key_error || (!device.voice_identifiable ? "登记需要设备的完整 Wi-Fi MAC"
      : !reachable ? "等设备连上工厂 Wi-Fi 后自动刷新地址"
      : state.factory?.audio?.running ? "设备正在处理离线音频，结束后可写入 Key"
      : state.factory?.busy ? "正在读取或设置设备，请稍候"
      : device.key_syncing ? "设备正在同步 Key" : "");
    $("#voice-binding-status").textContent = [status, hint].filter(Boolean).join("。");

    const choice = $("#voice-key-choice");
    const selected = state.factory?.selectedKey || "";
    choice.replaceChildren(new Option(voice.keys.length ? "请选择 Key" : "请先添加 Key", ""));
    for (const key of binding ? [binding] : voice.keys) {
      const option = new Option(`${key.label} · ${key.count}/${voice.limit} 台`, key.id);
      option.disabled = !binding && key.count >= voice.limit;
      choice.add(option);
    }
    const available = [...choice.options].some((option) => option.value === selected && !option.disabled);
    choice.value = binding ? String(binding.id) : available ? selected : "";
    choice.disabled = Boolean(busy || binding || !reachable);
    const assign = $("#assign-voice-key");
    assign.textContent = binding ? "重新写入 Key" : "写入 Key";
    assign.disabled = Boolean(busy || device.key_syncing || !reachable || !choice.value ||
      choice.selectedOptions[0]?.disabled);
    const clear = $("#clear-voice-key");
    clear.hidden = !binding;
    clear.disabled = Boolean(busy || device.key_syncing || !reachable);
    renderDeviceCatalog();
  }

  function renderDeviceCatalog() {
    const { snapshot, run } = state.voice;
    const busy = Boolean(run || state.factory?.busy || state.factory?.audio?.running) || !canReachVoiceDevice(voiceDevice());
    $("#read-device-voices").disabled = busy;
    $("#restore-device-voice").disabled = busy || !snapshot || snapshot.current_voice === "xiaozhi-native";
    $("#record-device-voice").disabled = busy || !snapshot?.has_key;
    $("#preview-device-voice").disabled = busy || !snapshot?.has_key || snapshot.current_voice === "xiaozhi-native";
    $("#device-voice-table").hidden = !snapshot?.voices.length;
    const list = $("#device-voice-list");
    list.replaceChildren();
    for (const voice of snapshot?.voices || []) {
      const row = list.insertRow();
      row.insertCell().textContent = voice.name;
      row.insertCell().textContent = voice.current ? "使用中" : voice.builtin ? "内置" : "复刻";
      const actions = node("div", null, "button-row");
      const select = action("使用", () => runVoiceAction("select", voice.id));
      select.disabled = busy || voice.current;
      actions.append(select);
      if (!voice.builtin) {
        const rename = action("重命名", async () => {
          const name = await decide({ title: "重命名音色", field: "音色名称", value: voice.name, label: "保存" });
          if (name) await runVoiceAction("rename", voice.id, name);
        });
        const remove = action("删除", async () => {
          if (await decide({ title: "删除云端音色", message: voice.name, label: "删除", danger: true })) {
            await runVoiceAction("delete", voice.id);
          }
        }, "danger-ghost");
        rename.disabled = busy;
        remove.disabled = busy || voice.current;
        actions.append(rename, remove);
      }
      row.insertCell().appendChild(actions);
    }
  }

  async function withVoiceDevice(message, task, statusId = "voice-operation-status") {
    const device = voiceDevice();
    const factory = state.factory;
    if (!factoryActive(factory) || factory.busy || factory.audio?.running ||
        !canReachVoiceDevice(device) || state.voice.run) return;
    const run = {};
    state.voice.run = run;
    const active = () => state.voice.run === run && factoryActive(factory);
    const say = (text, tone = "pending") => {
      if (!active()) return;
      operationStatus(statusId, text, tone);
      if (tone === "success" || tone === "error") revealIfClipped(document.getElementById(statusId));
    };
    const endpoint = `/api/admin/voice/devices/${device.id}`;
    say(message);
    renderFactoryActions();
    try {
      await task({ endpoint, active, say });
    } catch (error) {
      say(error.message, "error");
    } finally {
      if (state.voice.run === run) {
        state.voice.run = null;
        if ($("#device-modal").open) renderFactoryActions();
      }
    }
  }

  async function changeDeviceKey(clearing) {
    const keyId = Number($("#voice-key-choice").value);
    const device = voiceDevice();
    if (!device || (!clearing && !keyId)) return;
    if (clearing && !await decide({
      title: `清除 ${device.name} 的 Key`, message: "设备确认后释放一个名额。已有音色保留在云端。",
      label: "清除 Key", danger: true,
    })) return;
    await withVoiceDevice(clearing ? "正在清除设备 Key…" : "正在写入设备 Key…", async ({ endpoint, active, say }) => {
      try {
        await api(`${endpoint}/key`, { method: clearing ? "DELETE" : "PUT", body: { key_id: keyId } });
        say(clearing ? "设备已清除 Key，名额已释放。" : "Key 已写入设备，可以读取音色。", "success");
      } finally {
        if (active()) {
          state.voice.snapshot = null;
          await refreshView("voice");
          await refreshView("devices", true);
        }
      }
    }, "device-key-feedback");
  }

  async function runVoiceAction(action, voiceId, name) {
    await withVoiceDevice(action === "record" ? "请靠近设备，等待录音提示。" : "正在联系设备…", async ({ endpoint, active, say }) => {
      const send = (step) => api(`${endpoint}/catalog`, {
        method: "POST", body: { action: step, id: voiceId, name },
      });
      if (action === "list") {
        const snapshot = await send("status");
        if (!active()) return;
        state.voice.snapshot = snapshot;
        if (!snapshot.has_key) {
          say("请先选择 Key 并写入设备。", "");
          return;
        }
        if (snapshot.busy) {
          say("设备正在处理音色，请稍后再次读取。", "");
          return;
        }
      }
      await send(action);
      if (action === "record") await new Promise((resolve) => setTimeout(resolve, 1200));
      const limit = Date.now() + (action === "record" ? 150_000 : 35_000);
      const managementErrors = {
        config: "设备未配置可用 Key", network: "云端音色服务调用失败，可先检查 Key",
        full: "设备音色已满", save: "设备保存失败", invalid: "音色或名称无效", uncertain: "上次创建结果未确认，请刷新列表",
      };
      while (active() && Date.now() < limit) {
        const snapshot = await send("status");
        if (!active()) return;
        state.voice.snapshot = snapshot;
        if (action === "record") {
          const phase = snapshot.enrollment_status;
          if (phase === "success") {
            await send("list");
            action = "list";
            say("录音创建完成，正在刷新音色…");
          } else if (phase === "failed" || phase === "cancelled") {
            throw new Error(phase === "failed" ? "设备录音复刻失败，请检查语音服务或录音长度" : "设备已取消录音");
          } else {
            say({ prompt: "请听设备提示。", recording: "设备正在录音…", processing: "正在创建音色…" }[phase] || "正在准备设备录音…");
          }
        } else if (action === "preview") {
          if (snapshot.preview_status === "complete") { say("设备已播放试听。", "success"); return; }
          if (["failed", "cancelled"].includes(snapshot.preview_status)) throw new Error("设备未完成试听");
          say(snapshot.preview_status === "playing" ? "设备正在播放试听…" : "等待设备播放试听…");
        } else if (action === "original" || !snapshot.busy) {
          if (action !== "original" && snapshot.management_status !== "ok") {
            throw new Error(managementErrors[snapshot.management_status] || "设备未完成操作，请刷新音色列表");
          }
          const done = { original: "已切回原声。", select: "已切换设备音色。", rename: "音色名称已保存。", delete: "音色已删除。" };
          say(done[action] || `已读取 ${snapshot.voices.filter((voice) => !voice.builtin).length} 个复刻音色。`, "success");
          return;
        } else {
          say("正在同步设备音色…");
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
      say("设备仍在处理，可以稍后再次读取。", "");
    });
  }


  function audioTarget() {
    const { product, category } = state.audio;
    return { product, category };
  }

  function sameAudioTarget(target) {
    return target?.product === state.audio.product && target.category === state.audio.category;
  }

  function audioTargetLabel(target) {
    const category = state.categories.find((item) => item.id === target.category);
    return `${productById(target.product).name} / ${category?.name || target.category}`;
  }

  function setAudioTarget(product, category) {
    if (product === state.audio.product && category === state.audio.category) return;
    state.audio.product = product;
    state.audio.category = category;
    state.audio.library = null;
    $("#audio-search").value = "";
    stopAudio();
    renderAudioSelectors();
    renderAudioFiles();
  }

  function openProductAudio(product) {
    closeDialog("device-modal");
    setAudioTarget(product, "animal");
    return navigate("audio");
  }

  function renderAudioSelectors() {
    const { product, category } = audioTarget();
    const buttons = $("#audio-products");
    buttons.replaceChildren();
    for (const item of state.products) {
      const button = node("button", null, "product-choice");
      button.type = "button";
      button.setAttribute("aria-pressed", String(product === item.id));
      button.style.setProperty("--device-color", item.color);
      const emoji = node("span", item.emoji);
      emoji.setAttribute("aria-hidden", "true");
      button.append(emoji, node("strong", item.name));
      button.addEventListener("click", () => {
        if (product === item.id) return;
        setAudioTarget(item.id, state.audio.category);
        refreshView("audio");
      });
      buttons.append(button);
    }
    const categories = $("#audio-categories");
    categories.replaceChildren();
    for (const item of state.categories) {
      const button = node("button", item.name, "category-choice");
      button.type = "button";
      button.setAttribute("aria-pressed", String(category === item.id));
      button.addEventListener("click", () => {
        if (category === item.id) return;
        setAudioTarget(state.audio.product, item.id);
        refreshView("audio");
      });
      categories.append(button);
    }
    $("#upload-target").textContent = product ? audioTargetLabel(audioTarget()) : "";
  }

  async function loadAudio() {
    const target = audioTarget();
    if (!target.product) return;
    const request = ++state.audio.request;
    const current = () => request === state.audio.request && sameAudioTarget(target);
    try {
      const data = await api(`/api/admin/audio?${new URLSearchParams(target)}`);
      if (!current()) return;
      state.audio.library = { ...target, data };
      renderAudioFiles();
    } catch (error) {
      if (!current()) return;
      if (!state.audio.library) $("#audio-empty").textContent = error.message;
      throw error;
    }
  }

  function stopAudio() {
    const player = $("#audio-player");
    $("#audio-preview").hidden = true;
    if (player.hasAttribute("src")) {
      player.pause();
      player.removeAttribute("src");
      player.load();
    }
  }

  function listen(file, target) {
    const player = $("#audio-player");
    const preview = $("#audio-preview");
    preview.hidden = false;
    $("#audio-playing-name").textContent = file.name;
    operationStatus("audio-player-status", "正在加载音频…", "pending");
    const params = new URLSearchParams(target);
    const source = `/api/admin/audio/preview/${encodeURIComponent(file.name)}?${params}`;
    player.src = source;
    player.play().catch(() => {
      if (!preview.hidden && player.getAttribute("src") === source) {
        operationStatus("audio-player-status", "试听未开始，可点播放键重试或下载文件。", "error");
      }
    });
    const bounds = preview.getBoundingClientRect();
    if (bounds.top < 85 || bounds.bottom > innerHeight) preview.scrollIntoView({ block: "center" });
  }

  function renderAudioFiles() {
    const library = state.audio.library;
    const empty = $("#audio-empty");
    const root = $("#audio-file-list");
    root.replaceChildren();
    const capacity = $("#audio-capacity");
    const clear = $("#clear-category-button");
    capacity.hidden = true;
    clear.disabled = true;
    if (!library) {
      $("#audio-count").textContent = "—";
      $("#audio-size").textContent = "分类 —";
      $("#audio-total-size").textContent = "全产品 —";
      $("#audio-revision").textContent = "版本 —";
      empty.hidden = false;
      empty.textContent = "正在读取音频…";
      return;
    }
    const { product, category, data } = library;
    const target = { product, category };
    const params = new URLSearchParams(target);
    const query = $("#audio-search").value.trim().toLowerCase();
    const files = data.files.filter((file) => file.name.toLowerCase().includes(query));
    $("#audio-count").textContent = query ? `${files.length} / ${data.files.length}` : data.files.length;
    $("#audio-size").textContent = `本分类 ${formatBytes(data.summary.totals[category]?.size)}`;
    $("#audio-total-size").textContent = `全产品 ${data.summary.total} 个 · ${formatBytes(data.summary.total_size)}`;
    $("#audio-revision").textContent = `版本 ${data.revision.slice(0, 8)}`;
    $("#audio-revision").title = data.revision;
    empty.hidden = files.length > 0;
    empty.textContent = query ? "没有匹配的文件名。" : "这个分类还没有音频。";
    clear.disabled = !data.files.length || state.audio.uploading;
    if (data.summary.total > 32) {
      capacity.textContent = `该产品已有 ${data.summary.total} 个文件。设备清单最多识别 32 个。`;
      capacity.hidden = false;
    }
    for (const file of files) {
      const row = node("div", null, "file-row");
      const mark = node("span", null, "file-icon");
      mark.append(icon("icon-music"));
      const item = node("div", null, "file-name");
      item.append(node("strong", file.name), node("small", formatBytes(file.size)));
      const buttons = node("div", null, "button-row");
      const preview = action("试听", () => listen(file, target), "secondary");
      const download = node("a", null, "icon-button");
      download.href = `/api/download/${encodeURIComponent(file.name)}?${params}`;
      download.download = file.name;
      download.setAttribute("aria-label", `下载 ${file.name}`);
      download.title = "下载";
      download.append(icon("icon-download"));
      download.addEventListener("click", () => feedback("audio-feedback", "已发起下载", file.name));
      const remove = node("button", null, "icon-button file-delete");
      remove.type = "button";
      remove.setAttribute("aria-label", `删除 ${file.name}`);
      remove.title = "删除";
      remove.append(icon("icon-trash"));
      remove.addEventListener("click", () => deleteAudio(file, target, remove));
      buttons.append(preview, download, remove);
      row.append(mark, item, buttons);
      root.append(row);
    }
  }

  async function deleteAudio(file, target, button) {
    if (!await decide({
      title: `删除「${file.name}」`, message: audioTargetLabel(target), label: "删除", danger: true,
    })) return;
    button.disabled = true;
    feedback("audio-feedback", "正在删除…", file.name, "pending");
    try {
      await api(`/api/delete/${encodeURIComponent(file.name)}?${new URLSearchParams(target)}`, { method: "DELETE" });
      feedback("audio-feedback", "音频已删除", file.name);
      if (sameAudioTarget(target)) await refreshView("audio");
    } catch (error) {
      feedback("audio-feedback", "删除失败", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function uploadOne(file, target, onProgress) {
    return new Promise((resolve, reject) => {
      const data = new FormData();
      data.append("file", file);
      data.append("product", target.product);
      data.append("category", target.category);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.timeout = 120_000;
      xhr.setRequestHeader("X-CSRF-Token", state.csrf);
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      });
      xhr.addEventListener("load", () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText); } catch { /* Non-JSON proxy error. */ }
        if (xhr.status === 401 && payload.code === "authentication_required") {
          window.location.replace("/login");
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else reject(new Error(payload.error || `HTTP ${xhr.status}`));
      });
      xhr.addEventListener("timeout", () => reject(new Error("上传超时，请刷新清单确认结果")));
      xhr.addEventListener("error", () => reject(new Error("网络连接失败")));
      xhr.send(data);
    });
  }

  async function uploadFiles(fileList) {
    if (state.audio.uploading) return;
    const files = [...fileList];
    const target = audioTarget();
    if (!files.length || !target.product) return;
    state.audio.uploading = true;
    const input = $("#audio-input");
    input.disabled = true;
    const clear = $("#clear-category-button");
    clear.disabled = true;
    try {
      const saved = new Set(state.audio.library?.data.files.map((file) => file.name));
      const duplicates = files.filter((file) => saved.has(file.name.trim().normalize("NFC").replace(/\.opus$/i, ".opus")));
      if (duplicates.length && !await decide({
        title: `覆盖 ${duplicates.length} 个同名文件`, message: audioTargetLabel(target),
        label: "覆盖并上传",
      })) return;

      const queue = $("#upload-queue");
      feedback("upload-feedback", `正在上传 ${files.length} 个文件…`, audioTargetLabel(target), "pending");
      queue.hidden = false;
      queue.replaceChildren(node("strong", audioTargetLabel(target)));
      const entries = files.map((file) => {
        const row = node("div", null, "upload-item");
        const caption = node("div", null, "upload-item-caption");
        const stateText = node("span", "等待上传");
        caption.append(node("span", file.name), stateText);
        const bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        bar.setAttribute("aria-label", file.name);
        row.append(caption, bar);
        queue.append(row);
        return { file, row, stateText, bar };
      });

      let uploaded = 0;
      for (const entry of entries) {
        const { file, row, stateText, bar } = entry;
        try {
          if (!/\.opus$/i.test(file.name)) throw new Error("仅支持 .opus");
          if (!file.size || file.size > fileLimit) throw new Error("文件须大于 0 B 且不超过 32 MB");
          stateText.textContent = "上传中";
          await uploadOne(file, target, (percent) => {
            bar.value = percent;
            stateText.textContent = percent === 100 ? "保存中" : `${percent}%`;
          });
          uploaded += 1;
          bar.value = 100;
          stateText.textContent = "已上传";
        } catch (error) {
          row.classList.add("upload-error");
          stateText.textContent = error.message;
        }
      }
      const failed = files.length - uploaded;
      feedback("upload-feedback", failed ? `上传结束，${uploaded} 成功、${failed} 失败` : `已上传 ${uploaded} 个文件`, audioTargetLabel(target),
        failed ? "error" : "success");
      if (state.view === "audio" && sameAudioTarget(target)) await refreshView("audio");
    } finally {
      input.disabled = false;
      input.value = "";
      state.audio.uploading = false;
      clear.disabled = !state.audio.library?.data.files.length;
    }
  }

  async function clearCategory(button) {
    const library = state.audio.library;
    if (!library?.data.files.length || state.audio.uploading) return;
    const target = { product: library.product, category: library.category };
    if (!await decide({
      title: `清空 ${library.data.files.length} 个音频`, message: audioTargetLabel(target),
      label: "清空分类", danger: true,
    })) return;
    setLoading(button, true, "正在清空…");
    feedback("category-feedback", "正在清空分类…", audioTargetLabel(target), "pending");
    try {
      const data = await api(`/api/clear?${new URLSearchParams(target)}`, { method: "POST" });
      feedback("category-feedback", "分类已清空", `删除了 ${data.deleted} 个文件`);
      if (sameAudioTarget(target)) await refreshView("audio");
    } catch (error) {
      feedback("category-feedback", "清空失败", error.message, "error");
    } finally {
      setLoading(button, false);
      button.disabled = state.audio.uploading || !state.audio.library?.data.files.length;
    }
  }

  async function queryFlash() {
    const button = $("#flash-query");
    const input = $("#flash-ip");
    const ip = input.value.trim();
    const erase = $("#flash-erase");
    state.queriedFlashIp = "";
    erase.disabled = true;
    if (!ip) {
      operationStatus("flash-result", "请输入设备 IP。", "error");
      input.focus();
      return;
    }
    setLoading(button, true, "查询中…");
    operationStatus("flash-result", `正在查询 ${ip}…`, "pending");
    try {
      const data = await api(`/api/proxy-flash?ip=${encodeURIComponent(ip)}`);
      if (ip !== input.value.trim()) return;
      const bytes = (data.files || []).reduce((sum, file) => sum + Number(file.size || 0), 0);
      operationStatus("flash-result", `${ip} · ${data.count || 0} 个文件 · ${formatBytes(bytes)}`, "success");
      state.queriedFlashIp = ip;
      erase.disabled = false;
    } catch (error) {
      if (ip === input.value.trim()) operationStatus("flash-result", error.message, "error");
    } finally {
      setLoading(button, false);
    }
  }

  async function eraseFlash(button) {
    const ip = state.queriedFlashIp;
    if (!ip || ip !== $("#flash-ip").value.trim() || !await decide({
      title: "擦除设备音频 Flash", message: `设备 IP ${ip}，此操作会清除该设备的全部外置音频。`,
      label: "擦除", field: "输入 ERASE 确认", code: "ERASE", danger: true,
    })) return;
    setLoading(button, true, "正在请求…");
    operationStatus("flash-result", "正在请求设备擦除音频…", "pending");
    try {
      await api(`/api/proxy-flash-erase?ip=${encodeURIComponent(ip)}`, { method: "POST" });
      operationStatus("flash-result", "擦除请求已发送。请查询设备状态确认结果。", "success");
    } catch (error) {
      operationStatus("flash-result", `擦除失败 · ${error.message}`, "error");
    } finally {
      setLoading(button, false);
      state.queriedFlashIp = "";
      button.disabled = true;
    }
  }

  async function refreshDeviceKeys(factory, addedLabel = "") {
    if (!factoryActive(factory)) return;
    const retry = $("#reload-device-keys");
    const reloading = !retry.hidden;
    if (reloading) setLoading(retry, true, "正在刷新…");
    if (addedLabel) operationStatus("device-key-feedback", "Key 已保存，正在刷新可选 Key…", "pending");
    try {
      await loadVoiceDashboard();
      if (!factoryActive(factory)) return;
      retry.hidden = true;
      if (!addedLabel) {
        operationStatus("device-key-feedback", reloading ? "可选 Key 已刷新。" : "", reloading ? "success" : "");
        return;
      }
      const device = voiceDevice();
      const choice = $("#voice-key-choice");
      const selected = choice.value === factory.selectedKey && Boolean(choice.value) && !device?.key_id;
      const hint = device?.key_id ? "这台设备已有 Key。要更换可先清除原 Key。"
        : selected && canReachVoiceDevice(device)
          ? (choice.disabled ? "已选中，设备当前操作结束后即可写入。" : "已选中，点击「写入 Key」应用到设备。")
          : "已保存到 Key 库，可在设备连接后写入。";
      operationStatus("device-key-feedback", `「${addedLabel}」已添加。${hint}`, "success");
      if (selected && !choice.disabled) choice.focus({ preventScroll: true });
      revealIfClipped(selected ? choice.closest(".voice-binding") : $("#device-key-feedback"));
    } catch (error) {
      if (!factoryActive(factory)) return;
      retry.hidden = false;
      const detail = addedLabel ? "Key 已保存。可点「刷新 Key」更新可选项。" : "暂时无法读取可选 Key。";
      operationStatus("device-key-feedback", `${detail} ${error.message}`, "error");
    } finally {
      setLoading(retry, false);
    }
  }

  function bindVoiceEvents() {
    const keyDialog = $("#voice-key-modal");
    const keyForm = $("#voice-key-form");
    const keyInput = $("#voice-key-input");
    const keyError = $("#voice-key-error");
    const visibility = $("#voice-key-visibility");
    let keyEntry = 0;
    const showKey = (visible) => {
      keyInput.type = visible ? "text" : "password";
      visibility.textContent = visible ? "隐藏" : "显示";
      visibility.setAttribute("aria-pressed", String(visible));
    };
    visibility.addEventListener("click", () => showKey(keyInput.type === "password"));
    keyForm.addEventListener("input", () => { keyError.hidden = true; });
    keyDialog.addEventListener("close", () => {
      keyEntry += 1;
      keyForm.reset();
      keyError.textContent = "";
      keyError.hidden = true;
      operationStatus("voice-key-progress", "");
      showKey(false);
    });
    keyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      const entry = keyEntry;
      const formActive = () => keyDialog.open && entry === keyEntry;
      const factory = state.factory;
      const inDevice = factoryActive(factory);
      const draft = Object.fromEntries(new FormData(form));
      const label = draft.label.trim();
      if (!label || !draft.key.trim()) {
        keyError.textContent = label ? "请粘贴完整的百炼 API Key。" : "请填写 Key 的名称。";
        keyError.hidden = false;
        (label ? keyInput : form.elements.label).focus();
        revealIfClipped(keyError);
        return;
      }
      keyError.hidden = true;
      operationStatus("voice-key-progress", "正在保存百炼 Key…", "pending");
      setLoading(button, true, "保存中…");
      try {
        const result = await api("/api/admin/voice/keys", { method: "POST", body: draft });
        if (formActive()) closeDialog("voice-key-modal");
        if (inDevice && factoryActive(factory)) {
          factory.selectedKey = String(result.id);
          await refreshDeviceKeys(factory, label);
        } else {
          feedback("key-library-feedback", "百炼 Key 已添加", label);
          await refreshView("voice");
        }
      } catch (error) {
        if (formActive()) {
          operationStatus("voice-key-progress", "");
          keyError.textContent = error.message;
          keyError.hidden = false;
          revealIfClipped(keyError);
        }
      } finally {
        setLoading(button, false);
      }
    });
    $("#voice-key-choice").addEventListener("change", (event) => {
      state.factory.selectedKey = event.currentTarget.value;
      operationStatus("device-key-feedback", "");
      renderVoiceDevice();
    });
    $("#reload-device-keys").addEventListener("click", () => refreshDeviceKeys(state.factory));
    $("#assign-voice-key").addEventListener("click", () => changeDeviceKey(false));
    $("#clear-voice-key").addEventListener("click", () => changeDeviceKey(true));
    $("#read-device-voices").addEventListener("click", () => runVoiceAction("list"));
    $("#restore-device-voice").addEventListener("click", () => runVoiceAction("original"));
    $("#preview-device-voice").addEventListener("click", () => runVoiceAction("preview"));
    $("#record-device-voice").addEventListener("click", async () => {
      const name = await decide({ title: "用设备录音复刻", field: "音色名称", label: "开始录音" });
      if (name) await runVoiceAction("record", undefined, name);
    });
  }

  function factoryActive(factory) {
    return Boolean(factory && state.factory === factory && $("#device-modal").open);
  }

  function renderFactoryNavigation() {
    const current = state.visibleDevices.findIndex((device) => device.id === state.selectedDevice?.id);
    $("#previous-device").disabled = current <= 0;
    $("#next-device").disabled = current < 0 || current >= state.visibleDevices.length - 1;
  }

  function renderFactoryActions() {
    const factory = state.factory;
    if (!factoryActive(factory)) return;
    const blocked = !factory.device.developer_url || Boolean(factory.busy || state.voice.run);
    const running = factory.audio?.running === true;
    const slider = $("#factory-volume");
    slider.disabled = blocked || running || factory.volume === null;
    $("#factory-read-volume").disabled = blocked;
    $("#factory-save-volume").disabled = slider.disabled || Number(slider.value) === factory.volume;
    $("#factory-audio-check").disabled = blocked || running;
    $("#factory-audio-read").disabled = blocked;
    $("#factory-audio-write").disabled = blocked || running || !factory.selected.size;
    $("#factory-audio-write").textContent = factory.selected.size ? `写入 ${factory.selected.size} 个` : "写入选中";
    $("#factory-audio-select").disabled = blocked;
    for (const check of $$("#factory-audio-items input:not([data-local])")) {
      check.disabled = blocked;
    }
    renderVoiceDevice();
  }

  async function withFactoryWork(statusId, initial, task) {
    const factory = state.factory;
    if (!factoryActive(factory) || !factory.device.developer_url || factory.busy || state.voice.run) return;
    const active = () => factoryActive(factory);
    const say = (text, tone = "") => { if (active()) operationStatus(statusId, text, tone); };
    const endpoint = `/api/admin/devices/${factory.device.id}/factory`;
    factory.busy = true;
    say(initial, "pending");
    renderFactoryActions();
    try {
      await task({ factory, endpoint, active, say });
    } catch (error) {
      say(error.message, "error");
    } finally {
      factory.busy = false;
      if (active()) renderFactoryActions();
    }
  }

  function setFactoryVolume(factory, volume) {
    factory.volume = Number.isInteger(volume) ? volume : null;
    if (factory.volume !== null) $("#factory-volume").value = volume;
    $("#factory-volume-value").textContent = factory.volume ?? "—";
  }

  function readFactoryVolume(reportResult = false) {
    return withFactoryWork("factory-volume-status", "正在读取音量…", async ({ factory, endpoint, active, say }) => {
      const result = await api(endpoint);
      if (!active()) return;
      setFactoryVolume(factory, result.volume);
      $("#factory-audio-count").textContent = result.audio_files == null ? "" : `已装 ${result.audio_files} 个`;
      if (factory.volume === null) say("设备未上报音量。");
      else say(reportResult ? "已读取设备音量。" : "", reportResult ? "success" : "");
    });
  }

  function delay(time) {
    return new Promise((resolve) => setTimeout(resolve, time));
  }

  function saveFactoryVolume() {
    const volume = Number($("#factory-volume").value);
    return withFactoryWork("factory-volume-status", "正在保存音量…", async ({ factory, endpoint, active, say }) => {
      await api(`${endpoint}/volume`, { method: "PUT", body: { volume } });
      for (let attempt = 0; active() && attempt < 5; attempt += 1) {
        const actual = await api(endpoint);
        if (!active()) return;
        if (actual.volume === volume) {
          setFactoryVolume(factory, volume);
          say("设备音量已保存", "success");
          return;
        }
        await delay(400);
      }
      say("设备已接收设置，请稍后读取音量确认。");
    });
  }

  function renderFactoryAudio() {
    const factory = state.factory;
    const files = factory?.audio?.files || [];
    $("#factory-audio-picker").hidden = !files.length;
    const list = $("#factory-audio-items");
    list.replaceChildren();
    const labels = { new: "新增", changed: "可覆盖", same: "大小相同", local: "设备已有" };
    const available = files.filter((file) => file.state !== "local");
    const select = $("#factory-audio-select");
    select.hidden = !available.length;
    select.textContent = available.every((file) => factory.selected.has(file.name))
      ? "取消全选" : "选择全部";
    for (const file of files) {
      const row = node("label", null, "factory-audio-row");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = factory.selected.has(file.name);
      if (file.state === "local") {
        check.dataset.local = "";
        check.disabled = true;
      }
      check.addEventListener("change", () => {
        if (check.checked) factory.selected.add(file.name);
        else factory.selected.delete(file.name);
        $("#factory-audio-select").textContent =
          available.every((item) => factory.selected.has(item.name)) ? "取消全选" : "选择全部";
        renderFactoryActions();
      });
      row.append(check, node("span", file.name), node("small", labels[file.state]));
      list.append(row);
    }
  }

  function updateFactoryAudio(factory, result) {
    const previousRevision = factory.audio?.revision;
    factory.audio = result;
    if (previousRevision !== result.revision) {
      factory.selected = new Set(result.files.filter((file) => file.state === "new" || file.state === "changed")
        .map((file) => file.name));
    }
    renderFactoryAudio();
  }

  function factoryAudioAction(action) {
    const initial = action === "write" ? "正在提交音频写入…" : action === "check" ? "正在检查音频…" : "正在读取设备状态…";
    return withFactoryWork("factory-audio-status", initial, async ({ factory, endpoint, active, say }) => {
      const path = `${endpoint}/audio`;
      if (action !== "status") {
        const body = action === "check" ? { action }
          : { action, names: [...factory.selected], revision: factory.audio.revision };
        await api(path, { method: "POST", body });
      }
      const deadline = Date.now() + (action === "check" ? 35_000 : 180_000);
      do {
        if (!active()) return;
        const result = await api(path);
        if (!active()) return;
        updateFactoryAudio(factory, result);
        const message = result.message || (result.running ? initial : "点击「检查音频」查看可写入文件。");
        const tone = result.running ? "pending" : result.ok ? "success" : result.message ? "error" : "";
        say(message, tone);
        if (!result.running || action === "status") return;
        await delay(1200);
      } while (Date.now() < deadline);
      say("设备仍在处理，可稍后点击「查看状态」。");
    });
  }

  function bindFactoryEvents() {
    $("#previous-device").addEventListener("click", () => {
      const index = state.visibleDevices.findIndex((device) => device.id === state.selectedDevice?.id);
      if (index > 0) openDevice(state.visibleDevices[index - 1]);
    });
    $("#next-device").addEventListener("click", () => {
      const index = state.visibleDevices.findIndex((device) => device.id === state.selectedDevice?.id);
      if (index >= 0 && index + 1 < state.visibleDevices.length) openDevice(state.visibleDevices[index + 1]);
    });
    $("#factory-read-volume").addEventListener("click", () => readFactoryVolume(true));
    $("#factory-volume").addEventListener("input", (event) => {
      $("#factory-volume-value").textContent = event.currentTarget.value;
      const changed = Number(event.currentTarget.value) !== state.factory.volume;
      $("#factory-save-volume").disabled = !changed;
      operationStatus("factory-volume-status", changed ? "音量已调整，点「保存音量」写入设备。" : "");
    });
    $("#factory-volume-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (!$("#factory-save-volume").disabled) saveFactoryVolume();
    });
    $("#factory-audio-check").addEventListener("click", () => factoryAudioAction("check"));
    $("#factory-audio-read").addEventListener("click", () => factoryAudioAction("status"));
    $("#factory-audio-write").addEventListener("click", () => factoryAudioAction("write"));
    $("#factory-audio-select").addEventListener("click", () => {
      const factory = state.factory;
      const names = factory.audio.files.filter((file) => file.state !== "local").map((file) => file.name);
      factory.selected = new Set(names.every((name) => factory.selected.has(name)) ? [] : names);
      renderFactoryAudio();
      renderFactoryActions();
    });

  }

  function bindDeviceEvents() {
    $("#device-search").addEventListener("input", renderDevices);
    $("#device-product-filter").addEventListener("change", renderDevices);
    $("#device-status-filter").addEventListener("change", renderDevices);
    bindFactoryEvents();
    $("#open-device-audio").addEventListener("click", () => openProductAudio(state.selectedDevice.product_id));

    $("#activate-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      operationStatus("activate-feedback", "正在绑定设备…", "pending");
      setLoading(button, true, "绑定中…");
      try {
        const result = await api("/api/admin/devices/activate", {
          method: "POST", body: { activation_code: form.elements.activation_code.value.trim() },
        });
        closeDialog("activate-modal");
        form.reset();
        feedback("device-feedback", "设备已绑定", result.device.name);
        await refreshView("devices");
      } catch (error) {
        operationStatus("activate-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#add-device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      operationStatus("add-device-feedback", "正在添加设备…", "pending");
      setLoading(button, true, "添加中…");
      try {
        const result = await api("/api/admin/devices", {
          method: "POST", body: Object.fromEntries(new FormData(form)),
        });
        closeDialog("add-device-modal");
        form.reset();
        operationStatus("add-device-feedback", "");
        showToken(result.api_token);
        operationStatus("token-feedback", "设备已添加，请复制并保存令牌。", "success");
        await refreshView("devices");
      } catch (error) {
        operationStatus("add-device-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const device = state.selectedDevice;
      const factory = state.factory;
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      const body = { product_id: form.elements.product_id.value };
      if (device.status !== "pending") body.status = form.elements.status.value;
      setLoading(button, true, "保存中…");
      operationStatus("registration-feedback", "正在保存登记…", "pending");
      try {
        const result = await api(`/api/admin/devices/${device.id}`, { method: "PATCH", body });
        if (factoryActive(factory)) closeDialog("device-modal");
        feedback("device-feedback", "登记信息已保存", result.device.name);
        await refreshView("devices");
      } catch (error) {
        if (factoryActive(factory)) operationStatus("registration-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#rotate-token").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const device = state.selectedDevice;
      const factory = state.factory;
      if (!await decide({
        title: `轮换 ${device.name} 的令牌`, message: "旧令牌将立即失效。", label: "轮换令牌", danger: true,
      })) return;
      setLoading(button, true, "正在轮换…");
      operationStatus("registration-feedback", "正在轮换令牌…", "pending");
      try {
        const data = await api(`/api/admin/devices/${device.id}/rotate-token`, { method: "POST" });
        if (factoryActive(factory)) closeDialog("device-modal");
        showToken(data.api_token);
        operationStatus("token-feedback", "新令牌已生成，请复制并保存。", "success");
      } catch (error) {
        if (factoryActive(factory)) operationStatus("registration-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#delete-device").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const device = state.selectedDevice;
      const factory = state.factory;
      if (!await decide({
        title: `删除 ${device.name} 的登记`, message: "设备记录和令牌将被删除。", label: "删除登记", danger: true,
      })) return;
      setLoading(button, true, "删除中…");
      operationStatus("registration-feedback", "正在删除设备登记…", "pending");
      try {
        await api(`/api/admin/devices/${device.id}`, { method: "DELETE" });
        if (factoryActive(factory)) closeDialog("device-modal");
        feedback("device-feedback", "设备登记已删除", device.name);
        await refreshView("devices");
      } catch (error) {
        if (factoryActive(factory)) operationStatus("registration-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#copy-token").addEventListener("click", async () => {
      try {
        await copyText($("#device-token").textContent);
        operationStatus("token-feedback", "令牌已复制。", "success");
      } catch {
        operationStatus("token-feedback", "复制失败，请选中令牌手工复制。", "error");
      }
    });
    $("#token-modal").addEventListener("close", () => { $("#device-token").textContent = ""; });
  }

  function bindAudioEvents() {
    $("#audio-search").addEventListener("input", renderAudioFiles);
    $("#audio-input").addEventListener("change", (event) => uploadFiles(event.currentTarget.files));
    const dropZone = $("#drop-zone");
    for (const eventName of ["dragenter", "dragover", "dragleave", "drop"]) {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.toggle("is-dragging", eventName === "dragenter" || eventName === "dragover");
        if (eventName === "drop") uploadFiles(event.dataTransfer.files);
      });
    }
    $("#clear-category-button").addEventListener("click", (event) => clearCategory(event.currentTarget));
    $("#close-audio-player").addEventListener("click", stopAudio);
    const player = $("#audio-player");
    player.addEventListener("playing", () => operationStatus("audio-player-status", "正在试听"));
    player.addEventListener("ended", () => operationStatus("audio-player-status", "试听完成", "success"));
    player.addEventListener("pause", () => {
      if (!$("#audio-preview").hidden && !player.ended) operationStatus("audio-player-status", "已暂停");
    });
    $("#audio-player").addEventListener("error", () => {
      if (!$("#audio-preview").hidden) operationStatus("audio-player-status", "无法播放这段音频，可下载文件试听。", "error");
    });
  }

  function bindSettingsEvents() {
    $("#flash-ip").addEventListener("input", () => {
      state.queriedFlashIp = "";
      $("#flash-erase").disabled = true;
      operationStatus("flash-result", "");
    });
    $("#flash-query").addEventListener("click", queryFlash);
    $("#flash-erase").addEventListener("click", (event) => eraseFlash(event.currentTarget));
    $("#password-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const values = new FormData(form);
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      if (values.get("new_password") !== values.get("confirm_password")) {
        operationStatus("password-feedback", "两次新密码不同，请重新填写确认密码。", "error");
        form.elements.confirm_password.focus();
        return;
      }
      setLoading(button, true, "更新中…");
      operationStatus("password-feedback", "正在更新管理员密码…", "pending");
      try {
        await api("/api/auth/change-password", {
          method: "POST", body: {
            current_password: values.get("current_password"), new_password: values.get("new_password"),
          },
        });
        form.reset();
        operationStatus("password-feedback", "管理员密码已更新。", "success");
      } catch (error) {
        operationStatus("password-feedback", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });
  }

  function bindEvents() {
    bindDeviceEvents();
    bindAudioEvents();
    bindVoiceEvents();
    bindSettingsEvents();
    for (const button of $$("[data-view]")) {
      button.addEventListener("click", () => navigate(button.dataset.view));
    }
    for (const link of $$("[data-nav]")) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(link.dataset.nav);
      });
    }
    for (const button of $$("[data-open-dialog]")) {
      button.addEventListener("click", () => openDialog(button.dataset.openDialog));
    }
    for (const button of $$("[data-close-dialog]")) {
      button.addEventListener("click", () => closeDialog(button.dataset.closeDialog));
    }
    $("#theme-toggle").addEventListener("click", () => {
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    $("#refresh-button").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const view = state.view;
      setLoading(button, true, "刷新中…");
      try {
        if (await refreshView(view) && state.view === view) feedback("page-feedback", "当前页已刷新", "", "success", false);
      } finally {
        setLoading(button, false);
      }
    });
    $("#logout-button").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      setLoading(button, true, "");
      try {
        await api("/api/auth/logout", { method: "POST" });
      } catch (error) {
        feedback("page-feedback", "退出登录失败", error.message, "error");
        setLoading(button, false);
        return;
      }
      window.location.replace("/login");
    });
    window.addEventListener("hashchange", () => navigate(location.hash.slice(1), true));
  }

  async function loadCatalog() {
    if (state.products.length) return;
    if (!catalogRequest) {
      catalogRequest = api("/api/products").then((catalog) => {
        state.products = catalog.products;
        state.categories = catalog.categories;
        state.audio.product = state.products[0]?.id || "";
        populateProductSelects();
        renderAudioSelectors();
      }).finally(() => { catalogRequest = null; });
    }
    await catalogRequest;
  }

  async function initialize() {
    try {
      const savedTheme = localStorage.getItem("audio-hub-theme");
      if (savedTheme) setTheme(savedTheme);
    } catch {
      // The app also works with storage disabled.
    }
    bindEvents();
    await navigate(location.hash.slice(1), true);
    const refreshOnlineDevices = async () => {
      const active = document.activeElement;
      if (!document.hidden && state.view === "devices" && state.devicesLoaded && !$("dialog[open]") &&
          !$("#device-list").contains(active) && $("#page-load").hidden) await refreshView("devices", true);
      setTimeout(refreshOnlineDevices, 4_000);
    };
    setTimeout(refreshOnlineDevices, 4_000);
  }

  initialize();
})();
