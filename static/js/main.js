"use strict";

var ButtonLoader = (function () {
  function resolveButton(button) {
    if (!button) {
      return null;
    }
    if (typeof button === "string") {
      return document.querySelector(button);
    }
    return button;
  }

  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, Math.max(0, ms || 0));
    });
  }

  function findSubmitButton(form) {
    if (!form) {
      return null;
    }
    var active = document.activeElement;
    if (
      active &&
      form.contains(active) &&
      (
        (active.tagName === "BUTTON" && (active.type || "submit").toLowerCase() === "submit") ||
        (active.tagName === "INPUT" && (active.type || "").toLowerCase() === "submit")
      )
    ) {
      return active;
    }
    return form.querySelector("button[type='submit'], input[type='submit']");
  }

  function restoreButton(target) {
    if (!target) {
      return null;
    }

    if (typeof target._udlOriginalHTML !== "undefined") {
      target.innerHTML = target._udlOriginalHTML;
    }

    if (typeof target.disabled !== "undefined") {
      target.disabled = !!target._udlWasDisabled;
    }

    if (target._udlOriginalAriaDisabled === null || typeof target._udlOriginalAriaDisabled === "undefined") {
      target.removeAttribute("aria-disabled");
    } else {
      target.setAttribute("aria-disabled", target._udlOriginalAriaDisabled);
    }

    target.style.minWidth = target._udlOriginalMinWidthStyle || "";
    target.style.width = target._udlOriginalWidthStyle || "";
    target.style.height = target._udlOriginalHeightStyle || "";
    target.style.pointerEvents = target._udlOriginalPointerEvents || "";

    if (target._udlOriginalTabIndex === null || typeof target._udlOriginalTabIndex === "undefined") {
      target.removeAttribute("tabindex");
    } else {
      target.setAttribute("tabindex", target._udlOriginalTabIndex);
    }

    target.classList.remove("udl-btn-loading", "udl-btn-success", "udl-btn-error", "btn-icon-only", "udl-btn-icon-only");

    delete target._udlOriginalHTML;
    delete target._udlOriginalText;
    delete target._udlOriginalWidth;
    delete target._udlOriginalHeight;
    delete target._udlOriginalMinWidthStyle;
    delete target._udlOriginalWidthStyle;
    delete target._udlOriginalHeightStyle;
    delete target._udlOriginalPointerEvents;
    delete target._udlOriginalAriaDisabled;
    delete target._udlOriginalTabIndex;
    delete target._udlWasDisabled;
    delete target._udlLoadingStart;

    return target;
  }

  function start(button, loadingText) {
    var target = resolveButton(button);
    if (!target) {
      console.warn("[ButtonLoader] target button not found", button);
      return null;
    }

    if (target.classList.contains("udl-btn-loading")) {
      return target;
    }

    var iconOnly = !(target.textContent || "").trim();
    var hasCustomText = typeof loadingText === "string";
    var resolvedLoadingText = hasCustomText ? loadingText.trim() : "Loading...";

    target._udlOriginalHTML = target.innerHTML;
    target._udlOriginalText = (target.textContent || "").trim();
    target._udlOriginalWidth = target.offsetWidth;
    target._udlOriginalHeight = target.offsetHeight;
    target._udlOriginalMinWidthStyle = target.style.minWidth;
    target._udlOriginalWidthStyle = target.style.width;
    target._udlOriginalHeightStyle = target.style.height;
    target._udlOriginalPointerEvents = target.style.pointerEvents;
    target._udlOriginalAriaDisabled = target.getAttribute("aria-disabled");
    target._udlOriginalTabIndex = target.getAttribute("tabindex");
    target._udlWasDisabled = typeof target.disabled !== "undefined" ? target.disabled : false;
    target._udlLoadingStart = Date.now();

    if (typeof target.disabled !== "undefined") {
      target.disabled = true;
    }
    target.setAttribute("aria-disabled", "true");
    target.style.pointerEvents = "none";
    target.style.minWidth = target._udlOriginalWidth + "px";

    if (iconOnly) {
      target.style.width = target._udlOriginalWidth + "px";
      target.style.height = target._udlOriginalHeight + "px";
      target.classList.add("btn-icon-only", "udl-btn-icon-only");
    }

    var loadingHtml = "<span class='udl-btn-spinner' role='status' aria-hidden='true'></span>";
    if (!iconOnly && resolvedLoadingText) {
      loadingHtml += "<span class='udl-btn-loading-text' aria-live='polite'>" + resolvedLoadingText + "</span>";
    }

    target.innerHTML = loadingHtml;
    target.classList.add("udl-btn-loading");

    return target;
  }

  function stop(button, options) {
    var target = resolveButton(button);
    if (!target) {
      return Promise.resolve(null);
    }

    var settings = options || {};
    var showError = settings.error === true;
    var showSuccess = showError ? false : (typeof settings.success === "boolean" ? settings.success : true);
    var successText = settings.successText || "Done!";
    var errorText = settings.errorText || "Failed";
    var extraDelay = Number(settings.delay || 0);

    var elapsed = Date.now() - Number(target._udlLoadingStart || Date.now());
    var remainingMinTime = Math.max(0, 300 - elapsed);

    return wait(remainingMinTime + Math.max(0, extraDelay)).then(function () {
      if (showError) {
        target.classList.remove("udl-btn-loading");
        target.classList.add("udl-btn-error");
        target.innerHTML = "<span class='udl-btn-state-icon' aria-hidden='true'>✗</span><span class='udl-btn-loading-text' aria-live='polite'>" + errorText + "</span>";
        return wait(800);
      }
      if (showSuccess) {
        target.classList.remove("udl-btn-loading");
        target.classList.add("udl-btn-success");
        target.innerHTML = "<span class='udl-btn-state-icon' aria-hidden='true'>✓</span><span class='udl-btn-loading-text' aria-live='polite'>" + successText + "</span>";
        return wait(600);
      }
      return Promise.resolve();
    }).then(function () {
      return restoreButton(target);
    });
  }

  function stopAll() {
    var loadingButtons = document.querySelectorAll(".udl-btn-loading");
    var restores = [];
    loadingButtons.forEach(function (btn) {
      restores.push(stop(btn, { success: false, error: false }));
    });
    return Promise.all(restores);
  }

  function isLoading(button) {
    var target = resolveButton(button);
    return !!(target && target.classList.contains("udl-btn-loading"));
  }

  function bindForm(form, options) {
    var formEl = typeof form === "string" ? document.querySelector(form) : form;
    if (!formEl) {
      console.warn("[ButtonLoader] form not found", form);
      return null;
    }
    if (formEl._udlBoundForm) {
      return formEl;
    }

    var config = options || {};
    formEl._udlBoundForm = true;

    formEl.addEventListener("submit", function (event) {
      event.preventDefault();

      if (!formEl.checkValidity()) {
        formEl.reportValidity();
        return;
      }

      var submitButton = findSubmitButton(formEl);
      if (!submitButton) {
        return;
      }

      if (isLoading(submitButton)) {
        return;
      }

      var loadingLabel = config.loadingText || submitButton.getAttribute("data-loading-text") || "Saving...";
      start(submitButton, loadingLabel);

      var submitPromise;
      try {
        submitPromise = typeof config.onSubmit === "function"
          ? config.onSubmit(new FormData(formEl), submitButton)
          : Promise.resolve({ success: true });
      } catch (err) {
        submitPromise = Promise.reject(err);
      }

      Promise.resolve(submitPromise)
        .then(function (result) {
          var isSuccess = !result || result.success !== false;
          return stop(submitButton, {
            success: isSuccess,
            error: !isSuccess,
            successText: config.successText || "Done!",
            errorText: (result && result.message) || config.errorText || "Failed"
          });
        })
        .catch(function (error) {
          return stop(submitButton, {
            success: false,
            error: true,
            errorText: (error && error.message) || config.errorText || "Failed"
          });
        });
    });

    return formEl;
  }

  return {
    start: start,
    stop: stop,
    stopAll: stopAll,
    isLoading: isLoading,
    bindForm: bindForm
  };
})();

window.ButtonLoader = ButtonLoader;

function bindDeclarativeButtonLoaders() {
  var loaderButtons = document.querySelectorAll('[data-btn-loader="true"]');
  loaderButtons.forEach(function (button) {
    if (button._udlLoaderClickBound) {
      return;
    }
    button._udlLoaderClickBound = true;

    button.addEventListener("click", function () {
      if (window.ButtonLoader.isLoading(button)) {
        return;
      }
      var loadingText = button.getAttribute("data-loading-text") || "Loading...";
      window.ButtonLoader.start(button, loadingText);
    });

    var form = button.form || button.closest("form");
    if (!form || form._udlLoaderSubmitBound) {
      return;
    }

    form._udlLoaderSubmitBound = true;
    form.addEventListener("submit", function () {
      if (!form.checkValidity()) {
        form.reportValidity();
        return;
      }

      var submitButton = null;
      var active = document.activeElement;

      if (
        active &&
        form.contains(active) &&
        (
          (active.tagName === "BUTTON" && (active.type || "submit").toLowerCase() === "submit") ||
          (active.tagName === "INPUT" && (active.type || "").toLowerCase() === "submit")
        )
      ) {
        submitButton = active;
      }

      if (!submitButton) {
        submitButton = form.querySelector('[data-btn-loader="true"][type="submit"], button[type="submit"][data-btn-loader="true"], input[type="submit"][data-btn-loader="true"], button[type="submit"], input[type="submit"]');
      }

      if (!submitButton || window.ButtonLoader.isLoading(submitButton)) {
        return;
      }

      var loadingText = submitButton.getAttribute("data-loading-text") || "Loading...";
      window.ButtonLoader.start(submitButton, loadingText);
    });
  });
}

window.addEventListener("pageshow", function (event) {
  if (event.persisted && window.ButtonLoader) {
    window.ButtonLoader.stopAll();
  }
});

// No tracking — privacy first

const THEME_KEY = "udl-theme";
const DEFAULT_THEME = "light";

function getStoredTheme() {
  try {
    var stored = localStorage.getItem(THEME_KEY) || DEFAULT_THEME;
    if (stored !== "dark" && stored !== "light") {
      return DEFAULT_THEME;
    }
    return stored;
  } catch (e) {
    return DEFAULT_THEME;
  }
}

function setThemeAttributes(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-bs-theme", theme);
  if (document.body) {
    document.body.setAttribute("data-theme", theme);
    document.body.setAttribute("data-bs-theme", theme);
  }
}

function applyTheme(theme) {
  setThemeAttributes(theme);
  updateThemeToggleButton(theme);
  document.dispatchEvent(new CustomEvent("themeChanged", { detail: { theme: theme } }));
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (e) {
  }
  saveThemeToServer(theme);
}

function updateThemeToggleButton(theme) {
  var toggleBtns = document.querySelectorAll(".theme-toggle-btn");
  toggleBtns.forEach(function (btn) {
    var moonIcon = btn.querySelector(".theme-icon-moon");
    var sunIcon = btn.querySelector(".theme-icon-sun");
    var themeLabel = btn.querySelector(".theme-label");

    if (theme === "light") {
      if (moonIcon) {
        moonIcon.classList.remove("d-none");
      }
      if (sunIcon) {
        sunIcon.classList.add("d-none");
      }
      if (themeLabel) {
        themeLabel.textContent = "Dark";
      }
      btn.setAttribute("aria-label", "Switch to dark mode");
      btn.setAttribute("title", "Switch to dark mode");
    } else {
      if (moonIcon) {
        moonIcon.classList.add("d-none");
      }
      if (sunIcon) {
        sunIcon.classList.remove("d-none");
      }
      if (themeLabel) {
        themeLabel.textContent = "Light";
      }
      btn.setAttribute("aria-label", "Switch to light mode");
      btn.setAttribute("title", "Switch to light mode");
    }
  });
}

function toggleTheme() {
  var current = document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
  var next = current === "light" ? "dark" : "light";
  applyTheme(next);
}

function saveThemeToServer(theme) {
  var csrfMeta = document.querySelector("meta[name=\"csrf-token\"]");
  if (!csrfMeta) {
    return;
  }
  fetch("/set-theme", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfMeta.content
    },
    body: JSON.stringify({ theme: theme })
  }).catch(function () {
    return;
  });
}

function initTheme() {
  var storedTheme = getStoredTheme();
  applyTheme(storedTheme);
}

document.addEventListener("click", function (event) {
  var target = event.target;
  if (!target) {
    return;
  }
  var btn = target.closest(".theme-toggle-btn");
  if (!btn) {
    return;
  }
  if (btn.disabled || btn.getAttribute("aria-disabled") === "true") {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  btn.disabled = true;
  btn.setAttribute("aria-disabled", "true");
  toggleTheme();
  setTimeout(function () {
    btn.disabled = false;
    btn.removeAttribute("aria-disabled");
  }, 200);
});

function calculateStrength(password) {
  if (!password) {
    return 0;
  }
  if (password.length < 10) {
    return 1;
  }
  var hasUpper = /[A-Z]/.test(password);
  var hasNumber = /[0-9]/.test(password);
  var hasSpecial = /[^A-Za-z0-9]/.test(password);
  if (!hasUpper || !hasNumber) {
    return 2;
  }
  if (hasUpper && hasNumber && !hasSpecial) {
    return 3;
  }
  return 4;
}

function updateStrengthBar(score) {
  var fill = document.querySelector(".strength-fill");
  if (!fill) {
    return;
  }
  fill.className = "strength-fill";
  if (score <= 0) {
    fill.style.width = "0";
    return;
  }
  if (score === 1) {
    fill.classList.add("strength-weak");
  } else if (score === 2) {
    fill.classList.add("strength-fair");
  } else if (score === 3) {
    fill.classList.add("strength-good");
  } else {
    fill.classList.add("strength-strong");
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(showCopiedMessage).catch(function () {
      fallbackCopy(text);
    });
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  var textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
    showCopiedMessage();
  } catch (err) {
    return;
  }
  document.body.removeChild(textarea);
}

function showCopiedMessage() {
  var tooltip = document.createElement("div");
  tooltip.className = "copy-tooltip";
  tooltip.textContent = "Copied!";
  document.body.appendChild(tooltip);
  setTimeout(function () {
    if (tooltip.parentNode) {
      tooltip.parentNode.removeChild(tooltip);
    }
  }, 2000);
}

function filterPlatforms(query, category, activeOnly) {
  var items = document.querySelectorAll(".platform-grid-item");
  var normalizedQuery = query ? query.toLowerCase() : "";
  var visibleCount = 0;
  items.forEach(function (item) {
    var name = (item.getAttribute("data-name") || "").toLowerCase();
    var display = (item.getAttribute("data-display") || "").toLowerCase();
    var itemCategory = item.getAttribute("data-category") || "all";
    var status = (item.getAttribute("data-status") || "").toLowerCase();
    var matchQuery =
      !normalizedQuery ||
      name.indexOf(normalizedQuery) !== -1 ||
      display.indexOf(normalizedQuery) !== -1;
    var matchCategory = category === "all" || itemCategory === category;
    var matchStatus = !activeOnly || status === "active";
    var shouldShow = matchQuery && matchCategory && matchStatus;
    item.style.display = shouldShow ? "" : "none";
    if (shouldShow) {
      visibleCount += 1;
    }
  });

  var emptyState = document.getElementById("platform-no-results");
  var searchHint = document.getElementById("platform-search-hint");
  if (emptyState) {
    emptyState.classList.toggle("d-none", visibleCount !== 0);
  }
  if (searchHint) {
    var hasQuery = normalizedQuery.length > 0;
    searchHint.classList.toggle("d-none", !hasQuery || visibleCount !== 0);
  }
}

function isIos() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent);
}

document.addEventListener("DOMContentLoaded", function () {
  initTheme();

  var navbar = document.querySelector(".site-navbar");
  window.addEventListener("scroll", function () {
    if (!navbar) {
      return;
    }
    if (window.scrollY > 20) {
      navbar.classList.add("scrolled");
    } else {
      navbar.classList.remove("scrolled");
    }
  });

  var alerts = document.querySelectorAll(".alert.auto-dismiss");
  alerts.forEach(function (alertEl) {
    setTimeout(function () {
      var instance = bootstrap.Alert.getInstance(alertEl);
      if (!instance) {
        instance = new bootstrap.Alert(alertEl);
      }
      instance.close();
    }, 5000);
  });

  var urlInputs = document.querySelectorAll(".url-input-auto, #url-input");
  urlInputs.forEach(function (input) {
    input.addEventListener("paste", function () {
      var form = input.closest("form");
      if (!form) {
        return;
      }
      input.classList.add("udl-input-paste-loading");
      setTimeout(function () {
        form.submit();
        setTimeout(function () {
          input.classList.remove("udl-input-paste-loading");
        }, 400);
      }, 500);
    });
  });

  bindDeclarativeButtonLoaders();

  var passwordInput = document.getElementById("register-password");
  if (passwordInput) {
    passwordInput.addEventListener("input", function () {
      updateStrengthBar(calculateStrength(passwordInput.value));
    });
  }

  var resetPasswordInput = document.getElementById("reset-password");
  if (resetPasswordInput) {
    resetPasswordInput.addEventListener("input", function () {
      updateStrengthBar(calculateStrength(resetPasswordInput.value));
    });
  }

  var qualitySelect = document.getElementById("quality-select");
  var formatSelect = document.getElementById("format-select");
  var sizeEstimate = document.querySelector(".size-estimate");
  var updateEstimate = function () {
    if (!sizeEstimate) {
      return;
    }
    var qualityValue = qualitySelect ? qualitySelect.value : "";
    var formatValue = formatSelect ? formatSelect.value : "";
    if (!qualityValue && !formatValue) {
      sizeEstimate.textContent = "Estimated size: --";
      return;
    }
    var label = "Selected: " + (qualityValue || "") + " / " + (formatValue || "");
    sizeEstimate.textContent = label;
  };
  if (qualitySelect) {
    qualitySelect.addEventListener("change", updateEstimate);
  }
  if (formatSelect) {
    formatSelect.addEventListener("change", updateEstimate);
  }

  var toggleButtons = document.querySelectorAll("[data-toggle='password']");
  toggleButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var targetId = btn.getAttribute("data-target");
      var targetInput = document.getElementById(targetId);
      if (!targetInput) {
        return;
      }
      if (targetInput.type === "password") {
        targetInput.type = "text";
        btn.innerHTML = "<i class='bi bi-eye-slash'></i>";
      } else {
        targetInput.type = "password";
        btn.innerHTML = "<i class='bi bi-eye'></i>";
      }
    });
  });

  var searchInput =
    document.getElementById("platform-search") ||
    document.getElementById("platform-search-input");
  var categoryButtons = document.querySelectorAll("[data-category]");
  var activeCategory = "all";
  var statusToggle = document.getElementById("platform-status-toggle");
  var activeOnly = false;

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      filterPlatforms(searchInput.value, activeCategory, activeOnly);
    });
  }

  categoryButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      categoryButtons.forEach(function (item) {
        item.classList.remove("active");
      });
      btn.classList.add("active");
      activeCategory = btn.getAttribute("data-category") || "all";
      filterPlatforms(searchInput ? searchInput.value : "", activeCategory, activeOnly);
    });
  });

  if (statusToggle) {
    statusToggle.addEventListener("change", function () {
      activeOnly = statusToggle.checked;
      filterPlatforms(searchInput ? searchInput.value : "", activeCategory, activeOnly);
    });
  }

  var anchorLinks = document.querySelectorAll("a[href^='#']");
  anchorLinks.forEach(function (link) {
    link.addEventListener("click", function (event) {
      var targetId = link.getAttribute("href");
      if (!targetId || targetId === "#" || targetId.charAt(0) !== "#") {
        return;
      }
      var target = document.getElementById(targetId.slice(1));
      if (!target) {
        return;
      }
      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  var collapse = document.querySelector(".navbar-collapse");
  if (collapse) {
    var navLinks = collapse.querySelectorAll(".nav-link");
    navLinks.forEach(function (link) {
      link.addEventListener("click", function () {
        if (collapse.classList.contains("show")) {
          var bsCollapse = bootstrap.Collapse.getInstance(collapse);
          if (!bsCollapse) {
            bsCollapse = new bootstrap.Collapse(collapse, { toggle: false });
          }
          bsCollapse.hide();
        }
      });
    });
  }

  if (isIos()) {
    var inputs = document.querySelectorAll("input, select, textarea");
    inputs.forEach(function (input) {
      input.style.fontSize = "16px";
    });
  }
});

if (document.readyState !== "loading") {
  initTheme();
}
