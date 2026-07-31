(() => {
  "use strict";

  const state = {
    csrf: document.querySelector('meta[name="csrf-token"]').content,
    products: [],
    categories: [],
    devices: [],
    overview: null,
    files: [],
    currentProduct: "",
    currentCategory: "animal",
    view: "overview",
  };

  const pageMeta = {
    overview: ["工作台", "系统概览"],
    devices: ["设备中心", "设备管理"],
    audio: ["内容中心", "音频中心"],
    system: ["平台配置", "系统设置"],
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function icon(id, className = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    if (className) svg.setAttribute("class", className);
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.appendChild(use);
    return svg;
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
    return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[index]}`;
  }

  function formatTime(value) {
    if (!value) return "从未";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "—";
    const delta = Date.now() - date.valueOf();
    if (delta < 60_000) return "刚刚";
    if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`;
    if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`;
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function productById(id) {
    return state.products.find((product) => product.id === id) || {
      name: id,
      short_name: id,
      emoji: "⌁",
      color: "#7b8493",
    };
  }

  async function api(url, options = {}) {
    const settings = { ...options };
    const method = (settings.method || "GET").toUpperCase();
    settings.headers = new Headers(settings.headers || {});
    settings.headers.set("Accept", "application/json");
    if (!["GET", "HEAD"].includes(method)) {
      settings.headers.set("X-CSRF-Token", state.csrf);
    }
    if (
      settings.body &&
      !(settings.body instanceof FormData) &&
      typeof settings.body !== "string"
    ) {
      settings.headers.set("Content-Type", "application/json");
      settings.body = JSON.stringify(settings.body);
    }
    const response = await fetch(url, settings);
    let payload;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      payload = { message: await response.text() };
    }
    if (response.status === 401 && payload.code === "authentication_required") {
      window.location.replace("/login");
      throw new Error("登录状态已失效");
    }
    if (!response.ok) throw new Error(payload.error || payload.message || "请求失败");
    return payload;
  }

  function toast(title, message = "", type = "success") {
    const item = document.createElement("div");
    item.className = `toast${type === "error" ? " is-error" : ""}`;
    const badge = document.createElement("span");
    badge.className = "toast-icon";
    badge.textContent = type === "error" ? "!" : "✓";
    const body = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = message;
    body.append(heading, copy);
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "关闭通知");
    close.textContent = "×";
    close.addEventListener("click", () => item.remove());
    item.append(badge, body, close);
    $("#toast-stack").appendChild(item);
    setTimeout(() => item.remove(), 4600);
  }

  function setLoading(button, loading, label = "处理中…") {
    if (!button) return;
    if (loading) {
      button.dataset.label = button.textContent;
      button.textContent = label;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog?.open) dialog.close();
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("audio-hub-theme", theme);
  }

  function navigate(view, updateHash = true) {
    if (!pageMeta[view]) view = "overview";
    state.view = view;
    $$("[data-view-panel]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
    });
    $$(".nav-item[data-view]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === view);
    });
    $("#page-eyebrow").textContent = pageMeta[view][0];
    $("#page-title").textContent = pageMeta[view][1];
    document.title = `${pageMeta[view][1]} · Audio Hub`;
    if (updateHash) history.replaceState(null, "", `#${view}`);
    closeMobileMenu();
    refreshView(view);
  }

  async function refreshView(view = state.view, silent = false) {
    try {
      if (view === "overview") await loadOverview();
      if (view === "devices") await loadDevices();
      if (view === "audio") await loadAudio();
    } catch (error) {
      toast("加载失败", error.message, "error");
    } finally {
      if (!silent) $("#refresh-button").classList.remove("is-spinning");
    }
  }

  function statusBadge(device) {
    const span = document.createElement("span");
    let key;
    let label;
    if (device.status === "pending") [key, label] = ["pending", "待激活"];
    else if (device.status === "disabled") [key, label] = ["disabled", "已停用"];
    else if (device.online) [key, label] = ["online", "在线"];
    else [key, label] = ["offline", "离线"];
    span.className = `status-badge status-badge--${key}`;
    span.textContent = label;
    return span;
  }

  function makeDeviceCell(device) {
    const product = productById(device.product_id);
    const wrapper = document.createElement("div");
    wrapper.className = "device-cell";
    const avatar = document.createElement("span");
    avatar.className = "device-avatar";
    avatar.textContent = product.emoji;
    const dot = document.createElement("i");
    if (device.online) dot.className = "is-online";
    avatar.appendChild(dot);
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = device.name;
    const uid = document.createElement("small");
    uid.textContent = device.device_uid;
    copy.append(name, uid);
    wrapper.append(avatar, copy);
    return wrapper;
  }

  async function loadOverview() {
    const data = await api("/api/admin/overview");
    state.overview = data;
    const pendingBadge = $("#pending-badge");
    pendingBadge.textContent = data.devices.pending;
    pendingBadge.hidden = data.devices.pending === 0;
    renderOverviewStats(data);
    renderOverviewProducts(data.products);
    renderRecentDevices(data.recent_devices);
  }

  function renderOverviewStats(data) {
    const cards = [
      ["icon-cpu", "violet", data.devices.total, "已登记设备", `${data.devices.pending} 台待激活`],
      ["icon-grid", "green", data.devices.online, "当前在线", `心跳窗口 120 秒`],
      ["icon-music", "amber", data.audio.total, "音频文件", formatBytes(data.audio.total_size)],
      ["icon-link", "blue", data.products.length, "产品型号", "统一设备平台"],
    ];
    const root = $("#overview-stats");
    root.replaceChildren();
    cards.forEach(([iconId, color, value, label, detail]) => {
      const card = document.createElement("article");
      card.className = "stat-card";
      const visual = document.createElement("span");
      visual.className = `stat-icon stat-icon--${color}`;
      visual.appendChild(icon(iconId));
      const copy = document.createElement("span");
      copy.className = "stat-copy";
      const strong = document.createElement("strong");
      strong.textContent = value;
      const caption = document.createElement("span");
      caption.textContent = label;
      const small = document.createElement("small");
      small.textContent = detail;
      copy.append(strong, caption, small);
      card.append(visual, copy);
      root.appendChild(card);
    });
  }

  function renderOverviewProducts(products) {
    const root = $("#overview-products");
    root.replaceChildren();
    products.forEach((product) => {
      const card = document.createElement("article");
      card.className = "product-card";
      card.style.setProperty("--product-color", product.color);
      card.style.setProperty("--product-soft", `${product.color}18`);
      const top = document.createElement("div");
      top.className = "product-card__top";
      const emoji = document.createElement("span");
      emoji.className = "product-emoji";
      emoji.textContent = product.emoji;
      const heading = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = product.name;
      const description = document.createElement("p");
      description.textContent = product.description;
      heading.append(title, description);
      top.append(emoji, heading);
      const metrics = document.createElement("div");
      metrics.className = "product-metrics";
      [
        [product.device_count, "设备"],
        [product.online_count, "在线"],
        [product.audio_count, "音频"],
      ].forEach(([value, label]) => {
        const metric = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = label;
        metric.append(strong, span);
        metrics.appendChild(metric);
      });
      card.append(top, metrics);
      root.appendChild(card);
    });
  }

  function renderRecentDevices(devices) {
    const body = $("#recent-devices");
    body.replaceChildren();
    $("#recent-empty").hidden = devices.length > 0;
    devices.forEach((device) => {
      const row = document.createElement("tr");
      const deviceCell = document.createElement("td");
      deviceCell.appendChild(makeDeviceCell(device));
      const productCell = document.createElement("td");
      const product = productById(device.product_id);
      productCell.textContent = `${product.emoji} ${product.short_name}`;
      const statusCell = document.createElement("td");
      statusCell.appendChild(statusBadge(device));
      const timeCell = document.createElement("td");
      timeCell.textContent = formatTime(device.last_seen_at);
      row.append(deviceCell, productCell, statusCell, timeCell);
      body.appendChild(row);
    });
  }

  async function loadDevices() {
    const data = await api("/api/admin/devices");
    state.devices = data.devices;
    renderDevices();
  }

  function filteredDevices() {
    const query = $("#device-search").value.trim().toLocaleLowerCase();
    const product = $("#device-product-filter").value;
    const status = $("#device-status-filter").value;
    return state.devices.filter((device) => {
      const haystack = `${device.name} ${device.device_uid} ${device.ip_address || ""}`.toLocaleLowerCase();
      const queryMatch = !query || haystack.includes(query);
      const productMatch = !product || device.product_id === product;
      let statusMatch = true;
      if (status === "online") statusMatch = device.online;
      else if (status === "offline") statusMatch = device.status === "active" && !device.online;
      else if (status) statusMatch = device.status === status;
      return queryMatch && productMatch && statusMatch;
    });
  }

  function renderDevices() {
    const devices = filteredDevices();
    const body = $("#device-list");
    body.replaceChildren();
    $("#device-empty").hidden = devices.length > 0;
    devices.forEach((device) => {
      const row = document.createElement("tr");
      const deviceCell = document.createElement("td");
      deviceCell.appendChild(makeDeviceCell(device));
      const productCell = document.createElement("td");
      const productLabel = document.createElement("span");
      productLabel.className = "product-label";
      productLabel.textContent = `${productById(device.product_id).emoji} ${productById(device.product_id).short_name}`;
      productCell.appendChild(productLabel);
      const statusCell = document.createElement("td");
      statusCell.appendChild(statusBadge(device));
      const batteryCell = document.createElement("td");
      if (device.battery_level == null) {
        batteryCell.textContent = "—";
      } else {
        const battery = document.createElement("span");
        battery.className = "battery";
        const track = document.createElement("span");
        track.className = "battery-track";
        const fill = document.createElement("span");
        fill.className = `battery-fill${device.battery_level < 20 ? " is-low" : ""}`;
        fill.style.width = `${device.battery_level}%`;
        track.appendChild(fill);
        const value = document.createElement("span");
        value.textContent = `${device.battery_level}%`;
        battery.append(track, value);
        batteryCell.appendChild(battery);
      }
      const firmwareCell = document.createElement("td");
      firmwareCell.textContent = device.firmware_version || "—";
      const timeCell = document.createElement("td");
      timeCell.textContent = formatTime(device.last_seen_at);
      const actionCell = document.createElement("td");
      actionCell.className = "row-actions";
      const action = document.createElement("button");
      action.type = "button";
      action.className = "icon-button";
      action.title = "查看设备";
      action.appendChild(icon("icon-more"));
      action.addEventListener("click", () => openDevice(device));
      actionCell.appendChild(action);
      row.append(deviceCell, productCell, statusCell, batteryCell, firmwareCell, timeCell, actionCell);
      body.appendChild(row);
    });
  }

  function populateProductSelects() {
    ["device-product-filter", "add-device-product", "edit-device-product"].forEach((id) => {
      const select = document.getElementById(id);
      const keepFirst = id === "device-product-filter";
      const first = keepFirst ? select.options[0] : null;
      select.replaceChildren();
      if (first) select.appendChild(first);
      state.products.forEach((product) => {
        const option = document.createElement("option");
        option.value = product.id;
        option.textContent = `${product.emoji} ${product.name}`;
        select.appendChild(option);
      });
    });
  }

  function openDevice(device) {
    const form = $("#device-form");
    form.elements.id.value = device.id;
    form.elements.name.value = device.name;
    form.elements.product_id.value = device.product_id;
    form.elements.status.value = device.status === "pending" ? "active" : device.status;
    $("#device-modal-title").textContent = device.name;
    const strip = $("#device-detail-strip");
    strip.replaceChildren();
    [
      [device.device_uid, "设备 ID"],
      [device.ip_address || "—", "IP 地址"],
      [device.token_prefix ? `${device.token_prefix}…` : "未签发", "令牌"],
    ].forEach(([value, label]) => {
      const box = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = value;
      const span = document.createElement("span");
      span.textContent = label;
      box.append(strong, span);
      strip.appendChild(box);
    });
    openDialog("device-modal");
  }

  function showToken(token) {
    $("#device-token").textContent = token;
    openDialog("token-modal");
  }

  function renderAudioSelectors() {
    const products = $("#audio-products");
    products.replaceChildren();
    state.products.forEach((product) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `product-choice${state.currentProduct === product.id ? " is-active" : ""}`;
      button.style.setProperty("--product-color", product.color);
      button.style.setProperty("--product-soft", `${product.color}1a`);
      const emoji = document.createElement("span");
      emoji.textContent = product.emoji;
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = product.name;
      const description = document.createElement("small");
      description.textContent = product.description;
      copy.append(name, description);
      button.append(emoji, copy);
      button.addEventListener("click", () => {
        state.currentProduct = product.id;
        renderAudioSelectors();
        loadAudio();
      });
      products.appendChild(button);
    });
    const categories = $("#audio-categories");
    categories.replaceChildren();
    state.categories.forEach((category) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `category-choice${state.currentCategory === category.id ? " is-active" : ""}`;
      button.textContent = `${category.emoji} ${category.name}`;
      button.addEventListener("click", () => {
        state.currentCategory = category.id;
        renderAudioSelectors();
        loadAudio();
      });
      categories.appendChild(button);
    });
  }

  async function loadAudio() {
    if (!state.currentProduct) return;
    const params = new URLSearchParams({
      product: state.currentProduct,
      category: state.currentCategory,
    });
    const data = await api(`/api/files?${params}`);
    state.files = data.files;
    $("#audio-revision").textContent = `版本 ${data.revision}`;
    renderAudioFiles();
  }

  function renderAudioFiles() {
    const query = $("#audio-search").value.trim().toLocaleLowerCase();
    const files = state.files.filter((file) => file.name.toLocaleLowerCase().includes(query));
    const root = $("#audio-file-list");
    root.replaceChildren();
    $("#audio-empty").hidden = files.length > 0;
    $("#audio-count").textContent = state.files.length;
    $("#audio-size").textContent = `共 ${formatBytes(state.files.reduce((sum, file) => sum + file.size, 0))}`;
    files.forEach((file) => {
      const row = document.createElement("div");
      row.className = "file-row";
      const visual = document.createElement("span");
      visual.className = "file-icon";
      visual.appendChild(icon("icon-music"));
      const info = document.createElement("span");
      info.className = "file-info";
      const name = document.createElement("strong");
      name.textContent = file.name;
      name.title = file.name;
      const meta = document.createElement("span");
      meta.textContent = `${formatBytes(file.size)} · #${file.index}`;
      info.append(name, meta);
      const download = document.createElement("a");
      download.className = "icon-button file-download";
      download.title = "下载";
      download.href = `/api/download-idx/${file.index}?${new URLSearchParams({
        product: state.currentProduct,
        category: state.currentCategory,
      })}`;
      download.appendChild(icon("icon-download"));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button file-delete";
      remove.title = "删除";
      remove.appendChild(icon("icon-trash"));
      remove.addEventListener("click", () => deleteAudio(file));
      row.append(visual, info, download, remove);
      root.appendChild(row);
    });
  }

  async function deleteAudio(file) {
    if (!confirm(`确定删除“${file.name}”吗？`)) return;
    try {
      const params = new URLSearchParams({
        product: state.currentProduct,
        category: state.currentCategory,
      });
      await api(`/api/delete/${encodeURIComponent(file.name)}?${params}`, { method: "DELETE" });
      toast("文件已删除", file.name);
      await loadAudio();
      if (state.overview) loadOverview();
    } catch (error) {
      toast("删除失败", error.message, "error");
    }
  }

  async function uploadFiles(fileList) {
    const files = [...fileList].filter((file) => file.name.toLocaleLowerCase().endsWith(".opus"));
    if (!files.length) {
      toast("未找到 Opus 文件", "请选择扩展名为 .opus 的音频", "error");
      return;
    }
    const queue = $("#upload-queue");
    queue.hidden = false;
    queue.replaceChildren();
    for (const file of files) {
      const item = document.createElement("div");
      item.className = "upload-item";
      const line = document.createElement("div");
      line.className = "upload-item__line";
      const name = document.createElement("span");
      name.textContent = file.name;
      const status = document.createElement("span");
      status.textContent = "等待中";
      const progress = document.createElement("div");
      progress.className = "progress";
      const bar = document.createElement("div");
      bar.style.width = "0%";
      progress.appendChild(bar);
      line.append(name, status);
      item.append(line, progress);
      queue.appendChild(item);
      try {
        await uploadOne(file, (percent) => {
          bar.style.width = `${percent}%`;
          status.textContent = `${percent}%`;
        });
        status.textContent = "已完成";
      } catch (error) {
        status.textContent = "失败";
        bar.style.background = "var(--danger)";
        toast(`上传失败：${file.name}`, error.message, "error");
      }
    }
    $("#audio-input").value = "";
    await loadAudio();
    if (state.overview) loadOverview();
    setTimeout(() => {
      queue.hidden = true;
      queue.replaceChildren();
    }, 1600);
  }

  function uploadOne(file, onProgress) {
    return new Promise((resolve, reject) => {
      const data = new FormData();
      data.append("file", file);
      data.append("product", state.currentProduct);
      data.append("category", state.currentCategory);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.setRequestHeader("X-CSRF-Token", state.csrf);
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      });
      xhr.addEventListener("load", () => {
        let payload = {};
        try {
          payload = JSON.parse(xhr.responseText || "{}");
        } catch {
          payload = {};
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          toast("上传完成", file.name);
          resolve(payload);
        } else {
          reject(new Error(payload.error || `HTTP ${xhr.status}`));
        }
      });
      xhr.addEventListener("error", () => reject(new Error("网络连接失败")));
      xhr.send(data);
    });
  }

  async function queryFlash() {
    const button = $("#flash-query");
    const ip = $("#flash-ip").value.trim();
    if (!ip) {
      toast("请输入设备 IP", "", "error");
      return;
    }
    setLoading(button, true, "查询中…");
    try {
      const data = await api(`/api/proxy-flash?ip=${encodeURIComponent(ip)}`);
      $("#flash-dot").className = "status-dot status-dot--online";
      const result = $("#flash-result");
      result.replaceChildren();
      const stats = document.createElement("div");
      stats.className = "device-result__stats";
      const totalSize = (data.files || []).reduce((sum, file) => sum + Number(file.size || 0), 0);
      [
        [data.count || 0, "文件数"],
        [formatBytes(totalSize), "已使用"],
        [ip, "设备 IP"],
      ].forEach(([value, label]) => {
        const cell = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = label;
        cell.append(strong, span);
        stats.appendChild(cell);
      });
      result.appendChild(stats);
    } catch (error) {
      $("#flash-dot").className = "status-dot status-dot--neutral";
      $("#flash-result").textContent = error.message;
      toast("设备连接失败", error.message, "error");
    } finally {
      setLoading(button, false);
    }
  }

  function openMobileMenu() {
    $("#sidebar").classList.add("is-open");
    $("#mobile-overlay").classList.add("is-open");
  }

  function closeMobileMenu() {
    $("#sidebar").classList.remove("is-open");
    $("#mobile-overlay").classList.remove("is-open");
  }

  function bindEvents() {
    $$(".nav-item[data-view]").forEach((button) => {
      button.addEventListener("click", () => navigate(button.dataset.view));
    });
    $$("[data-nav]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(button.dataset.nav);
      });
    });
    $$('[data-action="open-activate"]').forEach((button) => {
      button.addEventListener("click", () => openDialog("activate-modal"));
    });
    $$('[data-action="open-add-device"]').forEach((button) => {
      button.addEventListener("click", () => openDialog("add-device-modal"));
    });
    $$('[data-action="show-devices"]').forEach((button) => {
      button.addEventListener("click", () => navigate("devices"));
    });
    $$("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => closeDialog(button.dataset.closeDialog));
    });
    $("#mobile-menu").addEventListener("click", openMobileMenu);
    $("#mobile-overlay").addEventListener("click", closeMobileMenu);
    $("#theme-toggle").addEventListener("click", () => {
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    $("#refresh-button").addEventListener("click", (event) => {
      event.currentTarget.classList.add("is-spinning");
      refreshView();
    });
    $("#logout-button").addEventListener("click", async () => {
      try {
        await api("/api/auth/logout", { method: "POST" });
      } finally {
        window.location.replace("/login");
      }
    });
    ["device-search", "device-product-filter", "device-status-filter"].forEach((id) => {
      document.getElementById(id).addEventListener(id === "device-search" ? "input" : "change", renderDevices);
    });
    $("#audio-search").addEventListener("input", renderAudioFiles);
    $("#audio-input").addEventListener("change", (event) => uploadFiles(event.target.files));
    const dropZone = $("#drop-zone");
    ["dragenter", "dragover"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.remove("is-dragging");
      });
    });
    dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));
    $("#clear-category-button").addEventListener("click", async () => {
      const category = state.categories.find((item) => item.id === state.currentCategory);
      const product = productById(state.currentProduct);
      if (!confirm(`确定清空“${product.name} / ${category?.name || state.currentCategory}”的全部音频吗？`)) return;
      try {
        const params = new URLSearchParams({
          product: state.currentProduct,
          category: state.currentCategory,
        });
        const data = await api(`/api/clear?${params}`, { method: "POST" });
        toast("分类已清空", `删除 ${data.deleted} 个文件`);
        await loadAudio();
      } catch (error) {
        toast("清空失败", error.message, "error");
      }
    });
    $("#flash-query").addEventListener("click", queryFlash);
    $("#flash-erase").addEventListener("click", async () => {
      const ip = $("#flash-ip").value.trim();
      if (!ip || !confirm(`确定擦除设备 ${ip} 的全部音频 Flash 吗？`)) return;
      try {
        await api(`/api/proxy-flash-erase?ip=${encodeURIComponent(ip)}`, { method: "POST" });
        toast("擦除请求已发送", ip);
        queryFlash();
      } catch (error) {
        toast("擦除失败", error.message, "error");
      }
    });

    $("#activate-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (event.submitter?.value === "cancel") {
        closeDialog("activate-modal");
        return;
      }
      const button = event.submitter;
      setLoading(button, true, "绑定中…");
      try {
        const code = $("#activation-code").value.trim();
        const data = await api("/api/admin/devices/activate", {
          method: "POST",
          body: { activation_code: code },
        });
        closeDialog("activate-modal");
        event.currentTarget.reset();
        toast("设备绑定成功", data.device.name);
        await Promise.all([loadOverview(), loadDevices()]);
      } catch (error) {
        toast("绑定失败", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#add-device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (event.submitter?.value === "cancel") {
        closeDialog("add-device-modal");
        return;
      }
      const button = event.submitter;
      const form = new FormData(event.currentTarget);
      setLoading(button, true, "添加中…");
      try {
        const data = await api("/api/admin/devices", {
          method: "POST",
          body: Object.fromEntries(form.entries()),
        });
        closeDialog("add-device-modal");
        event.currentTarget.reset();
        showToken(data.api_token);
        await Promise.all([loadOverview(), loadDevices()]);
      } catch (error) {
        toast("添加失败", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (event.submitter?.value === "cancel") {
        closeDialog("device-modal");
        return;
      }
      const button = event.submitter;
      const form = new FormData(event.currentTarget);
      const id = form.get("id");
      setLoading(button, true, "保存中…");
      try {
        await api(`/api/admin/devices/${id}`, {
          method: "PATCH",
          body: {
            name: form.get("name"),
            product_id: form.get("product_id"),
            status: form.get("status"),
          },
        });
        closeDialog("device-modal");
        toast("设备信息已更新");
        await Promise.all([loadOverview(), loadDevices()]);
      } catch (error) {
        toast("保存失败", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });

    $("#rotate-token").addEventListener("click", async () => {
      const id = $("#device-form").elements.id.value;
      if (!confirm("轮换后旧令牌立即失效，确定继续吗？")) return;
      try {
        const data = await api(`/api/admin/devices/${id}/rotate-token`, { method: "POST" });
        closeDialog("device-modal");
        showToken(data.api_token);
        loadDevices();
      } catch (error) {
        toast("令牌轮换失败", error.message, "error");
      }
    });

    $("#delete-device").addEventListener("click", async () => {
      const form = $("#device-form");
      const id = form.elements.id.value;
      const name = form.elements.name.value;
      if (!confirm(`确定永久删除设备“${name}”吗？此操作不可撤销。`)) return;
      try {
        await api(`/api/admin/devices/${id}`, { method: "DELETE" });
        closeDialog("device-modal");
        toast("设备已删除", name);
        await Promise.all([loadOverview(), loadDevices()]);
      } catch (error) {
        toast("删除失败", error.message, "error");
      }
    });

    $("#copy-token").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("#device-token").textContent);
        toast("令牌已复制");
      } catch {
        toast("复制失败", "请手工选中复制", "error");
      }
    });

    $("#password-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      if (form.get("new_password") !== form.get("confirm_password")) {
        toast("密码不一致", "请重新确认新密码", "error");
        return;
      }
      const button = event.submitter;
      setLoading(button, true, "更新中…");
      try {
        await api("/api/auth/change-password", {
          method: "POST",
          body: {
            current_password: form.get("current_password"),
            new_password: form.get("new_password"),
          },
        });
        event.currentTarget.reset();
        toast("管理员密码已更新");
      } catch (error) {
        toast("密码更新失败", error.message, "error");
      } finally {
        setLoading(button, false);
      }
    });
  }

  async function initialize() {
    const savedTheme = localStorage.getItem("audio-hub-theme");
    if (savedTheme) setTheme(savedTheme);
    bindEvents();
    try {
      const [catalog, sessionInfo] = await Promise.all([
        api("/api/products"),
        api("/api/auth/session"),
      ]);
      state.products = catalog.products;
      state.categories = catalog.categories;
      state.csrf = sessionInfo.csrf_token;
      state.currentProduct = state.products[0]?.id || "";
      populateProductSelects();
      renderAudioSelectors();
      const initial = window.location.hash.slice(1);
      navigate(pageMeta[initial] ? initial : "overview", false);
      setInterval(() => {
        if (state.view === "overview" || state.view === "devices") {
          refreshView(state.view, true);
        }
      }, 60_000);
    } catch (error) {
      toast("初始化失败", error.message, "error");
    }
  }

  initialize();
})();
