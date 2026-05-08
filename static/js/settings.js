"use strict";

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

function initSettings() {
  readActiveTabFromURL();
  bindTabNavigation();
  bindSettingsForms();
  bindDeleteAccountModal();
  bindPasswordStrengthMeter();
  bindPasswordMatchIndicator();
  bindAnonymousModeToggle();
  bindProfileDirtyCheck();
  bindLogoutAll();
  bindExportDataButton();
}

function bindSettingsForms() {
  var profileForm = document.querySelector("form[action='/settings'] input[name='form_name'][value='profile']");
  if (profileForm && profileForm.form) {
    profileForm.form.addEventListener("submit", function (event) {
      var form = profileForm.form;
      if (!form.checkValidity()) {
        form.reportValidity();
        event.preventDefault();
        return;
      }
      var submitBtn = form.querySelector("button[type='submit'], input[type='submit']");
      if (!submitBtn) {
        return;
      }
      if (window.ButtonLoader && window.ButtonLoader.isLoading(submitBtn)) {
        return;
      }
      if (!startActionButton(submitBtn, "Saving...")) {
        event.preventDefault();
      }
    });
  }

  var passwordFormInput = document.querySelector("form[action='/settings'] input[name='form_name'][value='password']");
  if (passwordFormInput && passwordFormInput.form) {
    passwordFormInput.form.addEventListener("submit", function (event) {
      var form = passwordFormInput.form;
      var newPassword = document.getElementById("new-password");
      var confirmPassword = document.getElementById("confirm-password");

      if (!form.checkValidity()) {
        form.reportValidity();
        event.preventDefault();
        return;
      }

      if (newPassword && confirmPassword && newPassword.value !== confirmPassword.value) {
        event.preventDefault();
        var indicator = document.getElementById("password-match-indicator");
        if (indicator) {
          indicator.textContent = "Passwords do not match";
          indicator.className = "small text-danger";
        }
        return;
      }

      var submitBtn = form.querySelector("button[type='submit'], input[type='submit']");
      if (!submitBtn) {
        return;
      }
      if (window.ButtonLoader && window.ButtonLoader.isLoading(submitBtn)) {
        return;
      }
      if (!startActionButton(submitBtn, "Updating...")) {
        event.preventDefault();
      }
    });
  }

  var preferencesFormInput = document.querySelector("form[action='/settings'] input[name='form_name'][value='preferences']");
  if (preferencesFormInput && preferencesFormInput.form) {
    preferencesFormInput.form.addEventListener("submit", function (event) {
      var form = preferencesFormInput.form;
      if (!form.checkValidity()) {
        form.reportValidity();
        event.preventDefault();
        return;
      }
      var submitBtn = form.querySelector("button[type='submit'], input[type='submit']");
      if (!submitBtn) {
        return;
      }
      if (window.ButtonLoader && window.ButtonLoader.isLoading(submitBtn)) {
        return;
      }
      if (!startActionButton(submitBtn, "Saving...")) {
        event.preventDefault();
      }
    });
  }
}

function readActiveTabFromURL() {
  var params = new URLSearchParams(window.location.search);
  var activeTab = params.get("tab") || "profile";
  var trigger = document.querySelector("[data-bs-target='#tab-" + activeTab + "']");
  if (!trigger) {
    return;
  }
  var tab = new bootstrap.Tab(trigger);
  tab.show();
}

function bindTabNavigation() {
  var tabs = document.querySelectorAll("[data-bs-toggle='tab']");
  tabs.forEach(function (tabEl) {
    tabEl.addEventListener("shown.bs.tab", function (event) {
      var target = event.target.getAttribute("data-bs-target") || "";
      var tabName = target.replace("#tab-", "");
      var params = new URLSearchParams(window.location.search);
      params.set("tab", tabName);
      var newUrl = window.location.pathname + "?" + params.toString();
      window.history.pushState({}, "", newUrl);
    });
  });
}

function bindDeleteAccountModal() {
  var modalEl = document.getElementById("deleteAccountModal");
  var confirmInput = document.getElementById("delete-confirm-input");
  var passwordInput = document.getElementById("delete-password-input");
  var confirmButton = document.getElementById("confirm-delete-btn");
  if (!confirmInput || !passwordInput || !confirmButton || !modalEl) {
    return;
  }

  function updateDeleteButton() {
    var valid = confirmInput.value === "DELETE" && passwordInput.value.trim().length > 0;
    confirmButton.disabled = !valid;
  }

  confirmInput.addEventListener("input", updateDeleteButton);
  passwordInput.addEventListener("input", updateDeleteButton);

  confirmButton.addEventListener("click", function () {
    var messageEl = document.getElementById("delete-error-message");
    if (messageEl) {
      messageEl.textContent = "";
    }

    var valid = confirmInput.value === "DELETE" && passwordInput.value.trim().length > 0;
    if (!valid) {
      if (messageEl) {
        messageEl.textContent = "Type DELETE and enter your password.";
      }
      return;
    }

    if (!startActionButton(confirmButton, "Deleting Account...")) {
      return;
    }

    var unlockModal = lockModalWhileLoading(modalEl, confirmButton);

    fetch("/settings/delete-account", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({
        password: passwordInput.value,
        confirm: confirmInput.value
      })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.message || "Delete failed");
          }
          return data;
        });
      })
      .then(function (data) {
        return stopActionButton(confirmButton, { success: true, successText: "Deleted" }).then(function () {
          window.location.href = data.redirect || "/";
        });
      })
      .catch(function (err) {
        stopActionButton(confirmButton, { success: false, error: true, errorText: "Failed - Check Password" });
        if (messageEl) {
          messageEl.textContent = err.message || "Unable to delete account";
        }
      })
      .finally(function () {
        unlockModal();
      });
  });
}

function bindPasswordStrengthMeter() {
  var passwordInput = document.getElementById("new-password");
  var strengthFill = document.getElementById("password-strength-fill");
  if (!passwordInput || !strengthFill) {
    return;
  }

  var requirements = {
    length: document.getElementById("req-length"),
    upper: document.getElementById("req-upper"),
    number: document.getElementById("req-number"),
    special: document.getElementById("req-special")
  };

  passwordInput.addEventListener("input", function () {
    var password = passwordInput.value || "";
    var score = calculateStrength(password);
    updateStrengthBar(score, strengthFill);
    updateRequirement(requirements.length, password.length >= 10);
    updateRequirement(requirements.upper, /[A-Z]/.test(password));
    updateRequirement(requirements.number, /[0-9]/.test(password));
    updateRequirement(requirements.special, /[^A-Za-z0-9]/.test(password));
  });
}

function bindPasswordMatchIndicator() {
  var passwordInput = document.getElementById("new-password");
  var confirmInput = document.getElementById("confirm-password");
  var indicator = document.getElementById("password-match-indicator");
  if (!passwordInput || !confirmInput || !indicator) {
    return;
  }

  function updateIndicator() {
    if (!confirmInput.value) {
      indicator.textContent = "";
      indicator.className = "small text-muted";
      return;
    }
    if (confirmInput.value === passwordInput.value) {
      indicator.textContent = "Passwords match";
      indicator.className = "small text-success";
    } else {
      indicator.textContent = "Passwords do not match";
      indicator.className = "small text-danger";
    }
  }

  confirmInput.addEventListener("input", updateIndicator);
  passwordInput.addEventListener("input", updateIndicator);
}

function bindAnonymousModeToggle() {
  var toggle = document.getElementById("anonymous-mode-toggle");
  if (!toggle) {
    return;
  }
  toggle.addEventListener("change", function () {
    var previousChecked = !toggle.checked;
    toggle.disabled = true;
    toggle.style.opacity = "0.6";

    fetch("/settings/toggle-anonymous", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({ anonymous_mode: toggle.checked })
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Unable to update anonymous mode");
        }
        return response.json();
      })
      .then(function () {
        toggle.disabled = false;
        toggle.style.opacity = "";
      })
      .catch(function () {
        toggle.disabled = false;
        toggle.style.opacity = "";
        toggle.checked = previousChecked;
        showToast("Unable to update anonymous mode", "error");
      });
  });
}

function bindProfileDirtyCheck() {
  var emailInput = document.getElementById("profile-email");
  var warning = document.getElementById("email-change-warning");
  if (!emailInput || !warning) {
    return;
  }
  var original = emailInput.value;
  emailInput.addEventListener("input", function () {
    if (emailInput.value !== original) {
      warning.classList.remove("d-none");
    } else {
      warning.classList.add("d-none");
    }
  });
}

function bindLogoutAll() {
  var button = document.getElementById("logout-all-btn");
  if (!button) {
    return;
  }
  button.addEventListener("click", function () {
    if (!startActionButton(button, "Working...")) {
      return;
    }
    showToast("Not implemented yet", "info");
    setTimeout(function () {
      stopActionButton(button, { success: false, error: false });
    }, 400);
  });
}

function bindExportDataButton() {
  var exportButton = document.querySelector("a[href='/settings/download-data']");
  if (!exportButton) {
    return;
  }

  exportButton.addEventListener("click", function (event) {
    event.preventDefault();
    if (!startActionButton(exportButton, "Preparing...")) {
      return;
    }
    window.location.href = exportButton.getAttribute("href") || "/settings/download-data";
    setTimeout(function () {
      stopActionButton(exportButton, { success: false, error: false });
    }, 3000);
  });
}

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
  if (!hasUpper && !hasNumber && !hasSpecial) {
    return 2;
  }
  if (password.length >= 10 && (hasUpper || hasNumber)) {
    return 3;
  }
  if (password.length >= 10 && hasUpper && hasNumber && hasSpecial) {
    return 4;
  }
  return 2;
}

function updateStrengthBar(score, fill) {
  var widths = ["0%", "25%", "50%", "75%", "100%"];
  fill.style.width = widths[score] || "0%";
  fill.className = "strength-fill";
  if (score <= 1) {
    fill.classList.add("strength-weak");
  } else if (score === 2) {
    fill.classList.add("strength-fair");
  } else if (score === 3) {
    fill.classList.add("strength-good");
  } else if (score >= 4) {
    fill.classList.add("strength-strong");
  }
}

function updateRequirement(el, met) {
  if (!el) {
    return;
  }
  el.classList.toggle("text-success", met);
  el.classList.toggle("text-muted", !met);
  var icon = el.querySelector("i");
  if (icon) {
    icon.className = met ? "bi bi-check-circle" : "bi bi-x-circle";
  }
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

document.addEventListener("DOMContentLoaded", initSettings);
