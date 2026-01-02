// Логика режима мастера вынесена в отдельный файл,
// чтобы основной HTML не разрастался и был легче для чтения.
(() => {
  const API_MASTER_LOGIN_URL = "/api/kiosk/master/login";
  const API_MASTER_LOGOUT_URL = "/api/kiosk/master/logout";
  const API_SETTINGS_URL = "/api/kiosk/settings";
  const API_SKU_URL = "/api/kiosk/sku";
  const API_SKU_CATALOG_URL = "/api/kiosk/sku_catalog";
  const API_REPORT_PREVIEW_URL = "/api/kiosk/reports/preview";
  const API_REPORT_EXPORT_URL = "/api/kiosk/reports/export";
  const API_REPORT_USB_URL = "/api/kiosk/reports/save_to_usb";
  const API_REPORT_SHIFT_CSV_URL = "/api/kiosk/reports/shift.csv";
  const API_REPORT_WORKERS_CSV_URL = "/api/kiosk/reports/workers.csv";
  const API_SHIFT_PLAN_IMPORT_URL = "/api/kiosk/shift_plan/import";

  // UI-элементы мастера: кнопки, модалка, статус.
  const btnMasterLogin = document.getElementById("btnMasterLogin");
  const btnMasterLogout = document.getElementById("btnMasterLogout");
  const masterStatus = document.getElementById("masterStatus");

  const masterLoginBackdrop = document.getElementById("masterLoginBackdrop");
  const masterLoginInput = document.getElementById("masterLoginInput");
  const masterLoginHint = document.getElementById("masterLoginHint");
  const masterLoginActions = document.getElementById("masterLoginActions");
  const masterLoginCancel = document.getElementById("masterLoginCancel");

  // Вкладка "Управление" доступна только мастеру.
  const tabbar = document.getElementById("mainTabbar");
  const masterOnlyElements = Array.from(document.querySelectorAll(".master-only"));

  // Чекбоксы настроек.
  const settingCanReorder = document.getElementById("allowReorderQueue");
  const settingCanEditQty = document.getElementById("allowChangeQty");
  const settingCanRemoveSku = document.getElementById("allowRemoveSku");
  const settingCanAddSku = document.getElementById("allowAddFromCatalog");
  const settingCanManualMode = document.getElementById("allowManualMode");
  const settingCanSkipSku = document.getElementById("allowSkipSku");
  const settingMasterTimeout = document.getElementById("settingMasterTimeout");
  const btnSettingsSave = document.getElementById("btnSettingsSave");
  const btnMasterLogoutSettings = document.getElementById("btnMasterLogoutSettings");
  const settingsHint = document.getElementById("settingsHint");

  // Каталог SKU: элементы управления и модалка.
  const skuCatalogList = document.getElementById("skuCatalogList");
  const skuCatalogSearch = document.getElementById("skuCatalogSearch");
  const btnSkuAdd = document.getElementById("btnSkuAdd");
  const skuCatalogModalBackdrop = document.getElementById("skuCatalogModalBackdrop");
  const skuCatalogModalTitle = document.getElementById("skuCatalogModalTitle");
  const skuCatalogModalActions = document.getElementById("skuCatalogModalActions");
  const skuCatalogModalCancel = document.getElementById("skuCatalogModalCancel");
  const skuModelCode = document.getElementById("skuModelCode");
  const skuWidthCm = document.getElementById("skuWidthCm");
  const skuFabricCode = document.getElementById("skuFabricCode");
  const skuColorCode = document.getElementById("skuColorCode");
  const skuName = document.getElementById("skuName");
  const skuIsActive = document.getElementById("skuIsActive");
  const skuPreviewValue = document.getElementById("skuPreviewValue");

  // Отчёты: элементы управления и контейнер предпросмотра.
  const reportType = document.getElementById("reportType");
  const reportDateFrom = document.getElementById("reportDateFrom");
  const reportDateTo = document.getElementById("reportDateTo");
  const btnReportPreview = document.getElementById("btnReportPreview");
  const reportsPreview = document.getElementById("reportsPreview");
  const btnReportDownloadCsv = document.getElementById("btnReportDownloadCsv");
  const btnReportDownloadXlsx = document.getElementById("btnReportDownloadXlsx");
  const btnReportUsbCsv = document.getElementById("btnReportUsbCsv");
  const btnReportUsbXlsx = document.getElementById("btnReportUsbXlsx");
  const reportDateCsv = document.getElementById("reportDateCsv");
  const btnReportShiftCsv = document.getElementById("btnReportShiftCsv");
  const btnReportWorkersCsv = document.getElementById("btnReportWorkersCsv");

  let masterModalOpen = false;
  let currentMasterId = null;
  // Единственный источник истины для мастер-режима на фронте.
  // По умолчанию он выключен, чтобы не показывать лишние вкладки оператору.
  let uiMasterActive = false;
  let masterTimeoutId = null;
  let skuModalOpen = false;
  let skuModalMode = "create";
  let skuEditingId = null;
  // Учительская заметка: каталог SKU нужен всему UI, поэтому держим его в window.
  window.kzSkuCatalog = Array.isArray(window.kzSkuCatalog) ? window.kzSkuCatalog : [];

  function normalizeSkuCode(value) {
    /**
     * Пояснение учителя: приводим SKU к строке,
     * чтобы сравнение не ломалось из-за null/undefined.
     */
    return (value || "").toString().trim();
  }

  window.findSku = (code) => {
    /**
     * Учительская подсказка: быстрый поиск SKU по точному совпадению.
     */
    const normalized = normalizeSkuCode(code);
    if (!normalized) return null;
    return (window.kzSkuCatalog || []).find((item) => item.sku_code === normalized) || null;
  };

  window.filterSku = (query) => {
    /**
     * Учительская подсказка: фильтруем по коду и названию,
     * чтобы поиск работал и по SKU, и по человекочитаемому названию.
     */
    const needle = normalizeSkuCode(query).toLowerCase();
    const catalog = (window.kzSkuCatalog || []).filter((item) => item.is_active !== false);
    if (!needle) return catalog;
    return catalog.filter((item) => {
      const code = normalizeSkuCode(item.sku_code).toLowerCase();
      const name = normalizeSkuCode(item.name).toLowerCase();
      return code.includes(needle) || name.includes(needle);
    });
  };

  async function loadSkuCatalog() {
    /**
     * Учительская подсказка: загружаем каталог SKU без мастер-режима,
     * чтобы очередь сразу показывала актуальные данные.
     */
    try {
      const resp = await fetch(API_SKU_CATALOG_URL, { cache: "no-store" });
      if (!resp.ok) {
        return;
      }
      const data = await resp.json();
      window.kzSkuCatalog = Array.isArray(data.items) ? data.items : [];
      if (typeof window.onSkuCatalogLoaded === "function") {
        window.onSkuCatalogLoaded(window.kzSkuCatalog);
      }
    } catch (error) {
      console.warn("Не удалось загрузить каталог SKU:", error);
    }
  }
  // Учительская подсказка: отдаём функцию наружу, чтобы UI мог обновлять каталог при необходимости.
  window.loadSkuCatalog = loadSkuCatalog;

  function setMasterUi(masterId) {
    /**
     * Обновляем строку статуса мастера и кнопку выхода.
     *
     * Почему так:
     * - оператор сразу видит, кто вошёл в режим мастера;
     * - кнопка "Выйти" появляется только при активном режиме,
     *   чтобы не путать обычного пользователя.
     */
    if (masterStatus) {
      masterStatus.textContent = masterId ? `Мастер: ${masterId}` : "Мастер: —";
    }
    if (btnMasterLogout) {
      btnMasterLogout.classList.toggle("master-hidden", !masterId);
    }
    if (btnMasterLogoutSettings) {
      btnMasterLogoutSettings.classList.toggle("master-hidden", !masterId);
    }
    currentMasterId = masterId || null;
    uiMasterActive = !!currentMasterId;
    document.body.classList.toggle("is-master-active", uiMasterActive);
    refreshMasterTimeout();
  updateSettingsAvailability();
  document.body.classList.toggle("is-master-active", uiMasterActive);
  renderTabs();
    if (masterId) {
      fetchSkuCatalog();
    } else {
      clearSkuCatalog();
    }
  }

  window.syncMasterState = (masterId) => {
    /**
     * Синхронизируем kiosk.js с backend-статусом мастера.
     *
     * Это важно для сценария авто-таймаута:
     * backend сбросил master_id, UI должен отключить чекбоксы.
     */
    currentMasterId = masterId || null;
    uiMasterActive = !!currentMasterId;
    document.body.classList.toggle("is-master-active", uiMasterActive);
    refreshMasterTimeout();
    updateSettingsAvailability();
    renderTabs();
    if (currentMasterId) {
      fetchSkuCatalog();
    } else {
      clearSkuCatalog();
    }
  };

  function refreshMasterTimeout() {
    /**
     * Таймер мастер-режима живёт на фронте только для UX.
     *
     * Почему так:
     * - backend всё равно контролирует таймаут;
     * - мы делаем UI быстрее и понятнее (вкладки скрываются вовремя).
     */
    if (masterTimeoutId) {
      clearTimeout(masterTimeoutId);
      masterTimeoutId = null;
    }
    if (!uiMasterActive) {
      localStorage.removeItem("kiosk_master_active");
      sessionStorage.removeItem("kiosk_master_active");
      return;
    }
    const timeoutMinutes = parseInt(settingMasterTimeout?.value || "15", 10);
    const safeMinutes = Number.isNaN(timeoutMinutes) ? 15 : Math.max(1, Math.min(timeoutMinutes, 240));
    masterTimeoutId = setTimeout(() => {
      // По таймауту делаем мягкий выход: UI сбрасывается и просит мастера войти снова.
      logoutMaster("timeout");
    }, safeMinutes * 60 * 1000);
  }

  let activeTabId = "tab-operator";

  function setActiveTab(tabId) {
    /**
     * Меняем активную вкладку и показываем нужный экран.
     */
    activeTabId = tabId;
    document.querySelectorAll(".screen").forEach((screen) => {
      screen.dataset.active = screen.id === tabId ? "true" : "false";
    });
    renderTabs();
  }

  function renderTabs() {
    /**
     * Рисуем вкладки единым способом.
     *
     * Почему через JS:
     * - мастер-вкладки можно скрывать без дублирования разметки;
     * - активная вкладка подсвечивается как "зелёная пилюля".
     */
    if (!tabbar) return;
    const isMaster = uiMasterActive;
    const tabs = [
      { id: "tab-operator", label: "Оператор" },
      { id: "tab-queue", label: "Очередь" },
    ];
    if (isMaster) {
      tabs.push({ id: "tab-admin", label: "Управление", master: true });
      tabs.push({ id: "tab-reports", label: "Отчёты", master: true });
    }
    tabs.push({ id: "tab-stats", label: "Статистика" });

    if (!isMaster && (activeTabId === "tab-admin" || activeTabId === "tab-reports")) {
      activeTabId = "tab-operator";
    }

    tabbar.innerHTML = "";
    tabs.forEach((tab) => {
      const btn = document.createElement("div");
      btn.className = "pill-btn tab";
      if (tab.master) {
        btn.classList.add("tab--master");
      }
      if (tab.id === activeTabId) {
        btn.classList.add("tab--active", "pill-btn--active");
      }
      btn.setAttribute("role", "button");
      btn.setAttribute("tabindex", "0");
      btn.dataset.target = tab.id;
      btn.innerHTML = `<span class="dot"></span><span>${tab.label}</span>`;
      btn.addEventListener("click", () => setActiveTab(tab.id));
      tabbar.appendChild(btn);
    });

    masterOnlyElements.forEach((el) => {
      el.classList.toggle("master-only-hidden", !isMaster);
    });
  }

  function updateSettingsAvailability() {
    /**
     * Блокируем/разблокируем чекбоксы настроек.
     *
     * Логика простая:
     * - если мастер не вошёл, менять права нельзя;
     * - UI остаётся читаемым, но с подсказкой, почему он заблокирован.
     */
    const enabled = !!currentMasterId;
    if (settingCanReorder) {
      settingCanReorder.disabled = !enabled;
    }
    if (settingCanEditQty) {
      settingCanEditQty.disabled = !enabled;
    }
    if (settingCanAddSku) {
      settingCanAddSku.disabled = !enabled;
    }
    if (settingCanRemoveSku) {
      settingCanRemoveSku.disabled = !enabled;
    }
    if (settingCanManualMode) {
      settingCanManualMode.disabled = !enabled;
    }
    if (settingCanSkipSku) {
      settingCanSkipSku.disabled = !enabled;
    }
    if (settingMasterTimeout) {
      settingMasterTimeout.disabled = !enabled;
    }
    if (btnSettingsSave) {
      btnSettingsSave.classList.toggle("pill-btn--disabled", !enabled);
    }
    if (settingsHint) {
      settingsHint.textContent = enabled
        ? "Изменения применяются сразу и сохраняются в базе."
        : "Настройки доступны только мастеру. Перед изменением войдите как мастер.";
    }
  }

  window.importShiftPlanFile = async (file) => {
    /**
     * Импорт сменного задания в мастер-режиме (CSV или JSON).
     *
     * Мы отправляем файл на backend и показываем итог:
     * - успех: "Импортировано N позиций";
     * - ошибка: список из первых 10 ошибок + "…";
     * - отсутствие python-multipart: отдельное сообщение.
     */
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const resp = await fetch(API_SHIFT_PLAN_IMPORT_URL, {
        method: "POST",
        body: formData,
      });

      if (resp.status === 501) {
        window.showPackToast?.("Импорт файлов недоступен: нужен python-multipart");
        return;
      }

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const errors = Array.isArray(data.errors) ? data.errors : [];
        if (errors.length) {
          const shown = errors.slice(0, 10).map((err) => String(err));
          let message = shown.join("; ");
          if (errors.length > 10) {
            message += "…";
          }
          window.showPackToast?.(`Импорт отменён: ${message}`);
        } else {
          window.showPackToast?.(data.detail || "Импорт не выполнен.");
        }
        return;
      }

      const importedCount = Number.isFinite(data.total_items) ? data.total_items : 0;
      window.showPackToast?.(`Импортировано ${importedCount} позиций`);

      if (typeof window.refreshShiftPlanFromBackend === "function") {
        await window.refreshShiftPlanFromBackend();
      } else if (typeof window.syncQueueFromBackend === "function") {
        await window.syncQueueFromBackend();
      }
    } catch (error) {
      window.showPackToast?.("Ошибка сети: файл не импортирован.");
    }
  };

  function clearSkuCatalog() {
    /**
     * Очищаем список SKU, если мастер-режим выключен.
     *
     * Это не даёт оператору видеть каталог, который доступен только мастеру.
     */
    if (skuCatalogList) {
      skuCatalogList.innerHTML = "";
    }
  }

  function applySettingsToUi(settings) {
    /**
     * Синхронизируем чекбоксы с настройками backend.
     *
     * Зачем:
     * - UI показывает реальные права оператора;
     * - любые изменения подтягиваются даже после перезагрузки.
     */
    if (settingCanReorder) {
      settingCanReorder.checked = !!settings.operator_can_reorder;
    }
    if (settingCanEditQty) {
      settingCanEditQty.checked = !!settings.operator_can_edit_qty;
    }
    if (settingCanAddSku) {
      settingCanAddSku.checked = !!settings.operator_can_add_sku_to_shift;
    }
    if (settingCanRemoveSku) {
      settingCanRemoveSku.checked = !!settings.operator_can_remove_sku_from_shift;
    }
    if (settingCanManualMode) {
      settingCanManualMode.checked = !!settings.operator_can_manual_mode;
    }
    if (settingCanSkipSku) {
      // Пока backend не хранит этот флаг, оставляем false и явно показываем TODO.
      // TODO: добавить operator_can_skip_sku в настройках backend.
      settingCanSkipSku.checked = !!settings.operator_can_skip_sku;
    }
    if (settingMasterTimeout) {
      settingMasterTimeout.value = String(settings.master_session_timeout_min || 15);
    }
    if (window.applyOperatorSettings) {
      window.applyOperatorSettings({
        allow_reorder_queue: !!settings.operator_can_reorder,
        allow_change_qty: !!settings.operator_can_edit_qty,
        allow_add_from_catalog: !!settings.operator_can_add_sku_to_shift,
        allow_remove_sku: !!settings.operator_can_remove_sku_from_shift,
        allow_manual_mode: !!settings.operator_can_manual_mode,
        allow_skip_sku: !!settings.operator_can_skip_sku,
      });
    }
  }

  async function fetchSettings() {
    /**
     * Загружаем настройки из backend.
     *
     * Важно: не кидаем исключения наружу, чтобы UI не ломался при сетевых сбоях.
     */
    try {
      const resp = await fetch(API_SETTINGS_URL, { cache: "no-store" });
      if (!resp.ok) {
        return;
      }
      const data = await resp.json();
      if (data && data.settings) {
        applySettingsToUi(data.settings);
      }
      if (data && data.master_mode && data.master_id) {
        setMasterUi(data.master_id);
      }
    } catch (error) {
      // Молча игнорируем сетевые ошибки, чтобы не мешать оператору.
    }
  }

  async function saveSettings() {
    /**
     * Отправляем текущие настройки на backend.
     *
     * Если backend вернул ошибку, возвращаем прежние значения,
     * чтобы UI не показывал неверное состояние.
     */
    const timeoutValue = parseInt(settingMasterTimeout?.value || "15", 10);
    if (Number.isNaN(timeoutValue) || timeoutValue < 1 || timeoutValue > 240) {
      window.showPackToast?.("Таймаут мастера должен быть от 1 до 240 минут.");
      return;
    }
    const payload = {
      operator_can_reorder: !!settingCanReorder?.checked,
      operator_can_edit_qty: !!settingCanEditQty?.checked,
      operator_can_add_sku_to_shift: !!settingCanAddSku?.checked,
      operator_can_remove_sku_from_shift: !!settingCanRemoveSku?.checked,
      operator_can_manual_mode: !!settingCanManualMode?.checked,
      // TODO: добавить operator_can_skip_sku в backend и сохранять его здесь.
    master_session_timeout_min: timeoutValue,
    };
    try {
      const resp = await fetch(API_SETTINGS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        window.showPackToast?.(detail.detail || "Не удалось сохранить настройки.");
        await fetchSettings();
        return;
      }
      const data = await resp.json();
      if (data && data.settings) {
        applySettingsToUi(data.settings);
      }
    } catch (error) {
      window.showPackToast?.("Ошибка сети: настройки не сохранены.");
      await fetchSettings();
    }
  }

  function buildSkuPreview() {
    /**
     * Собираем SKU в простом формате без сложных шаблонов.
     *
     * Это MVP: показываем мастеру наглядный код без хитрых правил.
     */
    const model = (skuModelCode?.value || "").trim();
    const width = (skuWidthCm?.value || "").trim();
    const fabric = (skuFabricCode?.value || "").trim();
    const color = (skuColorCode?.value || "").trim();
    const parts = [model, width, fabric, color].filter(Boolean);
    const result = parts.join("-");
    if (skuPreviewValue) {
      skuPreviewValue.textContent = result || "—";
    }
    return result;
  }

  function setSkuFormDisabled(disabled) {
    /**
     * При редактировании блокируем поля, которые нельзя менять.
     *
     * Так мы защищаем sku_code от случайного изменения.
     */
    const fields = [skuModelCode, skuWidthCm, skuFabricCode, skuColorCode];
    fields.forEach((field) => {
      if (!field) return;
      field.disabled = disabled;
    });
  }

  function openSkuModal(mode, item = null) {
    skuModalOpen = true;
    skuModalMode = mode;
    skuEditingId = item ? item.id : null;
    if (skuCatalogModalBackdrop) {
      skuCatalogModalBackdrop.classList.add("open");
      skuCatalogModalBackdrop.setAttribute("aria-hidden", "false");
    }
    if (skuCatalogModalTitle) {
      skuCatalogModalTitle.textContent = mode === "edit" ? "Редактировать SKU" : "Добавить SKU";
    }
    if (skuCatalogModalActions) {
      skuCatalogModalActions.innerHTML = "";
      skuCatalogModalActions.appendChild(
        makeCatalogActionButton("Сохранить", "success", () => saveSkuModal())
      );
    }
    if (mode === "edit" && item) {
      if (skuModelCode) skuModelCode.value = item.model_code || "";
      if (skuWidthCm) skuWidthCm.value = String(item.width_cm || "");
      if (skuFabricCode) skuFabricCode.value = item.fabric_code || "";
      if (skuColorCode) skuColorCode.value = item.color_code || "";
      if (skuName) skuName.value = item.name || "";
      if (skuIsActive) skuIsActive.checked = !!item.is_active;
      setSkuFormDisabled(true);
      if (skuPreviewValue) {
        skuPreviewValue.textContent = item.sku_code || "—";
      }
    } else {
      if (skuModelCode) skuModelCode.value = "";
      if (skuWidthCm) skuWidthCm.value = "";
      if (skuFabricCode) skuFabricCode.value = "";
      if (skuColorCode) skuColorCode.value = "";
      if (skuName) skuName.value = "";
      if (skuIsActive) skuIsActive.checked = true;
      setSkuFormDisabled(false);
      buildSkuPreview();
    }
  }

  function closeSkuModal() {
    skuModalOpen = false;
    if (skuCatalogModalBackdrop) {
      skuCatalogModalBackdrop.classList.remove("open");
      skuCatalogModalBackdrop.setAttribute("aria-hidden", "true");
    }
  }

  function makeCatalogActionButton(text, kind, onClick) {
    /**
     * Кнопки модалки — такие же pill-кнопки, чтобы стиль был единым.
     */
    const btn = document.createElement("div");
    btn.className = "pill-btn";
    if (kind === "danger") {
      btn.classList.add("pill-btn--danger");
    }
    if (kind === "primary" || kind === "success") {
      btn.classList.add("pill-btn--primary");
    }
    btn.innerHTML = `<span class="dot"></span><span>${text}</span>`;
    btn.addEventListener("click", () => onClick());
    return btn;
  }

  async function fetchSkuCatalog() {
    /**
     * Загружаем список SKU (для мастера).
     *
     * Мы включаем неактивные записи, чтобы можно было ими управлять.
     */
    if (!currentMasterId) return;
    const query = (skuCatalogSearch?.value || "").trim();
    const url = new URL(API_SKU_URL, window.location.origin);
    if (query) {
      url.searchParams.set("q", query);
    }
    url.searchParams.set("include_inactive", "true");
    try {
      const resp = await fetch(url.toString(), { cache: "no-store" });
      if (!resp.ok) return;
      const data = await resp.json();
      renderSkuCatalog(data.items || []);
    } catch (error) {
      // Сетевые ошибки не блокируют UI, просто оставляем список как есть.
    }
  }

  function getReportHeaders(type) {
    if (type === "employees") {
      return ["worker_id", "packed_count", "worktime_sec", "downtime_sec"];
    }
    if (type === "sku") {
      return ["sku", "packed_count"];
    }
    return ["shift_id", "worker_id", "start_time", "finish_time", "packed_count"];
  }

  function renderReportPreview(rows, headers) {
    if (!reportsPreview) return;
    reportsPreview.innerHTML = "";
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "settings-hint";
      empty.textContent = "Нет данных за выбранный период.";
      reportsPreview.appendChild(empty);
      return;
    }
    const table = document.createElement("table");
    table.className = "reports-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headers.forEach((header) => {
      const th = document.createElement("th");
      th.textContent = header;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      headers.forEach((header) => {
        const td = document.createElement("td");
        td.textContent = row[header] ?? "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    reportsPreview.appendChild(table);
  }

  function getReportParams() {
    return {
      type: reportType?.value || "employees",
      date_from: reportDateFrom?.value || "",
      date_to: reportDateTo?.value || "",
    };
  }

  async function fetchReportPreview() {
    /**
     * Загружаем первые 50 строк для предпросмотра.
     */
    if (!currentMasterId) return;
    const params = getReportParams();
    if (!params.date_from || !params.date_to) {
      window.showPackToast?.("Выберите период отчёта.");
      return;
    }
    const url = new URL(API_REPORT_PREVIEW_URL, window.location.origin);
    url.searchParams.set("type", params.type);
    url.searchParams.set("date_from", params.date_from);
    url.searchParams.set("date_to", params.date_to);
    try {
      const resp = await fetch(url.toString(), { cache: "no-store" });
      if (!resp.ok) {
        window.showPackToast?.("Не удалось загрузить предпросмотр.");
        return;
      }
      const data = await resp.json();
      renderReportPreview(data.rows || [], getReportHeaders(params.type));
    } catch (error) {
      window.showPackToast?.("Ошибка сети: предпросмотр не загружен.");
    }
  }

  function triggerReportDownload(format) {
    /**
     * Скачиваем отчёт через прямую ссылку, чтобы браузер сохранил файл.
     */
    if (!currentMasterId) return;
    const params = getReportParams();
    if (!params.date_from || !params.date_to) {
      window.showPackToast?.("Выберите период отчёта.");
      return;
    }
    const url = new URL(API_REPORT_EXPORT_URL, window.location.origin);
    url.searchParams.set("type", params.type);
    url.searchParams.set("date_from", params.date_from);
    url.searchParams.set("date_to", params.date_to);
    url.searchParams.set("format", format);
    window.location.href = url.toString();
  }

  function triggerSimpleCsvDownload(urlBase) {
    /**
     * Учительская подсказка: минимальный CSV скачиваем одной ссылкой.
     */
    if (!currentMasterId) return;
    const dateValue = reportDateCsv?.value || "";
    if (!dateValue) {
      window.showPackToast?.("Выберите дату отчёта.");
      return;
    }
    const url = new URL(urlBase, window.location.origin);
    url.searchParams.set("date", dateValue);
    window.location.href = url.toString();
  }

  async function saveReportToUsb(format) {
    /**
     * Просим backend сохранить отчёт на USB и возвращаем путь.
     */
    if (!currentMasterId) return;
    const params = getReportParams();
    if (!params.date_from || !params.date_to) {
      window.showPackToast?.("Выберите период отчёта.");
      return;
    }
    const payload = {
      report_type: params.type,
      date_from: params.date_from,
      date_to: params.date_to,
      format,
    };
    try {
      const resp = await fetch(API_REPORT_USB_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        window.showPackToast?.(detail.detail || "Не удалось сохранить отчёт.");
        return;
      }
      const data = await resp.json();
      window.showPackToast?.(`Файл сохранён: ${data.path}`);
    } catch (error) {
      window.showPackToast?.("Ошибка сети: отчёт не сохранён.");
    }
  }

  function setReportDefaultDates() {
    /**
     * По умолчанию ставим сегодняшний день, чтобы отчёт строился без лишних шагов.
     */
    const today = new Date().toISOString().slice(0, 10);
    if (reportDateFrom && !reportDateFrom.value) {
      reportDateFrom.value = today;
    }
    if (reportDateTo && !reportDateTo.value) {
      reportDateTo.value = today;
    }
    if (reportDateCsv && !reportDateCsv.value) {
      reportDateCsv.value = today;
    }
  }

  function renderSkuCatalog(items) {
    if (!skuCatalogList) return;
    skuCatalogList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "settings-hint";
      empty.textContent = "Пока нет SKU. Добавьте первую запись.";
      skuCatalogList.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "sku-catalog-row";

      const code = document.createElement("div");
      code.className = "sku-catalog-title";
      code.textContent = item.sku_code || "—";

      const name = document.createElement("div");
      name.className = "sku-catalog-meta";
      name.innerHTML = `<div>${item.name || "—"}</div><div>${item.model_code || ""} • ${item.width_cm || ""} см • ${item.fabric_code || ""} • ${item.color_code || ""}</div>`;

      const status = document.createElement("div");
      status.className = "sku-catalog-meta";
      status.textContent = item.is_active ? "Активен" : "Неактивен";

      const actions = document.createElement("div");
      actions.className = "sku-catalog-actions";
      const editBtn = document.createElement("div");
      editBtn.className = "pill-btn pill-btn--ghost pill-btn--mini";
      editBtn.innerHTML = "<span class=\"dot\"></span><span>Редактировать</span>";
      editBtn.addEventListener("click", () => openSkuModal("edit", item));
      actions.appendChild(editBtn);

      row.appendChild(code);
      row.appendChild(name);
      row.appendChild(status);
      row.appendChild(actions);
      skuCatalogList.appendChild(row);
    });
  }

  async function saveSkuModal() {
    /**
     * Создаём или обновляем SKU.
     *
     * В режиме редактирования меняем только имя и активность.
     */
    if (skuModalMode === "edit" && skuEditingId) {
      const payload = {
        name: (skuName?.value || "").trim(),
        is_active: !!skuIsActive?.checked,
      };
      try {
        const resp = await fetch(`${API_SKU_URL}/${skuEditingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          window.showPackToast?.("Не удалось сохранить SKU.");
          return;
        }
        closeSkuModal();
        fetchSkuCatalog();
      } catch (error) {
        window.showPackToast?.("Ошибка сети: SKU не сохранён.");
      }
      return;
    }

    const skuCode = buildSkuPreview();
    const payload = {
      sku_code: skuCode,
      name: (skuName?.value || "").trim(),
      model_code: (skuModelCode?.value || "").trim(),
      width_cm: parseInt(skuWidthCm?.value || "0", 10),
      fabric_code: (skuFabricCode?.value || "").trim(),
      color_code: (skuColorCode?.value || "").trim(),
      is_active: !!skuIsActive?.checked,
    };
    if (!payload.sku_code || !payload.name) {
      window.showPackToast?.("Заполните код SKU и название.");
      return;
    }
    try {
      const resp = await fetch(API_SKU_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        window.showPackToast?.(detail.detail || "Не удалось добавить SKU.");
        return;
      }
      closeSkuModal();
      fetchSkuCatalog();
    } catch (error) {
      window.showPackToast?.("Ошибка сети: SKU не добавлен.");
    }
  }

  function openMasterModal() {
    /**
     * Открываем модалку входа мастера.
     *
     * Мы фокусируем поле ввода, чтобы сканер сразу отправлял QR в это поле.
     */
    masterModalOpen = true;
    if (masterLoginBackdrop) {
      masterLoginBackdrop.classList.add("open");
      masterLoginBackdrop.setAttribute("aria-hidden", "false");
    }
    if (masterLoginHint) {
      masterLoginHint.textContent = "Ожидание сканирования…";
    }
    if (masterLoginInput) {
      masterLoginInput.value = "";
      masterLoginInput.focus();
    }
    if (masterLoginActions) {
      masterLoginActions.innerHTML = "";
      masterLoginActions.appendChild(makeMasterActionButton("Войти", "success", loginMaster));
    }
  }

  function closeMasterModal() {
    masterModalOpen = false;
    if (masterLoginBackdrop) {
      masterLoginBackdrop.classList.remove("open");
      masterLoginBackdrop.setAttribute("aria-hidden", "true");
    }
  }

  function makeMasterActionButton(text, kind, onClick) {
    /**
     * Кнопки в модалке должны выглядеть как остальные "пилюли".
     * Это сохраняет единый стиль и снижает когнитивную нагрузку.
     */
    const btn = document.createElement("div");
    btn.className = "pill-btn";
    if (kind === "danger") {
      btn.classList.add("pill-btn--danger");
    }
    if (kind === "primary" || kind === "success") {
      btn.classList.add("pill-btn--primary");
    }
    btn.innerHTML = `<span class="dot"></span><span>${text}</span>`;
    btn.setAttribute("role", "button");
    btn.setAttribute("tabindex", "0");
    btn.addEventListener("click", onClick);
    return btn;
  }

  async function loginMaster() {
    /**
     * Отправляем QR на backend и включаем режим мастера.
     *
     * Если QR неверный — показываем понятное сообщение,
     * чтобы оператор сразу понял, что нужно пересканировать.
     */
    const qrText = (masterLoginInput?.value || "").trim();
    if (!qrText) {
      if (masterLoginHint) {
        masterLoginHint.textContent = "Введите или отсканируйте QR-код мастера.";
      }
      return;
    }

    try {
      const resp = await fetch(API_MASTER_LOGIN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qr_text: qrText }),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        if (masterLoginHint) {
          masterLoginHint.textContent = detail.detail || "Не удалось войти в режим мастера.";
        }
        return;
      }
      const data = await resp.json();
      setMasterUi(data.master_id || qrText);
      await fetchSettings();
      closeMasterModal();
    } catch (error) {
      if (masterLoginHint) {
        masterLoginHint.textContent = "Ошибка сети: попробуйте ещё раз.";
      }
    }
  }

  async function logoutMaster(reason = "manual") {
    /**
     * Выход из режима мастера.
     *
     * Даже если сеть недоступна, мы показываем пользователю,
     * что режим отключается, чтобы избежать ложного чувства доступа.
     */
    try {
      await fetch(API_MASTER_LOGOUT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
    } catch (error) {
      // Мы сознательно не блокируем UI: важнее убрать доступ сразу.
    }
    setMasterUi(null);
    await fetchSettings();
  }

  if (btnMasterLogin) {
    btnMasterLogin.addEventListener("click", () => openMasterModal());
  }

  if (btnMasterLogout) {
    btnMasterLogout.addEventListener("click", () => logoutMaster());
  }

  if (masterLoginCancel) {
    masterLoginCancel.addEventListener("click", () => closeMasterModal());
  }

  if (masterLoginInput) {
    masterLoginInput.addEventListener("keydown", (event) => {
      // Сканер часто отправляет Enter в конце строки — удобно логинить сразу.
      if (event.key === "Enter") {
        event.preventDefault();
        loginMaster();
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    // Закрываем модалку мастера по ESC, чтобы оператор мог быстро отменить вход.
    if (event.key === "Escape" && masterModalOpen) {
      closeMasterModal();
    }
    if (event.key === "Escape" && skuModalOpen) {
      closeSkuModal();
    }
  });

  // Экспортируем функцию наружу, чтобы можно было переключать вкладки из других модулей.
  window.activateMainTab = setActiveTab;

  if (settingCanReorder) {
    settingCanReorder.addEventListener("change", () => saveSettings());
  }
  if (settingCanEditQty) {
    settingCanEditQty.addEventListener("change", () => saveSettings());
  }
  if (settingCanAddSku) {
    settingCanAddSku.addEventListener("change", () => saveSettings());
  }
  if (settingCanRemoveSku) {
    settingCanRemoveSku.addEventListener("change", () => saveSettings());
  }
  if (settingCanManualMode) {
    settingCanManualMode.addEventListener("change", () => saveSettings());
  }
  if (settingCanSkipSku) {
    settingCanSkipSku.addEventListener("change", () => saveSettings());
  }
  if (btnSettingsSave) {
    btnSettingsSave.addEventListener("click", () => {
      if (btnSettingsSave.classList.contains("pill-btn--disabled")) {
        return;
      }
      saveSettings();
    });
  }
  if (btnMasterLogoutSettings) {
    btnMasterLogoutSettings.addEventListener("click", () => logoutMaster());
  }
  if (settingMasterTimeout) {
    settingMasterTimeout.addEventListener("keydown", (event) => {
      // ENTER удобно использовать как "сохранить".
      if (event.key === "Enter") {
        event.preventDefault();
        saveSettings();
      }
    });
  }

  if (skuCatalogSearch) {
    skuCatalogSearch.addEventListener("input", () => fetchSkuCatalog());
  }
  if (btnSkuAdd) {
    btnSkuAdd.addEventListener("click", () => openSkuModal("create"));
  }
  if (skuCatalogModalCancel) {
    skuCatalogModalCancel.addEventListener("click", () => closeSkuModal());
  }
  [skuModelCode, skuWidthCm, skuFabricCode, skuColorCode].forEach((field) => {
    if (!field) return;
    field.addEventListener("input", () => buildSkuPreview());
  });

  if (btnReportPreview) {
    btnReportPreview.addEventListener("click", () => fetchReportPreview());
  }
  if (btnReportDownloadCsv) {
    btnReportDownloadCsv.addEventListener("click", () => triggerReportDownload("csv"));
  }
  if (btnReportDownloadXlsx) {
    btnReportDownloadXlsx.addEventListener("click", () => triggerReportDownload("xlsx"));
  }
  if (btnReportUsbCsv) {
    btnReportUsbCsv.addEventListener("click", () => saveReportToUsb("csv"));
  }
  if (btnReportUsbXlsx) {
    btnReportUsbXlsx.addEventListener("click", () => saveReportToUsb("xlsx"));
  }
  if (btnReportShiftCsv) {
    btnReportShiftCsv.addEventListener("click", () => triggerSimpleCsvDownload(API_REPORT_SHIFT_CSV_URL));
  }
  if (btnReportWorkersCsv) {
    btnReportWorkersCsv.addEventListener("click", () => triggerSimpleCsvDownload(API_REPORT_WORKERS_CSV_URL));
  }

  // Стартовая синхронизация настроек.
  updateSettingsAvailability();
  renderTabs({ master_active: !!currentMasterId });
  fetchSettings();
  // Учительская подсказка: каталог для очереди загружаем всегда, не только в мастер-режиме.
  loadSkuCatalog();
  fetchSkuCatalog();
  setReportDefaultDates();
})();
