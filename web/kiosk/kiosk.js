// Логика режима мастера вынесена в отдельный файл,
// чтобы основной HTML не разрастался и был легче для чтения.
(() => {
  const API_MASTER_LOGIN_URL = "/api/kiosk/master/login";
  const API_MASTER_LOGOUT_URL = "/api/kiosk/master/logout";
  const API_SETTINGS_URL = "/api/kiosk/settings";

  // UI-элементы мастера: кнопки, модалка, статус.
  const btnMasterLogin = document.getElementById("btnMasterLogin");
  const btnMasterLogout = document.getElementById("btnMasterLogout");
  const masterStatus = document.getElementById("masterStatus");

  const masterLoginBackdrop = document.getElementById("masterLoginBackdrop");
  const masterLoginInput = document.getElementById("masterLoginInput");
  const masterLoginHint = document.getElementById("masterLoginHint");
  const masterLoginActions = document.getElementById("masterLoginActions");
  const masterLoginCancel = document.getElementById("masterLoginCancel");

  // Чекбоксы настроек.
  const settingCanReorder = document.getElementById("settingCanReorder");
  const settingCanEditQty = document.getElementById("settingCanEditQty");
  const settingMasterTimeout = document.getElementById("settingMasterTimeout");
  const btnSettingsSave = document.getElementById("btnSettingsSave");
  const btnMasterLogoutSettings = document.getElementById("btnMasterLogoutSettings");
  const settingsHint = document.getElementById("settingsHint");

  let masterModalOpen = false;
  let currentMasterId = null;

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
    updateSettingsAvailability();
  }

  window.syncMasterState = (masterId) => {
    /**
     * Синхронизируем kiosk.js с backend-статусом мастера.
     *
     * Это важно для сценария авто-таймаута:
     * backend сбросил master_id, UI должен отключить чекбоксы.
     */
    currentMasterId = masterId || null;
    updateSettingsAvailability();
  };

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
    if (settingMasterTimeout) {
      settingMasterTimeout.value = String(settings.master_session_timeout_min || 15);
    }
    if (window.applyOperatorSettings) {
      window.applyOperatorSettings({
        operator_can_reorder: !!settings.operator_can_reorder,
        operator_can_edit_qty: !!settings.operator_can_edit_qty,
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

  async function logoutMaster() {
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
        body: JSON.stringify({ reason: "manual" }),
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
  });

  function initMainTabs() {
    /**
     * Переключение верхних вкладок.
     *
     * Мы не меняем данные и бизнес-логику, только показываем нужный экран.
     * Это безопасно: все API и таймеры продолжают работать в фоне.
     */
    const tabbar = document.getElementById("mainTabbar");
    if (!tabbar) return;
    const tabs = Array.from(tabbar.querySelectorAll(".tab"));
    const screens = Array.from(document.querySelectorAll(".screen"));

    const activateScreen = (screenId) => {
      screens.forEach((screen) => {
        const isActive = screen.id === screenId;
        screen.dataset.active = isActive ? "true" : "false";
      });
      tabs.forEach((tab) => {
        tab.classList.toggle("tab--active", tab.dataset.screen === screenId);
        // Активную вкладку делаем зелёной pill-кнопкой, чтобы она выглядела как остальные действия.
        tab.classList.toggle("pill-btn--active", tab.dataset.screen === screenId);
      });
    };

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.dataset.screen;
        if (!target) return;
        activateScreen(target);
      });
    });
  }

  if (settingCanReorder) {
    settingCanReorder.addEventListener("change", () => saveSettings());
  }
  if (settingCanEditQty) {
    settingCanEditQty.addEventListener("change", () => saveSettings());
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

  // Стартовая синхронизация настроек.
  updateSettingsAvailability();
  fetchSettings();
  initMainTabs();
})();
