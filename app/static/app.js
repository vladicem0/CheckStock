"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const number = new Intl.NumberFormat("ru-RU");
const dates = new Intl.DateTimeFormat("ru-RU", {dateStyle: "short", timeStyle: "short"});
const stateLabels = {
  completed: ["Выполнено", "complete"], draft: ["Заявка", "draft"],
  in_transit: ["В пути", "transit"], received: ["Принято", "complete"],
  reserved: ["Резерв", "reserve"], shipped: ["В пути к клиенту", "transit"],
  cancelled: ["Отменено", "cancelled"]
};
const kindLabels = {receipt: "Приход", transfer: "Перемещение", fbs: "FBS", fbo: "FBO"};
const eventLabels = {
  receipt_completed: "Приход на ФФ", transfer_created: "Создана заявка",
  transfer_dispatched: "Перемещение отправлено", transfer_received: "Перемещение принято",
  fbs_reserved: "Создан резерв FBS", fbs_shipped: "FBS отправлен клиенту",
  fbs_cancelled: "Резерв FBS отменён", fbo_shipped: "FBO отправлен",
  fbo_received: "FBO принят маркетплейсом"
};
let latestLoad = 0;
let currentInventory = null;
const pendingOperations = new Set();
const pendingWarehouses = new Set();
const warehouseDrafts = new Map();

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}

function cell(row, content, className = "") {
  const result = node("td", className);
  if (content instanceof Node) result.append(content);
  else result.textContent = String(content);
  row.append(result);
  return result;
}

function statusBadge(status) {
  const [label, style] = stateLabels[status] || [status, "draft"];
  return node("span", `badge ${style}`, label);
}

function shortId(id) {
  const result = node("span", "operation-id", id.slice(0, 8));
  result.title = `ID операции: ${id}`;
  return result;
}

function route(source, destination) {
  const result = node("div", "route-text");
  result.append(node("span", "", source), node("span", "route-arrow", "→"), node("span", "", destination));
  return result;
}

function feedback(element, message = "") {
  element.textContent = message;
  element.hidden = !message;
}

function emptyRow(body, columns, text) {
  const row = node("tr");
  const value = cell(row, text, "empty");
  value.colSpan = columns;
  body.replaceChildren(row);
}

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {cache: "no-store", ...options});
  } catch (_) {
    const error = new Error("Не удалось получить ответ сервера. Проверьте соединение и обновите данные, прежде чем повторять действие.");
    error.uncertain = true;
    throw error;
  }
  let data;
  try {
    data = await response.json();
  } catch (_) {
    const error = new Error("Сервер вернул неожиданный ответ. Обновите данные, чтобы проверить результат действия.");
    error.uncertain = true;
    throw error;
  }
  if (!response.ok) {
    const fields = Array.isArray(data?.errors) ? data.errors.map(item => item.message).join(". ") : "";
    const shortage = Number.isInteger(data?.available_here) && Number.isInteger(data?.requested)
      ? ` Доступно на выбранном ФФ: ${number.format(data.available_here)} шт.; запрошено: ${number.format(data.requested)} шт.` : "";
    const error = new Error(`${data?.detail || "Не удалось выполнить действие"}${fields ? `. ${fields}` : ""}${shortage}`);
    error.status = response.status;
    // A server error can happen after commit while serializing a response.
    error.uncertain = response.status >= 500;
    throw error;
  }
  return data;
}

function post(url, payload) {
  return api(url, {
    method: "POST", headers: {"Content-Type": "application/json"},
    ...(payload === undefined ? {} : {body: JSON.stringify(payload)})
  });
}

function fillSelect(select, items, label) {
  const previous = select.value;
  const previousExists = items.some(item => item.id === previous);
  const requireChoice = select.dataset.selectionRequired === "true" || Boolean(previous && !previousExists);
  select.replaceChildren();
  for (const item of items) {
    const option = node("option", "", label(item));
    option.value = item.id;
    select.append(option);
  }
  if (previousExists) {
    select.value = previous;
    delete select.dataset.selectionRequired;
  } else if (requireChoice) {
    const label = select.dataset.warehouses === "active" ? "Выберите активный ФФ" : select.hasAttribute("data-products") ? "Выберите товар" : "Выберите ФФ";
    const placeholder = node("option", "", label);
    placeholder.value = "";
    placeholder.disabled = true;
    select.prepend(placeholder);
    select.value = "";
    select.dataset.selectionRequired = "true";
  } else if (!items.length) {
    const option = node("option", "", "Нет доступных вариантов");
    option.value = "";
    select.append(option);
  }
}

function updateFormAvailability() {
  $$('[data-operation-form]').forEach(form => {
    const submission = form.submission;
    $$('input[name], select[name]', form).forEach(control => {
      if (submission) {
        const value = String(submission.payload[control.name]);
        if (control.tagName === "SELECT" && ![...control.options].some(option => option.value === value)) {
          const option = node("option", "", submission.labels[control.name] || `Исходный выбор · ${value}`);
          option.value = value;
          control.append(option);
        }
        control.value = value;
      }
      control.disabled = Boolean(submission) || form.dataset.pending === "true";
    });
    const unavailable = $$('select', form).some(select => !select.value);
    const button = $('button[type="submit"]', form);
    button.disabled = form.dataset.pending === "true" || (!submission && unavailable);
    button.textContent = form.dataset.pending === "true" ? "Выполняем…"
      : submission ? "Повторить запрос" : form.submitLabel;
  });
}

function updateForms(inventory) {
  $$('select[data-products]').forEach(select => fillSelect(select, inventory.products, item => `${item.sku} · ${item.name}`));
  const destination = $("#transfer-destination");
  const initialDestination = destination.dataset.initialized !== "true";
  $$('select[data-warehouses]').forEach(select => {
    const choices = inventory.warehouses.filter(item => select.dataset.warehouses === "all" || !item.archived);
    fillSelect(select, choices, item => `${item.name}${item.archived ? " · архив" : ""}`);
  });
  destination.dataset.initialized = "true";
  if (initialDestination && destination.dataset.selectionRequired !== "true") {
    const source = $("#transfer-source").value;
    const other = [...destination.options].find(option => option.value && option.value !== source);
    if (other) destination.value = other.value;
  }
  updateFormAvailability();
}

function renderInventory(inventory) {
  currentInventory = inventory;
  const ids = {
    on_hand: "total-on-hand", reserved_fbs: "total-reserved", available: "total-available",
    transit_ff: "total-transit-ff", transit_fbo: "total-transit-fbo", transit_fbs: "total-transit-fbs"
  };
  Object.entries(ids).forEach(([key, id]) => {document.getElementById(id).textContent = number.format(inventory.totals[key]);});
  $("#seed-empty").hidden = inventory.products.length !== 0;
  $("#archive-note").hidden = !inventory.warehouses.some(item => item.archived);
  const table = $("#stock-table");
  const head = node("tr");
  head.append(node("th", "", "Товар / SKU"));
  for (const warehouse of inventory.warehouses) {
    const th = node("th");
    const title = node("span", "warehouse-heading", warehouse.name);
    title.title = `ID склада: ${warehouse.id}`;
    if (warehouse.archived) title.append(node("span", "badge archive", "Архив"));
    const labels = node("div", "matrix-labels");
    ["Физически", "Резерв FBS", "Доступно"].forEach(label => labels.append(node("span", "", label)));
    th.append(title, labels);
    head.append(th);
  }
  $("thead", table).replaceChildren(head);
  const body = $("tbody", table);
  body.replaceChildren();
  const cells = new Map(inventory.balances.map(item => [`${item.product_id}:${item.warehouse_id}`, item]));
  for (const product of inventory.products) {
    const row = node("tr");
    const name = node("div");
    name.append(node("span", "product-name", product.name), node("span", "sku", product.sku));
    cell(row, name);
    for (const warehouse of inventory.warehouses) {
      const balance = cells.get(`${product.id}:${warehouse.id}`);
      const values = node("div", "matrix-values");
      ["on_hand", "reserved_fbs", "available"].forEach((field, index) => {
        const value = balance[field];
        values.append(node("span", `${value === 0 ? "zero" : ""} ${index === 1 ? "reserved-value" : ""}`, number.format(value)));
      });
      cell(row, values, warehouse.archived ? "archived-cell" : "");
    }
    body.append(row);
  }
  if (!inventory.products.length) emptyRow(body, inventory.warehouses.length + 1, "Пока нет товаров. Загрузите демонстрационные данные.");
  for (const category of ["ff", "fbo", "fbs"]) {
    document.getElementById(`transit-${category}-count`).textContent = `${number.format(inventory.totals[`transit_${category}`])} шт.`;
    const list = document.getElementById(`transit-${category}-list`);
    list.replaceChildren();
    for (const operation of inventory.transit[category]) {
      const item = node("div", "transit-item");
      const top = node("div", "transit-item-top");
      top.append(node("strong", "", operation.sku), node("strong", "", `${number.format(operation.qty)} шт.`));
      const journey = node("p", "transit-item-route", `${operation.source_name} → ${operation.destination_name}`);
      item.append(top, journey, statusBadge(operation.status), shortId(operation.id));
      list.append(item);
    }
    if (!inventory.transit[category].length) list.append(node("p", "transit-empty", "Сейчас нет товара в пути"));
  }
  updateForms(inventory);
  renderWarehouses(inventory.warehouses);
}

function actionButton(operation, label, suffix) {
  const button = node("button", `button small-button ${suffix === "cancel" ? "danger" : "secondary"}`, label);
  button.type = "button";
  button.setAttribute("aria-label", `${label} · ${kindLabels[operation.kind]} ${operation.sku} · ${operation.id.slice(0, 8)}`);
  button.disabled = pendingOperations.has(operation.id);
  const category = operation.kind === "transfer" ? "transfers" : operation.kind;
  button.addEventListener("click", async () => {
    if (pendingOperations.has(operation.id)) return;
    pendingOperations.add(operation.id);
    updateOperationAvailability();
    feedback($("#operations-error"));
    feedback($("#operations-status"));
    try {
      const result = await post(`/api/${category}/${operation.id}/${suffix}`);
      feedback($("#operations-status"), `${kindLabels[result.kind]} · ${result.sku}: ${stateLabels[result.status][0].toLowerCase()}.`);
      await refreshAfterMutation();
    } catch (error) {
      feedback($("#operations-error"), error.message);
    } finally {
      pendingOperations.delete(operation.id);
      updateOperationAvailability();
    }
  });
  return button;
}

function updateOperationAvailability() {
  $$('#operations-body tr[data-operation-id]').forEach(row => {
    $$('button', row).forEach(button => {button.disabled = pendingOperations.has(row.dataset.operationId);});
  });
}

function renderOperations(operations) {
  $("#operation-count").textContent = `${operations.length} операций`;
  const body = $("#operations-body");
  body.replaceChildren();
  for (const operation of operations) {
    const row = node("tr");
    row.dataset.operationId = operation.id;
    const name = node("div", "", kindLabels[operation.kind]);
    name.append(shortId(operation.id));
    cell(row, name);
    const product = node("div", "", operation.product_name);
    product.append(node("span", "sku", operation.sku));
    cell(row, product);
    cell(row, number.format(operation.qty), "numeric");
    cell(row, route(operation.source_name, operation.destination_name));
    cell(row, statusBadge(operation.status));
    const actions = node("div", "actions");
    if (operation.kind === "transfer" && operation.status === "draft") actions.append(actionButton(operation, "Отправить", "dispatch"));
    if (operation.kind === "transfer" && operation.status === "in_transit") actions.append(actionButton(operation, "Принять", "receive"));
    if (operation.kind === "fbs" && operation.status === "reserved") {
      actions.append(actionButton(operation, "Отправить", "ship"), actionButton(operation, "Отменить", "cancel"));
    }
    if (operation.kind === "fbo" && operation.status === "in_transit") actions.append(actionButton(operation, "Принять FBO", "receive"));
    if (!actions.children.length) actions.append(node("span", "action-note", operation.status === "shipped" ? "Едет клиенту" : "Завершено"));
    cell(row, actions);
    body.append(row);
  }
  if (!operations.length) emptyRow(body, 6, "Операций пока нет");
}

function warehouseErrorMessage(error) {
  return error.message + (error.uncertain
    ? ' Результат изменения склада не подтверждён. Нажмите «Обновить данные» и проверьте список складов, прежде чем повторять действие.' : '');
}

async function warehouseMutation(url, method, payload, success, onSuccess = () => {}) {
  feedback($("#warehouses-error"));
  feedback($("#warehouses-status"));
  try {
    const result = await api(url, {
      method, headers: {"Content-Type": "application/json"},
      ...(payload ? {body: JSON.stringify(payload)} : {})
    });
    onSuccess(result);
    feedback($("#warehouses-status"), success);
    await refreshAfterMutation();
  } catch (error) {
    feedback($("#warehouses-error"), warehouseErrorMessage(error));
  }
}

function updateWarehouseAvailability() {
  $$('#warehouse-list [data-warehouse-id]').forEach(card => {
    $$('input, button', card).forEach(control => {control.disabled = pendingWarehouses.has(card.dataset.warehouseId);});
  });
}

function renderWarehouses(warehouses) {
  const list = $("#warehouse-list");
  list.replaceChildren();
  for (const warehouse of warehouses) {
    const card = node("article", `warehouse-item${warehouse.archived ? " archived" : ""}`);
    card.dataset.warehouseId = warehouse.id;
    const heading = node("div", "warehouse-item-heading");
    heading.append(node("h3", "", warehouse.name), node("span", `badge ${warehouse.archived ? "archive" : "complete"}`, warehouse.archived ? "Архив" : "Активен"));
    const form = node("form", "warehouse-edit");
    const input = node("input");
    input.type = "text";
    input.value = warehouseDrafts.has(warehouse.id) ? warehouseDrafts.get(warehouse.id) : warehouse.name;
    input.addEventListener("input", () => {warehouseDrafts.set(warehouse.id, input.value);});
    input.required = true;
    input.maxLength = 200;
    input.id = `warehouse-rename-${warehouse.id}`;
    const label = node("label", "sr-only", `Новое имя ФФ «${warehouse.name}»`);
    label.htmlFor = input.id;
    const save = node("button", "button secondary small-button", "Сохранить имя");
    save.type = "submit";
    form.append(label, input, save);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (pendingWarehouses.has(warehouse.id)) return;
      pendingWarehouses.add(warehouse.id);
      updateWarehouseAvailability();
      try {
        await warehouseMutation(`/api/warehouses/${warehouse.id}`, "PATCH", {name: input.value.trim()}, "Название склада сохранено. Снимки имён в истории не изменены.", () => {
          warehouseDrafts.delete(warehouse.id);
        });
      } finally {
        pendingWarehouses.delete(warehouse.id);
        updateWarehouseAvailability();
      }
    });
    const actions = node("div", "warehouse-actions");
    const identity = node("code", "", warehouse.id.slice(0, 8));
    identity.title = `Постоянный ID: ${warehouse.id}`;
    actions.append(identity);
    if (!warehouse.archived) {
      const archive = node("button", "button danger small-button", "Архивировать");
      archive.type = "button";
      archive.setAttribute("aria-label", `Архивировать ФФ «${warehouse.name}»`);
      const confirmation = node("div", "archive-confirmation");
      confirmation.hidden = true;
      confirmation.setAttribute("role", "group");
      confirmation.setAttribute("aria-label", `Подтверждение архивации ФФ «${warehouse.name}»`);
      const explanation = node("p", "", `Архивировать ФФ «${warehouse.name}»? Товар и история сохранятся. Новые приходы, резервы, FBO и входящие перемещения будут запрещены.`);
      const confirmActions = node("div", "actions");
      const confirm = node("button", "button danger small-button", "Подтвердить архивацию");
      confirm.type = "button";
      const cancel = node("button", "button secondary small-button", "Отмена");
      cancel.type = "button";
      confirmActions.append(confirm, cancel);
      confirmation.append(explanation, confirmActions);
      archive.addEventListener("click", () => {
        confirmation.hidden = false;
        archive.hidden = true;
        confirm.focus();
      });
      cancel.addEventListener("click", () => {
        confirmation.hidden = true;
        archive.hidden = false;
        archive.focus();
      });
      confirm.addEventListener("click", async () => {
        if (pendingWarehouses.has(warehouse.id)) return;
        pendingWarehouses.add(warehouse.id);
        updateWarehouseAvailability();
        try {
          await warehouseMutation(`/api/warehouses/${warehouse.id}`, "DELETE", null, "Склад архивирован. Остатки и история сохранены.");
        } finally {
          pendingWarehouses.delete(warehouse.id);
          updateWarehouseAvailability();
        }
      });
      actions.append(archive);
      card.append(confirmation);
    }
    card.prepend(heading, form, actions);
    if (warehouse.archived) card.append(node("p", "", "Можно завершить начатые операции и вывести товар перемещением."));
    list.append(card);
  }
  if (!warehouses.length) list.append(node("p", "muted", "Создайте первый склад или загрузите демонстрационные данные."));
  updateWarehouseAvailability();
}

function renderHistory(events) {
  const body = $("#history-body");
  body.replaceChildren();
  for (const event of events) {
    const row = node("tr");
    const time = node("time", "", dates.format(new Date(event.created_at)));
    time.dateTime = event.created_at;
    time.title = `UTC: ${new Date(event.created_at).toISOString()}`;
    cell(row, time, "time-cell");
    cell(row, eventLabels[event.event_type] || event.event_type);
    cell(row, event.sku);
    cell(row, number.format(event.qty), "numeric");
    cell(row, route(event.from_name, event.to_name));
    const identity = node("div");
    identity.append(statusBadge(event.operation_status), shortId(event.operation_id));
    cell(row, identity);
    body.append(row);
  }
  if (!events.length) emptyRow(body, 6, "История появится после первой операции");
}

async function loadDashboard() {
  const version = ++latestLoad;
  $("#refresh-button").disabled = true;
  feedback($("#global-error"));
  try {
    const [inventory, operations, history] = await Promise.all([
      api("/api/stocks"), api("/api/operations"), api("/api/history")
    ]);
    if (version !== latestLoad) return;
    renderInventory(inventory);
    renderOperations(operations);
    renderHistory(history);
    $("#updated-at").textContent = `Обновлено ${dates.format(new Date())}`;
  } catch (error) {
    if (version !== latestLoad) return;
    throw error;
  } finally {
    if (version === latestLoad) $("#refresh-button").disabled = false;
  }
}

async function refreshAfterMutation() {
  try {
    await loadDashboard();
  } catch (error) {
    feedback($("#global-error"), `Операция выполнена, но обновить панель не удалось. Нажмите «Обновить данные». ${error.message}`);
  }
}

function readSubmission(form) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`checkstock:${form.id}`));
    if (!saved || typeof saved.key !== "string" || !saved.key.trim() || saved.key.length > 200) return null;
    // Recover older signature-only records without replacing their operation key.
    const payload = saved.payload || JSON.parse(saved.signature);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
    const controls = $$('input[name], select[name]', form);
    if (Object.keys(payload).length !== controls.length) return null;
    for (const control of controls) {
      const value = payload[control.name];
      if (control.name === "qty") {
        if (!Number.isInteger(value) || value < 1 || value > 2147483647) return null;
      } else if (typeof value !== "string" || !value.trim()) return null;
    }
    const labels = {};
    for (const control of controls) {
      if (typeof saved.labels?.[control.name] === "string") labels[control.name] = saved.labels[control.name];
    }
    return {payload, key: saved.key, labels};
  } catch (_) {return null;}
}

function rememberSubmission(form, value) {
  // sessionStorage also preserves the key if the user reloads after losing a response.
  form.submission = value;
  try {
    if (value) sessionStorage.setItem(`checkstock:${form.id}`, JSON.stringify(value));
    else sessionStorage.removeItem(`checkstock:${form.id}`);
  } catch (_) { /* The in-memory copy still supports retry if storage is unavailable. */ }
}

function operationPayload(form) {
  const result = {};
  for (const [key, value] of new FormData(form)) result[key] = key === "qty" ? Number(value) : value.trim();
  return result;
}

function newOperationKey() {
  try {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    // getRandomValues also works on HTTP origins without randomUUID.
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, "0"));
    return [hex.slice(0, 4), hex.slice(4, 6), hex.slice(6, 8), hex.slice(8, 10), hex.slice(10)]
      .map(part => part.join("")).join("-");
  } catch (_) {
    throw new Error("Не удалось создать ключ операции. Обновите страницу и повторите попытку.");
  }
}

function newSubmission(form) {
  const labels = {};
  $$('select', form).forEach(select => {labels[select.name] = select.selectedOptions[0]?.textContent || select.value;});
  return {payload: operationPayload(form), key: newOperationKey(), labels};
}

function checkOperationResponse(form, operation) {
  const kinds = {"receipt-form": "receipt", "transfer-form": "transfer", "fbs-form": "fbs", "fbo-form": "fbo"};
  if (!operation || typeof operation.id !== "string" || !operation.id
      || typeof operation.sku !== "string" || operation.kind !== kinds[form.id]
      || !Object.hasOwn(stateLabels, operation.status)
      || operation.idempotency_key !== form.submission.key) {
    const error = new Error("Результат запроса не удалось подтвердить. Повторите запрос, чтобы проверить операцию.");
    error.uncertain = true;
    throw error;
  }
}

$$('[data-operation-form] select').forEach(select => {
  select.addEventListener("change", () => {
    if (select.value) delete select.dataset.selectionRequired;
    updateFormAvailability();
  });
});

$$('[data-operation-form]').forEach(form => {
  form.submitLabel = $('button[type="submit"]', form).textContent;
  form.submission = readSubmission(form);
  if (form.submission) {
    feedback($('.form-error', form), "Результат предыдущего запроса не подтверждён. Исходные данные сохранены. Нажмите «Повторить запрос»; новая операция не будет создана повторно.");
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (form.dataset.pending === "true") return;
    if (!form.submission && !form.reportValidity()) return;
    form.dataset.pending = "true";
    feedback($('.form-error', form));
    feedback($('.form-status', form));
    try {
      if (!form.submission) rememberSubmission(form, newSubmission(form));
      const payload = {...form.submission.payload, idempotency_key: form.submission.key};
      updateFormAvailability();
      const operation = await post(form.getAttribute("action"), payload);
      checkOperationResponse(form, operation);
      rememberSubmission(form, null);
      // A fresh quantity makes the next create an explicit new operation.
      $('input[name="qty"]', form).value = "";
      feedback($('.form-status', form), `${kindLabels[operation.kind]} · ${operation.sku}: ${stateLabels[operation.status][0].toLowerCase()}. ID ${operation.id.slice(0, 8)}.`);
      await refreshAfterMutation();
    } catch (error) {
      if (error.status >= 400 && error.status < 500) rememberSubmission(form, null);
      feedback($('.form-error', form), error.message + (form.submission
        ? ' Исходные данные сохранены. Нажмите «Повторить запрос», чтобы безопасно получить результат.' : ''));
    } finally {
      form.dataset.pending = "false";
      if (currentInventory) updateForms(currentInventory);
      else updateFormAvailability();
    }
  });
});

updateFormAvailability();

$("#warehouse-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  if (button.disabled) return;
  button.disabled = true;
  feedback($('.form-error', form));
  feedback($('.form-status', form));
  try {
    const warehouse = await post(form.getAttribute("action"), {name: $('#warehouse-name').value.trim()});
    $('#warehouse-name').value = "";
    feedback($('.form-status', form), `ФФ «${warehouse.name}» создан.`);
    await refreshAfterMutation();
  } catch (error) {
    feedback($('.form-error', form), warehouseErrorMessage(error));
  } finally {button.disabled = false;}
});

$("#refresh-button").addEventListener("click", () => {
  loadDashboard().catch(error => feedback($("#global-error"), error.message));
});
$("#history-timezone").textContent = `Время устройства (${Intl.DateTimeFormat().resolvedOptions().timeZone}); UTC — в подсказке.`;
loadDashboard().catch(error => {
  $("#updated-at").textContent = "Данные не загружены";
  feedback($("#global-error"), error.message);
});
