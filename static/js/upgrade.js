"use strict";

var paymentConfig = null;
var activeUpgradeButton = null;

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

function getUpgradeButtons() {
  return [].slice.call(document.querySelectorAll(".upgrade-btn"));
}

function disableAllUpgradeButtons() {
  getUpgradeButtons().forEach(function (button) {
    button.disabled = true;
    button.classList.add("disabled");
  });
}

function enableAllUpgradeButtons() {
  getUpgradeButtons().forEach(function (button) {
    button.disabled = false;
    button.classList.remove("disabled");
  });
  activeUpgradeButton = null;
}

function initUpgrade() {
  var configEl = document.getElementById("payment-config");
  if (configEl) {
    paymentConfig = {
      razorpayKeyId: configEl.dataset.razorpayKey || "",
      userEmail: configEl.dataset.userEmail || "",
      userName: configEl.dataset.userName || "",
      monthlyAmount: parseInt(configEl.dataset.monthlyAmount || "0", 10),
      annualAmount: parseInt(configEl.dataset.annualAmount || "0", 10),
      monthlyDisplay: configEl.dataset.monthlyDisplay || "",
      annualDisplay: configEl.dataset.annualDisplay || "",
      preselectedPlan: configEl.dataset.preselectedPlan || ""
    };
  }

  bindPlanToggle();
  bindUpgradeButtons();
  checkPaymentSuccess();
  applyPreselectedPlan();
}

function bindPlanToggle() {
  var toggle = document.getElementById("plan-toggle");
  if (!toggle) {
    return;
  }
  var monthlyBtn = document.getElementById("toggle-monthly");
  var annualBtn = document.getElementById("toggle-annual");
  var priceEl = document.getElementById("pro-price-display");
  var periodEl = document.getElementById("pro-period-display");
  var savingsBadge = document.getElementById("annual-savings-badge");
  var monthlyCta = document.getElementById("upgrade-monthly-btn");
  var annualCta = document.getElementById("upgrade-annual-btn");

  if (annualBtn) {
    annualBtn.addEventListener("click", function () {
      setToggleState("annual", monthlyBtn, annualBtn, priceEl, periodEl, savingsBadge, monthlyCta, annualCta);
    });
  }
  if (monthlyBtn) {
    monthlyBtn.addEventListener("click", function () {
      setToggleState("monthly", monthlyBtn, annualBtn, priceEl, periodEl, savingsBadge, monthlyCta, annualCta);
    });
  }
}

function applyPreselectedPlan() {
  if (!paymentConfig || !paymentConfig.preselectedPlan) {
    return;
  }
  var monthlyBtn = document.getElementById("toggle-monthly");
  var annualBtn = document.getElementById("toggle-annual");
  var priceEl = document.getElementById("pro-price-display");
  var periodEl = document.getElementById("pro-period-display");
  var savingsBadge = document.getElementById("annual-savings-badge");
  var monthlyCta = document.getElementById("upgrade-monthly-btn");
  var annualCta = document.getElementById("upgrade-annual-btn");
  if (paymentConfig.preselectedPlan === "pro_monthly") {
    setToggleState("monthly", monthlyBtn, annualBtn, priceEl, periodEl, savingsBadge, monthlyCta, annualCta);
  } else if (paymentConfig.preselectedPlan === "pro_annual") {
    setToggleState("annual", monthlyBtn, annualBtn, priceEl, periodEl, savingsBadge, monthlyCta, annualCta);
  }
}

function setToggleState(mode, monthlyBtn, annualBtn, priceEl, periodEl, savingsBadge, monthlyCta, annualCta) {
  if (mode === "annual") {
    if (annualBtn) {
      annualBtn.classList.add("btn-primary");
      annualBtn.classList.remove("btn-outline-primary");
    }
    if (monthlyBtn) {
      monthlyBtn.classList.add("btn-outline-primary");
      monthlyBtn.classList.remove("btn-primary");
    }
    if (priceEl) {
      priceEl.textContent = paymentConfig.annualDisplay;
    }
    if (periodEl) {
      periodEl.textContent = "/year";
    }
    if (monthlyCta) {
      monthlyCta.classList.add("d-none");
    }
    if (annualCta) {
      annualCta.classList.remove("d-none");
    }
    if (savingsBadge) {
      savingsBadge.classList.remove("d-none");
    }
  } else {
    if (monthlyBtn) {
      monthlyBtn.classList.add("btn-primary");
      monthlyBtn.classList.remove("btn-outline-primary");
    }
    if (annualBtn) {
      annualBtn.classList.add("btn-outline-primary");
      annualBtn.classList.remove("btn-primary");
    }
    if (priceEl) {
      priceEl.textContent = paymentConfig.monthlyDisplay;
    }
    if (periodEl) {
      periodEl.textContent = "/month";
    }
    if (annualCta) {
      annualCta.classList.add("d-none");
    }
    if (monthlyCta) {
      monthlyCta.classList.remove("d-none");
    }
    if (savingsBadge) {
      savingsBadge.classList.add("d-none");
    }
  }
}

function bindUpgradeButtons() {
  var buttons = document.querySelectorAll(".upgrade-btn");
  buttons.forEach(function (btn) {
    btn.addEventListener("click", function (event) {
      event.preventDefault();
      var plan = btn.getAttribute("data-plan");
      if (!plan) {
        return;
      }
      if (activeUpgradeButton) {
        return;
      }
      startPayment(plan, btn);
    });
  });
}

function startPayment(planName, triggerButton) {
  if (!startActionButton(triggerButton, "Processing...")) {
    return;
  }

  activeUpgradeButton = triggerButton;
  disableAllUpgradeButtons();

  fetch("/upgrade/create-order", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({ plan: planName })
  })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data.message || "Unable to create order");
        }
        return data;
      });
    })
    .then(function (orderData) {
      openRazorpayCheckout(orderData, planName, triggerButton);
    })
    .catch(function (err) {
      stopActionButton(triggerButton, { success: false, error: true, errorText: "Failed" }).then(function () {
        showToast(err.message || "Unable to start payment", "error");
        enableAllUpgradeButtons();
      });
    });
}

function openRazorpayCheckout(orderData, planName, triggerButton) {
  if (!paymentConfig) {
    stopActionButton(triggerButton, { success: false, error: true, errorText: "Failed" }).then(function () {
      enableAllUpgradeButtons();
      showToast("Payment configuration missing", "error");
    });
    return;
  }

  var accent = getComputedStyle(document.documentElement).getPropertyValue("--color-accent").trim() || "#E94560";
  var options = {
    key: paymentConfig.razorpayKeyId,
    amount: orderData.amount,
    currency: "INR",
    name: "UniversalDL",
    description: orderData.plan_description,
    order_id: orderData.order_id,
    prefill: {
      name: paymentConfig.userName,
      email: paymentConfig.userEmail
    },
    theme: { color: accent },
    modal: {
      ondismiss: function () {
        stopActionButton(triggerButton, { success: false, error: false }).then(function () {
          enableAllUpgradeButtons();
        });
        showToast("Payment cancelled. No charges were made.", "info");
      }
    },
    handler: function (response) {
      verifyPayment(response.razorpay_order_id, response.razorpay_payment_id, response.razorpay_signature, planName, triggerButton);
    }
  };

  var rzp;
  try {
    rzp = new Razorpay(options);
  } catch (error) {
    stopActionButton(triggerButton, { success: false, error: true, errorText: "Failed" }).then(function () {
      enableAllUpgradeButtons();
      showToast("Unable to open payment window", "error");
    });
    return;
  }
  rzp.on("payment.failed", function (response) {
    stopActionButton(triggerButton, { success: false, error: true, errorText: "Payment Failed" }).then(function () {
      enableAllUpgradeButtons();
      showPaymentError(response.error.description, response.error.metadata && response.error.metadata.payment_id);
    });
  });
  stopActionButton(triggerButton, { success: false, error: false }).then(function () {
    disableAllUpgradeButtons();
    rzp.open();
  });
}

function verifyPayment(orderId, paymentId, signature, planName, triggerButton) {
  showPaymentOverlay();
  fetch("/upgrade/verify-payment", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({
      razorpay_order_id: orderId,
      razorpay_payment_id: paymentId,
      razorpay_signature: signature
    })
  })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data.message || "Payment verification failed");
        }
        return data;
      });
    })
    .then(function (data) {
      hidePaymentOverlay();
      stopActionButton(triggerButton, { success: true, successText: "Verified" }).then(function () {
        enableAllUpgradeButtons();
        showToast("Pro plan activated", "success");
        setTimeout(function () {
          window.location.href = data.redirect_url || "/upgrade?success=true";
        }, 900);
      });
    })
    .catch(function (err) {
      hidePaymentOverlay();
      stopActionButton(triggerButton, { success: false, error: true, errorText: "Verification Failed" }).then(function () {
        enableAllUpgradeButtons();
        showPaymentError(err.message, paymentId);
      });
    });
}

function showPaymentError(message, paymentId) {
  var alert = document.getElementById("payment-error");
  if (!alert) {
    alert = document.createElement("div");
    alert.id = "payment-error";
    alert.className = "alert alert-danger mt-3";
    var container = document.getElementById("upgrade-error-container") || document.body;
    container.prepend(alert);
  }
  var extra = paymentId ? " Payment ID: " + paymentId + "." : "";
  alert.innerHTML = "<strong>Payment failed.</strong> " + (message || "Please try again.") + extra + " Contact support at support@universaldl.com.";
  alert.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showPaymentOverlay() {
  var overlay = document.getElementById("payment-overlay");
  if (overlay) {
    overlay.classList.remove("d-none");
    overlay.classList.add("active");
  }
}

function hidePaymentOverlay() {
  var overlay = document.getElementById("payment-overlay");
  if (overlay) {
    overlay.classList.add("d-none");
    overlay.classList.remove("active");
  }
}

function checkPaymentSuccess() {
  var params = new URLSearchParams(window.location.search);
  if (params.get("success") === "true") {
    initConfetti();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function initConfetti() {
  var container = document.getElementById("confetti-container");
  if (!container) {
    return;
  }
  for (var i = 0; i < 50; i += 1) {
    var piece = document.createElement("div");
    piece.className = "confetti-piece";
    piece.style.left = Math.random() * 100 + "%";
    piece.style.animationDelay = Math.random() * 0.5 + "s";
    piece.style.backgroundColor = randomConfettiColor();
    piece.style.transform = "rotate(" + Math.random() * 360 + "deg)";
    container.appendChild(piece);
  }
  setTimeout(function () {
    container.innerHTML = "";
  }, 4000);
}

function randomConfettiColor() {
  var css = getComputedStyle(document.documentElement);
  var colors = [
    css.getPropertyValue("--color-accent").trim(),
    css.getPropertyValue("--color-success").trim(),
    css.getPropertyValue("--color-warning").trim(),
    css.getPropertyValue("--color-accent-2").trim(),
    css.getPropertyValue("--color-info").trim()
  ].filter(Boolean);
  if (!colors.length) {
    return "#E94560";
  }
  return colors[Math.floor(Math.random() * colors.length)];
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

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  return "";
}

document.addEventListener("DOMContentLoaded", initUpgrade);
