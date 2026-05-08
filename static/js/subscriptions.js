"use strict";

var toastContainer = null;
var validatedChannel = null;
var validationJobId = null;
var deleteModal = null;
var pendingDeleteId = null;
var pendingDeleteButton = null;
var deleteModalElement = null;

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

function initSubscriptions() {
  initToasts();
  initTooltips();

  var body = document.body;
  var upgradeRequired = body && body.getAttribute("data-upgrade-required") === "1";
  if (upgradeRequired) {
    return;
  }

  bindValidateChannel();
  bindAddSubscriptionForm();
  bindToggleSwitches();
  bindTestNowButtons();
  bindDeleteButtons();
  bindFrequencyNote();
}

function initTooltips() {
  var tooltipTriggerList = [].slice.call(document.querySelectorAll("[data-bs-toggle='tooltip']"));
  tooltipTriggerList.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });
}

function bindValidateChannel() {
  var btn = document.getElementById("validate-channel-btn");
  var input = document.getElementById("channel-url");
  var result = document.getElementById("validation-result");
  var optionsRow = document.getElementById("subscription-options");
  if (!btn || !input || !result || !optionsRow) {
    return;
  }

  btn.addEventListener("click", function () {
    if (!startActionButton(btn, "Validating...")) {
      return;
    }

    var url = (input.value || "").trim();
    if (!url || url.indexOf("http") !== 0) {
      result.className = "text-danger small";
      result.textContent = "Please enter a valid URL starting with http.";
      stopActionButton(btn, { success: false, error: true, errorText: "Not Found" });
      return;
    }

    result.className = "text-muted small";
    result.textContent = "Checking channel...";
    validatedChannel = null;

    fetch("/download/analyze", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({ url: url })
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json().then(function (data) {
            throw new Error(data.message || "Validation failed");
          });
        }
        return response.json();
      })
      .then(function (data) {
        validationJobId = data.job_id;
        setTimeout(function () {
          fetch("/download/info/" + validationJobId)
            .then(function (infoRes) {
              return infoRes.json();
            })
            .then(function (payload) {
              if (!payload || !payload.job || !payload.job.platform) {
                throw new Error("Could not detect channel platform");
              }
              validatedChannel = {
                url: url,
                platform: payload.job.platform
              };
              result.className = "text-success small";
              result.textContent = "Detected: " + payload.job.platform + " channel";
              optionsRow.classList.remove("d-none");
              stopActionButton(btn, { success: true, successText: "Validated!" });
            })
            .catch(function (err) {
              result.className = "text-danger small";
              result.textContent = err.message || "Validation failed";
              stopActionButton(btn, { success: false, error: true, errorText: "Not Found" });
            })
            .finally(function () {
              return;
            });
        }, 3000);
      })
      .catch(function (err) {
        result.className = "text-danger small";
        result.textContent = err.message || "Validation failed";
        stopActionButton(btn, { success: false, error: true, errorText: "Not Found" });
      });
  });
}

function bindAddSubscriptionForm() {
  var form = document.getElementById("add-subscription-form");
  if (!form) {
    return;
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    if (!validatedChannel) {
      showToast("Please validate the channel URL first", "info");
      return;
    }

    var payload = {
      channel_url: document.getElementById("channel-url").value.trim(),
      frequency: getSelectValue("frequency-select"),
      quality: getSelectValue("quality-select"),
      format: getSelectValue("format-select"),
      notification_email: getCheckboxValue("notification-email")
    };

    var submitBtn = document.getElementById("subscribe-btn");
    var alreadyLoading = window.ButtonLoader && window.ButtonLoader.isLoading(submitBtn);
    if (!alreadyLoading && !startActionButton(submitBtn, "Subscribing...")) {
      return;
    }

    fetch("/subscriptions/add", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify(payload)
    })
      .then(function (response) {
        if (response.status === 403 || response.status === 409) {
          return response.json().then(function (data) {
            var error = new Error(data.message || "Request failed");
            error.status = response.status;
            throw error;
          });
        }
        if (!response.ok) {
          return response.json().then(function (data) {
            throw new Error(data.message || "Subscription failed");
          });
        }
        return response.json();
      })
      .then(function (data) {
        return stopActionButton(submitBtn, { success: true, successText: "Subscribed!" }).then(function () {
          showToast("Subscribed to " + data.channel_name + "!", "success");
          insertSubscriptionCard({
            id: data.subscription_id,
            channel_name: data.channel_name,
            platform: data.platform,
            frequency: payload.frequency,
            quality: payload.quality,
            format: payload.format,
            total_downloaded: 0,
            last_checked_at: null,
            next_check_at: null,
            last_download_at: null,
            known_count: 0,
            is_active: true,
            channel_url: payload.channel_url
          });
          updateSubscriptionCount(1);
          resetSubscriptionForm();
        });
      })
      .catch(function (err) {
        var errorText = "Failed";
        if (err && err.status === 403) {
          errorText = "Upgrade Required";
        } else if (err && err.status === 409) {
          errorText = "Already Added";
        }

        stopActionButton(submitBtn, { success: false, error: true, errorText: errorText }).then(function () {
          showToast(err.message || "Unable to subscribe", "error");
        });
      })
      .finally(function () {
        return;
      });
  });
}

function insertSubscriptionCard(sub) {
  var grid = document.getElementById("subscription-grid");
  if (!grid) {
    return;
  }
  var empty = document.getElementById("subscription-empty");
  if (empty && empty.parentNode) {
    empty.parentNode.removeChild(empty);
  }
  var wrapper = document.createElement("div");
  wrapper.className = "col-md-6 col-lg-4";
  wrapper.innerHTML = buildSubscriptionCard(sub);
  grid.prepend(wrapper);
}

function buildSubscriptionCard(sub) {
  var activeClass = sub.is_active ? "subscription-card-active" : "subscription-card-paused";
  var statusText = sub.is_active ? "Active" : "Paused";
  var checked = sub.is_active ? "checked" : "";
  var frequency = (sub.frequency || "daily").replace(/^\w/, function (c) { return c.toUpperCase(); });
  return (
    "<div class='card h-100 " + activeClass + " subscription-card' data-sub-id='" + sub.id + "'>" +
      "<div class='card-header d-flex justify-content-between align-items-center'>" +
        "<span class='status-badge status-disabled text-uppercase'>" + (sub.platform || "") + "</span>" +
        "<div class='form-check form-switch m-0'>" +
          "<input class='form-check-input sub-toggle-switch' type='checkbox' data-sub-id='" + sub.id + "' " + checked + ">" +
        "</div>" +
      "</div>" +
      "<div class='card-body'>" +
        "<h5 class='fw-semibold'>" + (sub.channel_name || "Channel") + "</h5>" +
        "<div class='small text-muted text-truncate' title='" + (sub.channel_url || "") + "'>" + (sub.channel_url || "") + "</div>" +
        "<div class='row g-2 mt-2 small'>" +
          "<div class='col-6'>Frequency: " + frequency + "</div>" +
          "<div class='col-6'>Quality: <span class='status-badge status-disabled'>" + (sub.quality || "") + "</span></div>" +
          "<div class='col-6'>Format: <span class='status-badge status-disabled'>" + (sub.format || "").toUpperCase() + "</span></div>" +
          "<div class='col-6'>Total: " + (sub.total_downloaded || 0) + " items</div>" +
        "</div>" +
        "<hr>" +
        "<div class='small text-muted'>Last checked: " + formatRelativeTime(sub.last_checked_at) + "</div>" +
        "<div class='small text-muted'>Next check: " + formatRelativeTime(sub.next_check_at) + "</div>" +
        "<div class='small text-muted'>Last download: " + formatRelativeTime(sub.last_download_at) + "</div>" +
        "<div class='small text-muted mt-1'>" + (sub.known_count || 0) + " items tracked</div>" +
        "<div class='small mt-2 fw-semibold sub-status-text'>" + statusText + "</div>" +
      "</div>" +
      "<div class='card-footer d-flex justify-content-between align-items-center gap-2'>" +
        "<button class='btn btn-sm btn-outline-primary test-now-btn' data-sub-id='" + sub.id + "'>Test Now</button>" +
        "<button class='btn btn-sm btn-outline-danger delete-sub-btn' data-sub-id='" + sub.id + "'>Delete</button>" +
      "</div>" +
    "</div>"
  );
}

function bindToggleSwitches() {
  document.addEventListener("change", function (event) {
    var toggle = event.target.closest(".sub-toggle-switch");
    if (!toggle) {
      return;
    }
    var subId = toggle.getAttribute("data-sub-id");
    if (!subId) {
      return;
    }
    var previousChecked = !toggle.checked;
    toggle.disabled = true;
    toggle.style.opacity = "0.6";

    var card = document.querySelector(".subscription-card[data-sub-id='" + subId + "']");
    fetch("/subscriptions/toggle/" + subId, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken()
      }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Toggle failed");
        }
        return response.json();
      })
      .then(function (data) {
        var isActive = data.is_active;
        if (card) {
          card.classList.toggle("subscription-card-active", isActive);
          card.classList.toggle("subscription-card-paused", !isActive);
          var statusText = card.querySelector(".sub-status-text");
          if (statusText) {
            statusText.textContent = isActive ? "Active" : "Paused";
          }
        }
        toggle.disabled = false;
        toggle.style.opacity = "";
        showToast(data.message || "Subscription updated", "success");
      })
      .catch(function () {
        toggle.disabled = false;
        toggle.style.opacity = "";
        toggle.checked = previousChecked;
        showToast("Unable to update subscription", "error");
      });
  });
}

function bindTestNowButtons() {
  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".test-now-btn");
    if (!btn) {
      return;
    }
    var subId = btn.getAttribute("data-sub-id");
    if (!subId) {
      return;
    }
    if (!startActionButton(btn, "Checking...")) {
      return;
    }

    fetch("/subscriptions/test/" + subId, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken()
      }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Test failed");
        }
        return response.json();
      })
      .then(function (data) {
        stopActionButton(btn, { success: true, successText: "Check Sent!" }).then(function () {
          showToast(data.message || "Checking for new content now", "info");
        });
      })
      .catch(function () {
        stopActionButton(btn, { success: false, error: true, errorText: "Error" }).then(function () {
          showToast("Unable to start test", "error");
        });
      });
  });
}

function bindDeleteButtons() {
  var modalEl = document.getElementById("deleteSubscriptionModal");
  if (modalEl) {
    deleteModal = new bootstrap.Modal(modalEl);
    deleteModalElement = modalEl;
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".delete-sub-btn");
    if (!btn) {
      return;
    }
    pendingDeleteId = btn.getAttribute("data-sub-id");
    pendingDeleteButton = btn;
    if (deleteModal) {
      deleteModal.show();
    }
  });

  var confirmBtn = document.getElementById("confirm-delete-sub-btn");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", function () {
      if (!pendingDeleteId) {
        return;
      }

      if (!startActionButton(confirmBtn, "Removing...")) {
        return;
      }

      if (pendingDeleteButton) {
        startActionButton(pendingDeleteButton, "");
      }

      var unlockModal = lockModalWhileLoading(deleteModalElement, confirmBtn);

      deleteSubscription(pendingDeleteId, confirmBtn, pendingDeleteButton)
        .finally(function () {
          pendingDeleteId = null;
          pendingDeleteButton = null;
          unlockModal();
        });
    });
  }
}

function deleteSubscription(subId, confirmBtn, sourceButton) {
  return fetch("/subscriptions/delete/" + subId, {
    method: "POST",
    headers: {
      "X-CSRFToken": getCsrfToken()
    }
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("Delete failed");
      }
      return response.json();
    })
    .then(function () {
      var stops = [];
      if (confirmBtn) {
        stops.push(stopActionButton(confirmBtn, { success: true, successText: "Removed" }));
      }
      if (sourceButton) {
        stops.push(stopActionButton(sourceButton, { success: false, error: false }));
      }

      return Promise.all(stops).then(function () {
        if (deleteModal) {
          deleteModal.hide();
        }
        var card = document.querySelector(".subscription-card[data-sub-id='" + subId + "']");
        if (card) {
          var wrapper = card.closest(".col-md-6") || card.parentNode;
          fadeOutAndRemove(wrapper || card);
        }
        updateSubscriptionCount(-1);
        showToast("Subscription removed", "success");
      });
    })
    .catch(function () {
      var restores = [];
      if (confirmBtn) {
        restores.push(stopActionButton(confirmBtn, { success: false, error: true, errorText: "Error" }));
      }
      if (sourceButton) {
        restores.push(stopActionButton(sourceButton, { success: false, error: false }));
      }
      return Promise.all(restores).then(function () {
        showToast("Unable to remove subscription", "error");
      });
    });
}

function bindFrequencyNote() {
  var select = document.getElementById("frequency-select");
  var note = document.getElementById("frequency-note");
  if (!select || !note) {
    return;
  }
  var update = function () {
    var value = select.options[select.selectedIndex].text;
    note.textContent = "We will check this channel " + value.toLowerCase() + " and download new content.";
  };
  select.addEventListener("change", update);
  update();
}

function resetSubscriptionForm() {
  var form = document.getElementById("add-subscription-form");
  if (form) {
    form.reset();
  }
  validatedChannel = null;
  var optionsRow = document.getElementById("subscription-options");
  if (optionsRow) {
    optionsRow.classList.add("d-none");
  }
  var result = document.getElementById("validation-result");
  if (result) {
    result.textContent = "";
  }
}

function updateSubscriptionCount(delta) {
  var countEl = document.getElementById("subscription-count");
  if (!countEl) {
    return;
  }
  var current = parseInt(countEl.getAttribute("data-count") || countEl.textContent, 10) || 0;
  var next = Math.max(0, current + delta);
  countEl.textContent = next;
  countEl.setAttribute("data-count", next);

  var maxEl = document.getElementById("subscription-max");
  var maxValue = maxEl ? parseInt(maxEl.getAttribute("data-max"), 10) : 0;
  if (maxEl && maxValue > 0) {
    maxEl.textContent = maxValue;
    var bar = document.getElementById("subscription-progress");
    if (bar) {
      var pct = Math.min(100, Math.round((next / maxValue) * 100));
      bar.style.width = pct + "%";
    }
  }
}

function getSelectValue(id) {
  var el = document.getElementById(id);
  return el ? el.value : "";
}

function getCheckboxValue(id) {
  var el = document.getElementById(id);
  return el ? el.checked : false;
}

function formatRelativeTime(value) {
  if (!value) {
    return "Never";
  }
  var date = new Date(value);
  if (isNaN(date.getTime())) {
    return value;
  }
  var diff = Math.floor((new Date().getTime() - date.getTime()) / 1000);
  if (diff < 60) {
    return "Just now";
  }
  var minutes = Math.floor(diff / 60);
  if (minutes < 60) {
    return minutes + " minutes ago";
  }
  var hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return hours + " hours ago";
  }
  var days = Math.floor(hours / 24);
  if (days < 7) {
    return days + " days ago";
  }
  return date.toISOString().split("T")[0];
}

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  return "";
}

function initToasts() {
  toastContainer = document.getElementById("toast-container");
  if (!toastContainer) {
    toastContainer = document.createElement("div");
    toastContainer.className = "toast-container position-fixed top-0 end-0 p-3";
    toastContainer.id = "toast-container";
    document.body.appendChild(toastContainer);
  }
}

function showToast(message, type) {
  if (!toastContainer) {
    initToasts();
  }
  var toastEl = document.createElement("div");
  var tone = "success";
  if (type === "error") {
    tone = "error";
  } else if (type === "info") {
    tone = "info";
  }
  toastEl.className = "toast align-items-center theme-toast theme-toast-" + tone;
  toastEl.setAttribute("role", "alert");
  toastEl.setAttribute("aria-live", "assertive");
  toastEl.setAttribute("aria-atomic", "true");
  toastEl.innerHTML = "<div class='d-flex'><div class='toast-body'>" + message + "</div><button type='button' class='btn-close me-2 m-auto' data-bs-dismiss='toast' aria-label='Close'></button></div>";
  toastContainer.appendChild(toastEl);
  var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
  toast.show();
  toastEl.addEventListener("hidden.bs.toast", function () {
    toastEl.remove();
  });
}

function fadeOutAndRemove(element) {
  if (!element) {
    return;
  }
  element.style.transition = "opacity 0.4s ease";
  element.style.opacity = "0";
  setTimeout(function () {
    if (element.parentNode) {
      element.parentNode.removeChild(element);
    }
  }, 400);
}

document.addEventListener("DOMContentLoaded", initSubscriptions);
