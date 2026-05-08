"use strict";

var usageChart = null;

function startActionButton(button, loadingText) {
  if (!button || !window.ButtonLoader) {
    return true;
  }
  if (window.ButtonLoader.isLoading(button)) {
    return false;
  }
  window.ButtonLoader.start(button, loadingText);
  return true;
}

function stopActionButton(button, options) {
  if (!button || !window.ButtonLoader) {
    return Promise.resolve();
  }
  return window.ButtonLoader.stop(button, options || { success: false, error: false });
}

function getThemeColors() {
  var isDark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    text: isDark ? "#94A3B8" : "#475569",
    textPrimary: isDark ? "#F1F5F9" : "#0F172A",
    border: isDark ? "#334155" : "#E2E8F0",
    surface: isDark ? "#1E293B" : "#FFFFFF",
    surface2: isDark ? "#334155" : "#F1F5F9",
    gridLines: isDark ? "rgba(51,65,85,0.6)" : "rgba(226,232,240,0.8)",
    accent: "#E94560",
    accentBg: isDark ? "rgba(233,69,96,0.15)" : "rgba(233,69,96,0.08)"
  };
}

function initApiSettings() {
  if (document.getElementById("api-usage-chart")) {
    initUsageChart();
  }
  bindGenerateKeyForm();
  bindRevokeKeyButton();
  bindCopyPrefixButton();
  bindCopyKeyButton();
  bindNewKeyModalCheckbox();
}

function initUsageChart() {
  var canvas = document.getElementById("api-usage-chart");
  if (!canvas) {
    return;
  }
  var labels = JSON.parse(canvas.dataset.labels || "[]");
  var counts = JSON.parse(canvas.dataset.counts || "[]");
  var ctx = canvas.getContext("2d");
  var colors = getThemeColors();

  if (usageChart) {
    usageChart.destroy();
  }

  usageChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "API Calls",
          data: counts,
          borderColor: colors.accent,
          backgroundColor: colors.accentBg,
          fill: true,
          tension: 0.4,
          pointRadius: 3,
          pointHoverRadius: 6,
          pointBackgroundColor: colors.accent,
          pointBorderColor: colors.surface
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: colors.surface,
          titleColor: colors.textPrimary,
          bodyColor: colors.text,
          borderColor: colors.border,
          borderWidth: 1
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { stepSize: 1, color: colors.text },
          grid: { color: colors.gridLines }
        },
        x: {
          ticks: { maxTicksLimit: 10, color: colors.text },
          grid: { color: colors.gridLines }
        }
      }
    }
  });
}

function bindGenerateKeyForm() {
  var forms = document.querySelectorAll(".generate-key-form");
  if (!forms.length) {
    return;
  }
  forms.forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var submitButton = form.querySelector("button[type='submit'], input[type='submit']");
      var nameInput = form.querySelector("input[name='key_name']");
      if (!nameInput || !nameInput.value.trim()) {
        showToast("Please enter a key name", "warning");
        return;
      }

      if (!startActionButton(submitButton, "Generating...")) {
        return;
      }

      var payload = { key_name: nameInput.value.trim() };
      fetch("/settings/api/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify(payload)
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) {
              throw new Error(data.message || "Unable to generate key");
            }
            return data;
          });
        })
        .then(function (data) {
          return stopActionButton(submitButton, { success: true, successText: "Generated!" }).then(function () {
            showNewKeyModal(data.raw_key);
            updateKeyPrefix(data.prefix);
          });
        })
        .catch(function (err) {
          stopActionButton(submitButton, { success: false, error: true, errorText: "Failed" }).then(function () {
            showToast(err.message || "Unable to generate key", "error");
          });
        });
    });
  });
}

function bindCopyPrefixButton() {
  var button = document.getElementById("copy-prefix-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    var input = document.getElementById("api-key-prefix-display");
    if (!input) {
      return;
    }
    button.disabled = true;
    copyToClipboard(input.value || "")
      .then(function () {
        showToast("API key prefix copied", "info");
      })
      .catch(function () {
        showToast("Unable to copy key prefix", "error");
      })
      .finally(function () {
        setTimeout(function () {
          button.disabled = false;
        }, 300);
      });
  });
}

function bindRevokeKeyButton() {
  var button = document.getElementById("revoke-key-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    var ok = window.confirm("Revoke this API key? This cannot be undone.");
    if (!ok) {
      return;
    }

    if (!startActionButton(button, "Revoking...")) {
      return;
    }

    fetch("/settings/api/revoke", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.message || "Unable to revoke key");
          }
          return data;
        });
      })
      .then(function () {
        return stopActionButton(button, { success: true, successText: "Revoked" }).then(function () {
          toggleKeyState(false);
          showToast("API key revoked successfully", "success");
        });
      })
      .catch(function (err) {
        stopActionButton(button, { success: false, error: true, errorText: "Failed" }).then(function () {
          showToast(err.message || "Unable to revoke key", "error");
        });
      });
  });
}

function bindCopyKeyButton() {
  var button = document.getElementById("copy-raw-key-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    var display = document.getElementById("raw-api-key-display");
    if (!display) {
      return;
    }
    button.disabled = true;
    var originalText = button.textContent;

    copyToClipboard(display.value || display.textContent || "")
      .then(function () {
        button.textContent = "✓ Copied!";
        setTimeout(function () {
          button.textContent = originalText;
          button.disabled = false;
        }, 2000);
      })
      .catch(function () {
        button.textContent = originalText;
        button.disabled = false;
        showToast("Unable to copy API key", "error");
      });
  });
}

function bindNewKeyModalCheckbox() {
  var checkbox = document.getElementById("confirm-copied-checkbox");
  var closeBtn = document.getElementById("close-new-key-modal-btn");
  if (!checkbox || !closeBtn) {
    return;
  }
  checkbox.addEventListener("change", function () {
    closeBtn.disabled = !checkbox.checked;
  });
}

function showNewKeyModal(rawKey) {
  var display = document.getElementById("raw-api-key-display");
  if (display) {
    display.value = rawKey;
    display.textContent = rawKey;
  }
  var checkbox = document.getElementById("confirm-copied-checkbox");
  var closeBtn = document.getElementById("close-new-key-modal-btn");
  if (checkbox) {
    checkbox.checked = false;
  }
  if (closeBtn) {
    closeBtn.disabled = true;
  }
  toggleKeyState(true);
  var modalEl = document.getElementById("newKeyModal");
  if (modalEl) {
    var modal = new bootstrap.Modal(modalEl, { backdrop: "static", keyboard: false });
    modal.show();
  }
}

function updateKeyPrefix(prefix) {
  var input = document.getElementById("api-key-prefix-display");
  if (input) {
    input.value = "udl_" + prefix + "***************";
  }
}

function toggleKeyState(hasKey) {
  var noKey = document.getElementById("no-api-key-state");
  var hasKeyEl = document.getElementById("has-api-key-state");
  if (noKey) {
    noKey.classList.toggle("d-none", hasKey);
  }
  if (hasKeyEl) {
    hasKeyEl.classList.toggle("d-none", !hasKey);
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch(function () {
      return fallbackCopy(text);
    });
  } else {
    return fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  return new Promise(function (resolve, reject) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
      resolve();
    } catch (err) {
      reject(err);
    }
    document.body.removeChild(textarea);
  });
}

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  return "";
}

function showToast(message, type) {
  var container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container position-fixed top-0 end-0 p-3";
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  var toastEl = document.createElement("div");
  var tone = "success";
  if (type === "error") {
    tone = "error";
  } else if (type === "info") {
    tone = "info";
  } else if (type === "warning") {
    tone = "warning";
  }
  toastEl.className = "toast align-items-center theme-toast theme-toast-" + tone;
  toastEl.setAttribute("role", "alert");
  toastEl.setAttribute("aria-live", "assertive");
  toastEl.setAttribute("aria-atomic", "true");
  toastEl.innerHTML = "<div class='d-flex'><div class='toast-body'>" + message + "</div><button type='button' class='btn-close me-2 m-auto' data-bs-dismiss='toast' aria-label='Close'></button></div>";
  container.appendChild(toastEl);
  var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
  toast.show();
  toastEl.addEventListener("hidden.bs.toast", function () {
    toastEl.remove();
  });
}

document.addEventListener("DOMContentLoaded", initApiSettings);
document.addEventListener("themeChanged", function () {
  if (document.getElementById("api-usage-chart")) {
    initUsageChart();
  }
});
