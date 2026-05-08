"use strict";

var adminDownloadsChart = null;
var adminPlatformChart = null;

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

function lockModalWhileLoading(modalEl, activeButton) {
  if (!modalEl) {
    return function () {
      return;
    };
  }

  var closeButton = modalEl.querySelector(".btn-close");
  var dismissButtons = modalEl.querySelectorAll("[data-bs-dismiss='modal']");
  var preventClose = function (event) {
    if (activeButton && window.ButtonLoader && window.ButtonLoader.isLoading(activeButton)) {
      event.preventDefault();
    }
  };

  modalEl.addEventListener("hide.bs.modal", preventClose);

  if (closeButton) {
    closeButton.disabled = true;
  }
  dismissButtons.forEach(function (button) {
    if (button !== activeButton) {
      button.disabled = true;
    }
  });

  return function unlockModal() {
    modalEl.removeEventListener("hide.bs.modal", preventClose);
    if (closeButton) {
      closeButton.disabled = false;
    }
    dismissButtons.forEach(function (button) {
      button.disabled = false;
    });
  };
}

function getThemeColors() {
  var isDark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    text: isDark ? "#94A3B8" : "#475569",
    textPrimary: isDark ? "#F1F5F9" : "#0F172A",
    border: isDark ? "#334155" : "#E2E8F0",
    surface: isDark ? "#1E293B" : "#FFFFFF",
    gridLines: isDark ? "rgba(51,65,85,0.6)" : "rgba(226,232,240,0.8)",
    accent: "#E94560",
    accentBg: isDark ? "rgba(233,69,96,0.15)" : "rgba(233,69,96,0.08)",
    platformPalette: ["#E94560", "#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#14B8A6", "#F97316", "#EC4899", "#22C55E", "#64748B"]
  };
}

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  return "";
}

function showAdminToast(message, type) {
  var container = document.getElementById("admin-toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container position-fixed top-0 end-0 p-3";
    container.id = "admin-toast-container";
    document.body.appendChild(container);
  }

  var toastEl = document.createElement("div");
  var tone = "success";
  if (type === "error") {
    tone = "error";
  } else if (type === "warning") {
    tone = "warning";
  } else if (type === "info") {
    tone = "info";
  }

  toastEl.className = "toast align-items-center theme-toast theme-toast-" + tone;
  toastEl.setAttribute("role", "alert");
  toastEl.setAttribute("aria-live", "assertive");
  toastEl.setAttribute("aria-atomic", "true");
  toastEl.innerHTML =
    "<div class='d-flex'><div class='toast-body'>" +
    message +
    "</div><button type='button' class='btn-close me-2 m-auto' data-bs-dismiss='toast' aria-label='Close'></button></div>";
  container.appendChild(toastEl);

  var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
  toast.show();
  toastEl.addEventListener("hidden.bs.toast", function () {
    toastEl.remove();
  });
}

function initAdmin() {
  var path = window.location.pathname;
  if (path.indexOf("/admin/dashboard") === 0) {
    initAdminDashboard();
  }
  if (path.indexOf("/admin/extractors") === 0) {
    initAdminExtractors();
  }
  if (path.indexOf("/admin/users") === 0) {
    initAdminUsers();
  }
  if (path.indexOf("/admin/queue") === 0) {
    initAdminQueue();
  }
  if (path.indexOf("/admin/logs") === 0) {
    initAdminLogs();
  }
  if (path.indexOf("/admin/settings") === 0) {
    initAdminSettings();
  }
}

function initAdminSidebar() {
  var toggleBtn = document.querySelector(".admin-toggle-btn");
  var sidebar = document.querySelector(".admin-sidebar");
  if (!toggleBtn || !sidebar) {
    return;
  }
  toggleBtn.addEventListener("click", function () {
    sidebar.classList.toggle("open");
  });
  document.addEventListener("click", function (event) {
    if (!sidebar.classList.contains("open")) {
      return;
    }
    if (sidebar.contains(event.target) || toggleBtn.contains(event.target)) {
      return;
    }
    sidebar.classList.remove("open");
  });
}

function initAdminDashboard() {
  initDashboardCharts();
  startQueueStatsPolling();
  bindExtractorActions();
  bindRetryButtons();
}

function initDashboardCharts() {
  var downloadLabels = readJsonScript("daily-download-labels");
  var downloadCounts = readJsonScript("daily-download-counts");
  var platformLabels = readJsonScript("platform-labels");
  var platformCounts = readJsonScript("platform-counts");
  var colors = getThemeColors();

  var downloadsCanvas = document.getElementById("downloads-chart");
  if (downloadsCanvas && window.Chart && downloadLabels && downloadCounts) {
    if (adminDownloadsChart) {
      adminDownloadsChart.destroy();
    }
    adminDownloadsChart = new Chart(downloadsCanvas, {
      type: "line",
      data: {
        labels: downloadLabels,
        datasets: [
          {
            label: "Downloads",
            data: downloadCounts,
            borderColor: colors.accent,
            backgroundColor: colors.accentBg,
            fill: true,
            tension: 0.35,
            pointRadius: 2,
            pointBackgroundColor: colors.accent,
            pointBorderColor: colors.surface
          }
        ]
      },
      options: {
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
          x: {
            ticks: { color: colors.text },
            grid: { color: colors.gridLines }
          },
          y: {
            ticks: { color: colors.text },
            grid: { color: colors.gridLines }
          }
        }
      }
    });
  }

  var platformCanvas = document.getElementById("platform-chart");
  if (platformCanvas && window.Chart && platformLabels && platformCounts) {
    if (adminPlatformChart) {
      adminPlatformChart.destroy();
    }
    adminPlatformChart = new Chart(platformCanvas, {
      type: "doughnut",
      data: {
        labels: platformLabels,
        datasets: [
          {
            data: platformCounts,
            backgroundColor: colors.platformPalette,
            borderColor: colors.surface,
            borderWidth: 2
          }
        ]
      },
      options: {
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: colors.text,
              boxWidth: 12,
              padding: 14
            }
          },
          tooltip: {
            backgroundColor: colors.surface,
            titleColor: colors.textPrimary,
            bodyColor: colors.text,
            borderColor: colors.border,
            borderWidth: 1
          }
        }
      }
    });
  }
}

function startQueueStatsPolling() {
  var activeEl = document.getElementById("kpi-active-jobs");
  if (!activeEl) {
    return;
  }
  var refreshLink = document.getElementById("refresh-queue-stats");
  if (refreshLink) {
    refreshLink.addEventListener("click", function (event) {
      event.preventDefault();
      fetchQueueStatsOnce(activeEl);
    });
  }
  fetchQueueStatsOnce(activeEl);
  setInterval(function () {
    fetchQueueStatsOnce(activeEl);
  }, 10000);
}

function fetchQueueStatsOnce(activeEl) {
  fetch("/admin/queue/stats")
    .then(function (response) { return response.json(); })
    .then(function (data) {
      if (typeof data.active_count !== "undefined") {
        activeEl.textContent = data.active_count;
      }
    })
    .catch(function () {
      return;
    });
}

function bindExtractorActions() {
  bindToggleExtractors();
  bindTestExtractor();
}

function initAdminExtractors() {
  bindToggleExtractors();
  bindTestExtractor();
  bindTriggerTasks();
}

function bindToggleExtractors() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".extractor-toggle-btn");
    if (!button) {
      return;
    }
    event.preventDefault();
    var extractorId = button.getAttribute("data-extractor-id");
    if (!extractorId) {
      return;
    }

    if (!startActionButton(button, "")) {
      return;
    }

    fetch("/admin/extractors/" + extractorId + "/toggle", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.success) {
          throw new Error(data.message || "Toggle failed");
        }
        button.textContent = data.is_enabled ? "Enabled" : "Disabled";
        button.classList.toggle("btn-success", data.is_enabled);
        button.classList.toggle("btn-secondary", !data.is_enabled);
        var row = button.closest("tr");
        if (row) {
          var badge = row.querySelector(".extractor-status-badge");
          if (badge) {
            badge.textContent = data.is_enabled ? "Active" : "Disabled";
            badge.className = "extractor-status-badge status-badge " + (data.is_enabled ? "status-active" : "status-disabled");
          }
        }
        return stopActionButton(button, { success: true }).then(function () {
          showAdminToast(data.message || "Updated", "success");
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Toggle failed", "error");
        });
      });
  });
}

function bindTestExtractor() {
  var modalEl = document.getElementById("testExtractorModal");
  if (!modalEl) {
    return;
  }
  var modal = new bootstrap.Modal(modalEl);
  var runBtn = modalEl.querySelector(".run-test-btn");
  var urlInput = modalEl.querySelector(".test-url-input");
  var resultArea = modalEl.querySelector(".test-result-area");
  var titleEl = modalEl.querySelector(".modal-title");

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".extractor-test-btn");
    if (!button) {
      return;
    }
    event.preventDefault();

    if (!startActionButton(button, "Testing...")) {
      return;
    }

    var extractorId = button.getAttribute("data-extractor-id");
    var platformName = button.getAttribute("data-platform-name");
    var defaultUrl = button.getAttribute("data-test-url") || "";
    modalEl.setAttribute("data-extractor-id", extractorId);
    if (titleEl) {
      titleEl.textContent = "Test " + platformName + " Extractor";
    }
    if (resultArea) {
      resultArea.innerHTML = "";
    }
    if (urlInput) {
      urlInput.value = "";
      urlInput.placeholder = defaultUrl || "https://";
    }
    modal.show();

    var unlockModal = lockModalWhileLoading(modalEl, button);
    runExtractorTest(extractorId, "", resultArea)
      .then(function (data) {
        return stopActionButton(button, {
          success: !!data.success,
          error: !data.success,
          successText: "Test Passed!",
          errorText: "Test Failed"
        });
      })
      .catch(function () {
        return stopActionButton(button, { success: false, error: true, errorText: "Error" });
      })
      .finally(function () {
        unlockModal();
      });
  });

  if (runBtn) {
    runBtn.addEventListener("click", function () {
      var extractorId = modalEl.getAttribute("data-extractor-id");
      if (!extractorId) {
        return;
      }
      if (!startActionButton(runBtn, "Running...")) {
        return;
      }

      var unlockModal = lockModalWhileLoading(modalEl, runBtn);
      runExtractorTest(extractorId, urlInput ? urlInput.value : "", resultArea)
        .then(function (data) {
          return stopActionButton(runBtn, {
            success: !!data.success,
            error: !data.success,
            successText: "Test Passed!",
            errorText: "Test Failed"
          });
        })
        .catch(function () {
          return stopActionButton(runBtn, { success: false, error: true, errorText: "Error" });
        })
        .finally(function () {
          unlockModal();
        });
    });
  }
}

function runExtractorTest(extractorId, testUrl, resultArea) {
  if (resultArea) {
    resultArea.innerHTML = "<div class='d-flex align-items-center gap-2'><span class='udl-btn-spinner' aria-hidden='true'></span><span>Running extractor test...</span></div>";
  }

  return fetch("/admin/extractors/" + extractorId + "/test", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({ test_url: testUrl || "" })
  })
    .then(function (response) {
      return response.json().then(function (data) {
        return data;
      });
    })
    .then(function (data) {
      renderExtractorTestResult(extractorId, data, resultArea);
      return data;
    });
}

function renderExtractorTestResult(extractorId, data, resultArea) {
  if (!resultArea) {
    return;
  }

  if (data && data.success) {
    var result = data.result || {};
    resultArea.innerHTML =
      "<div class='alert alert-success'><div class='fw-bold'>" +
      (result.title || "Success") +
      "</div><div class='small'>Qualities: " +
      (result.qualities_count || 0) +
      "</div><div class='small'>Duration: " +
      (result.duration || "-") +
      "</div></div>";

    var rowButton = document.querySelector(".extractor-test-btn[data-extractor-id='" + extractorId + "']");
    if (rowButton) {
      var badge = rowButton.closest("tr").querySelector(".extractor-status-badge");
      if (badge) {
        badge.textContent = "Active";
        badge.className = "extractor-status-badge status-badge status-active";
      }
    }
  } else {
    resultArea.innerHTML = "<div class='alert alert-danger'>" + ((data && data.message) || "Test failed") + "</div>";
  }
}

function initAdminUsers() {
  bindUserActions();
  bindUserProfileModal();
}

function bindUserActions() {
  var loadingLabelMap = {
    "grant-pro": "Granting...",
    "revoke-pro": "Revoking...",
    suspend: "Suspending...",
    unsuspend: "Unsuspending..."
  };

  var successLabelMap = {
    "grant-pro": "Granted!",
    "revoke-pro": "Revoked",
    suspend: "Suspended",
    unsuspend: "Unsuspended"
  };

  document.addEventListener("click", function (event) {
    var target = event.target.closest(".admin-user-action");
    if (!target) {
      return;
    }
    event.preventDefault();
    var action = target.getAttribute("data-action");
    var userId = target.getAttribute("data-user-id");
    if (!action || !userId) {
      return;
    }

    if (action === "grant-pro-custom") {
      return;
    }

    if (action === "suspend" && !confirm("Suspend this user?")) {
      return;
    }

    if (!startActionButton(target, loadingLabelMap[action] || "Processing...")) {
      return;
    }

    fetch("/admin/users/" + userId + "/" + action, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({})
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || !data.success) {
            throw new Error(data.message || "Action failed");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(target, {
          success: true,
          successText: successLabelMap[action] || "Done"
        }).then(function () {
          showAdminToast(data.message || "Updated", "success");
          window.location.reload();
        });
      })
      .catch(function (error) {
        stopActionButton(target, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Action failed", "error");
        });
      });
  });

  var grantCustomBtns = document.querySelectorAll(".grant-pro-custom-btn");
  grantCustomBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var userId = btn.getAttribute("data-user-id");
      var modalEl = document.getElementById("grantProModal");
      if (!modalEl) {
        return;
      }
      modalEl.setAttribute("data-user-id", userId);
      var modal = new bootstrap.Modal(modalEl);
      modal.show();
    });
  });

  var confirmBtn = document.getElementById("grant-pro-confirm");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", function () {
      var modalEl = document.getElementById("grantProModal");
      if (!modalEl) {
        return;
      }
      var userId = modalEl.getAttribute("data-user-id");
      var durationInput = document.getElementById("grant-pro-duration");
      var duration = durationInput ? parseInt(durationInput.value, 10) : 365;

      if (!startActionButton(confirmBtn, "Granting...")) {
        return;
      }

      var unlockModal = lockModalWhileLoading(modalEl, confirmBtn);
      fetch("/admin/users/" + userId + "/grant-pro", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ duration_days: duration || 365 })
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok || !data.success) {
              throw new Error(data.message || "Grant failed");
            }
            return data;
          });
        })
        .then(function (data) {
          return stopActionButton(confirmBtn, {
            success: true,
            successText: "Granted!"
          }).then(function () {
            showAdminToast(data.message || "Pro granted", "success");
            var modalInstance = bootstrap.Modal.getInstance(modalEl);
            if (modalInstance) {
              modalInstance.hide();
            }
            window.location.reload();
          });
        })
        .catch(function (error) {
          stopActionButton(confirmBtn, { success: false, error: true }).then(function () {
            showAdminToast(error && error.message ? error.message : "Grant failed", "error");
          });
        })
        .finally(function () {
          unlockModal();
        });
    });
  }
}

function bindUserProfileModal() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".view-profile-btn");
    if (!button) {
      return;
    }
    event.preventDefault();

    if (!startActionButton(button, "Loading...")) {
      return;
    }

    var userId = button.getAttribute("data-user-id");
    var modalEl = document.getElementById("userProfileModal");
    if (!modalEl) {
      stopActionButton(button, { success: false, error: true });
      return;
    }
    fetch("/admin/users/" + userId + "/profile")
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.message || "Failed to load profile");
          }
          return data;
        });
      })
      .then(function (data) {
        var body = modalEl.querySelector(".modal-body");
        if (!body) {
          return;
        }
        body.innerHTML =
          "<div class='mb-2'><strong>Email:</strong> " + (data.email || "-") + "</div>" +
          "<div class='mb-2'><strong>Plan:</strong> " + (data.plan || "-") + "</div>" +
          "<div class='mb-2'><strong>Downloads:</strong> " + (data.download_stats ? data.download_stats.total_downloads : 0) + "</div>" +
          "<div class='mb-2'><strong>Data:</strong> " + (data.download_stats ? data.download_stats.total_data_bytes : 0) + " bytes</div>" +
          "<div class='mb-2'><strong>Subscriptions:</strong> " + (data.subscription_count || 0) + "</div>" +
          "<div class='mb-2'><strong>API Usage:</strong> " + (data.api_usage_count || 0) + "</div>";
        var modal = new bootstrap.Modal(modalEl);
        modal.show();
        return stopActionButton(button, { success: false, error: false });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Failed to load profile", "error");
        });
      });
  });
}

function initAdminQueue() {
  startQueueAutoRefresh();
  bindRetryButtons();
  bindCancelButtons();
}

function startQueueAutoRefresh() {
  var activeEl = document.querySelector("[data-queue-stat='active']");
  if (!activeEl) {
    return;
  }
  setInterval(function () {
    fetch("/admin/queue/stats")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        updateQueueStat("active", data.active_count);
        updateQueueStat("reserved", data.reserved_count);
        updateQueueStat("downloads", data.downloads_depth);
        updateQueueStat("convert", data.convert_depth);
      })
      .catch(function () {
        return;
      });
  }, 10000);
}

function updateQueueStat(key, value) {
  var el = document.querySelector("[data-queue-stat='" + key + "']");
  if (el) {
    el.textContent = value;
  }
}

function bindRetryButtons() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".retry-job-btn");
    if (!button) {
      return;
    }
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) {
      return;
    }

    if (!startActionButton(button, "Retrying...")) {
      return;
    }

    fetch("/admin/queue/retry/" + jobId, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || !data.success) {
            throw new Error(data.message || "Retry failed");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Requeued" }).then(function () {
          showAdminToast(data.message || "Job requeued", "success");
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Retry failed", "error");
        });
      });
  });
}

function bindCancelButtons() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".cancel-job-btn");
    if (!button) {
      return;
    }
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) {
      return;
    }
    if (!confirm("Cancel this job?") ) {
      return;
    }

    if (!startActionButton(button, "Cancelling...")) {
      return;
    }

    fetch("/admin/queue/cancel/" + jobId, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || !data.success) {
            throw new Error(data.message || "Cancel failed");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Cancelled" }).then(function () {
          showAdminToast(data.message || "Job cancelled", "success");
          var row = button.closest("tr");
          if (row) {
            row.style.opacity = "0.4";
          }
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Cancel failed", "error");
        });
      });
  });
}

function initAdminLogs() {
  bindLogDetailButtons();
}

function bindLogDetailButtons() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".view-log-detail-btn");
    if (!button) {
      return;
    }
    var logId = button.getAttribute("data-log-id");
    var modalEl = document.getElementById("logDetailModal");
    if (!modalEl) {
      return;
    }
    fetch("/admin/logs/" + logId + "/detail")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var pre = modalEl.querySelector("pre");
        if (pre) {
          pre.textContent = JSON.stringify(data.detail_json || {}, null, 2);
        }
        var modal = new bootstrap.Modal(modalEl);
        modal.show();
      })
      .catch(function () {
        showAdminToast("Failed to load log detail", "error");
      });
  });
}

function initAdminSettings() {
  bindReloadProxies();
  bindTestEmail();
  bindClearCache();
  bindTriggerTasks();
  bindDetectFFmpeg();
  bindClearTemp();
}

function bindReloadProxies() {
  var button = document.getElementById("reload-proxies-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!startActionButton(button, "Reloading...")) {
      return;
    }

    fetch("/admin/settings/reload-proxies", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || data.success === false) {
            throw new Error(data.message || "Failed to reload proxies");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Reloaded!" }).then(function () {
          showAdminToast(data.message || "Proxy pool reloaded", "success");
          window.location.reload();
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Failed to reload proxies", "error");
        });
      });
  });
}

function bindTestEmail() {
  var button = document.getElementById("test-email-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!startActionButton(button, "Sending...")) {
      return;
    }

    fetch("/admin/settings/test-email", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || data.success === false) {
            throw new Error(data.message || "Failed to send test email");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Sent" }).then(function () {
          showAdminToast(data.message || "Email sent", "success");
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Failed to send test email", "error");
        });
      });
  });
}

function bindClearCache() {
  var button = document.getElementById("clear-cache-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!confirm("Clear analysis cache?")) {
      return;
    }

    if (!startActionButton(button, "Clearing...")) {
      return;
    }

    fetch("/admin/settings/clear-cache", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || data.success === false) {
            throw new Error(data.message || "Failed to clear cache");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Cleared!" }).then(function () {
          showAdminToast(data.message || "Cache cleared", "success");
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Failed to clear cache", "error");
        });
      });
  });
}

function bindClearTemp() {
  var button = document.getElementById("clear-temp-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!confirm("Clear all temp files?")) {
      return;
    }

    if (!startActionButton(button, "Clearing...")) {
      return;
    }

    fetch("/admin/settings/clear-temp", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || data.success === false) {
            throw new Error(data.message || "Failed to clear temp files");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(button, { success: true, successText: "Cleared" }).then(function () {
          showAdminToast(data.message || "Temp files cleared", "success");
          window.location.reload();
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "Failed to clear temp files", "error");
        });
      });
  });
}

function bindTriggerTasks() {
  var healthBtn = document.getElementById("trigger-health-btn");
  if (healthBtn) {
    healthBtn.addEventListener("click", function () {
      if (!startActionButton(healthBtn, "Dispatching...")) {
        return;
      }

      fetch("/admin/settings/trigger-health-check", {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() }
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok || data.success === false) {
              throw new Error(data.message || "Failed to trigger health check");
            }
            return data;
          });
        })
        .then(function (data) {
          return stopActionButton(healthBtn, { success: true, successText: "Task Dispatched!" }).then(function () {
            showAdminToast(data.message || "Health check started", "info");
          });
        })
        .catch(function (error) {
          stopActionButton(healthBtn, { success: false, error: true }).then(function () {
            showAdminToast(error && error.message ? error.message : "Failed to trigger health check", "error");
          });
        });
    });
  }

  var subBtn = document.getElementById("trigger-sub-poll-btn");
  if (subBtn) {
    subBtn.addEventListener("click", function () {
      if (!startActionButton(subBtn, "Dispatching...")) {
        return;
      }

      fetch("/admin/settings/trigger-sub-poll", {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() }
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok || data.success === false) {
              throw new Error(data.message || "Failed to trigger subscription poll");
            }
            return data;
          });
        })
        .then(function (data) {
          return stopActionButton(subBtn, { success: true, successText: "Task Dispatched!" }).then(function () {
            showAdminToast(data.message || "Subscription poll started", "info");
          });
        })
        .catch(function (error) {
          stopActionButton(subBtn, { success: false, error: true }).then(function () {
            showAdminToast(error && error.message ? error.message : "Failed to trigger subscription poll", "error");
          });
        });
    });
  }
}

function bindDetectFFmpeg() {
  var button = document.getElementById("detect-ffmpeg-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!startActionButton(button, "Detecting...")) {
      return;
    }

    fetch("/admin/settings/detect-ffmpeg", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() }
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || data.success === false) {
            throw new Error(data.message || "FFmpeg check failed");
          }
          return data;
        });
      })
      .then(function (data) {
        var target = document.getElementById("ffmpeg-version-text");
        if (target && data.version) {
          target.textContent = data.version;
        }
        return stopActionButton(button, { success: true, successText: "Detected" }).then(function () {
          showAdminToast("FFmpeg check complete", "info");
        });
      })
      .catch(function (error) {
        stopActionButton(button, { success: false, error: true }).then(function () {
          showAdminToast(error && error.message ? error.message : "FFmpeg check failed", "error");
        });
      });
  });
}

function readJsonScript(id) {
  var el = document.getElementById(id);
  if (!el) {
    return null;
  }
  try {
    return JSON.parse(el.textContent);
  } catch (err) {
    return null;
  }
}

document.addEventListener("DOMContentLoaded", function () {
  initAdminSidebar();
  initAdmin();
  initAdminTooltips();
});

document.addEventListener("themeChanged", function () {
  if (window.location.pathname.indexOf("/admin/dashboard") === 0) {
    initDashboardCharts();
  }
});

function initAdminTooltips() {
  var tooltipTriggerList = [].slice.call(document.querySelectorAll("[data-bs-toggle='tooltip']"));
  tooltipTriggerList.forEach(function (tooltipTriggerEl) {
    new bootstrap.Tooltip(tooltipTriggerEl);
  });
}
