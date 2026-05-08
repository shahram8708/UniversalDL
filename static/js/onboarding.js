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

function initOnboarding() {
  bindOptionCards();
  readSavedSelections();
  bindExtensionInstall();
  bindStepSubmits();
  bindSkipSetupLinks();
}

function bindOptionCards() {
  document.addEventListener("click", function (event) {
    var card = event.target.closest(".option-card");
    if (!card) {
      return;
    }
    var group = card.getAttribute("data-radio-name");
    var value = card.getAttribute("data-radio-value");
    if (!group || !value) {
      return;
    }
    var input = document.querySelector("input[name='" + group + "'][value='" + value + "']");
    if (input) {
      input.checked = true;
    }
    var siblings = document.querySelectorAll(".option-card[data-radio-name='" + group + "']");
    siblings.forEach(function (el) {
      el.classList.remove("selected");
    });
    card.classList.add("selected");
  });
}

function readSavedSelections() {
  var radios = document.querySelectorAll(".option-card");
  radios.forEach(function (card) {
    var group = card.getAttribute("data-radio-name");
    var value = card.getAttribute("data-radio-value");
    var input = document.querySelector("input[name='" + group + "'][value='" + value + "']");
    if (input && input.checked) {
      card.classList.add("selected");
    }
  });
}

function bindExtensionInstall() {
  var buttons = document.querySelectorAll(".mark-installed-btn");
  buttons.forEach(function (btn) {
    btn.addEventListener("click", function (event) {
      event.preventDefault();
      var installed = btn.getAttribute("data-installed") === "true";
      installed = !installed;
      btn.setAttribute("data-installed", installed ? "true" : "false");
      btn.classList.toggle("btn-outline-success", installed);
      btn.classList.toggle("btn-outline-primary", !installed);
      btn.textContent = installed ? "Installed" : "Install Extension";
      try {
        localStorage.setItem(btn.id + "-installed", installed ? "true" : "false");
      } catch (err) {
        return;
      }
    });

    try {
      var saved = localStorage.getItem(btn.id + "-installed");
      if (saved === "true") {
        btn.setAttribute("data-installed", "true");
        btn.classList.remove("btn-outline-primary");
        btn.classList.add("btn-outline-success");
        btn.textContent = "Installed";
      }
    } catch (err) {
      return;
    }
  });
}

function bindStepSubmits() {
  var steps = [
    { selector: "form[action='/onboarding/step/1']", loadingText: "Saving...", validate: validateStep1 },
    { selector: "form[action='/onboarding/step/2']", loadingText: "Saving..." },
    { selector: "form[action='/onboarding/complete']", loadingText: "Finishing Setup..." }
  ];

  steps.forEach(function (step) {
    var form = document.querySelector(step.selector);
    if (!form || form._udlSubmitBound) {
      return;
    }

    form._udlSubmitBound = true;
    form.addEventListener("submit", function (event) {
      if (typeof step.validate === "function" && !step.validate()) {
        event.preventDefault();
        return;
      }

      if (!form.checkValidity()) {
        form.reportValidity();
        event.preventDefault();
        return;
      }

      var submitButton = form.querySelector("button[type='submit'], input[type='submit']");
      if (!submitButton) {
        return;
      }

      if (window.ButtonLoader && window.ButtonLoader.isLoading(submitButton)) {
        return;
      }

      if (!startActionButton(submitButton, step.loadingText)) {
        event.preventDefault();
      }
    });
  });
}

function bindSkipSetupLinks() {
  var skipLinks = document.querySelectorAll("a[href='/onboarding/skip']");
  skipLinks.forEach(function (link) {
    if (link._udlBound) {
      return;
    }
    link._udlBound = true;

    link.addEventListener("click", function (event) {
      event.preventDefault();
      startActionButton(link, "Skipping...");
      window.location.href = link.getAttribute("href") || "/onboarding/skip";
    });
  });
}

function validateStep1() {
  var formatSelected = document.querySelector("input[name='default_format']:checked");
  var qualitySelected = document.querySelector("input[name='default_quality']:checked");
  var formatError = document.getElementById("format-error");
  var qualityError = document.getElementById("quality-error");
  var valid = true;
  if (!formatSelected) {
    valid = false;
    if (formatError) {
      formatError.textContent = "Please select a format.";
      formatError.classList.remove("d-none");
    }
  } else if (formatError) {
    formatError.classList.add("d-none");
  }
  if (!qualitySelected) {
    valid = false;
    if (qualityError) {
      qualityError.textContent = "Please select a quality.";
      qualityError.classList.remove("d-none");
    }
  } else if (qualityError) {
    qualityError.classList.add("d-none");
  }
  return valid;
}

document.addEventListener("DOMContentLoaded", initOnboarding);
