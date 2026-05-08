"use strict";

function createProgressTracker(jobId, callbacks) {
  var retries = 0;
  var source = null;

  function connect() {
    source = new EventSource("/download/progress/" + jobId);
    source.onmessage = function (event) {
      var data = JSON.parse(event.data);
      if (callbacks && callbacks.onStatusChange) {
        callbacks.onStatusChange(data);
      }
      if (data.status === "complete" && callbacks.onComplete) {
        callbacks.onComplete(data);
      } else if (data.status === "failed" && callbacks.onFailed) {
        callbacks.onFailed(data);
      } else if (callbacks.onProgress) {
        callbacks.onProgress(data);
      }
    };
    source.onerror = function () {
      retries += 1;
      if (source) {
        source.close();
      }
      if (retries <= 3) {
        setTimeout(connect, 2000);
      }
    };
  }

  connect();

  return {
    close: function () {
      if (source) {
        source.close();
      }
    }
  };
}

function formatSpeed(bps) {
  if (!bps || bps < 1024) {
    return (bps || 0) + " B/s";
  }
  if (bps < 1048576) {
    return (bps / 1024).toFixed(1) + " KB/s";
  }
  return (bps / 1048576).toFixed(2) + " MB/s";
}

function formatETA(seconds) {
  if (!seconds || isNaN(seconds) || seconds <= 0) {
    return "Calculating...";
  }
  if (seconds < 60) {
    return seconds + "s remaining";
  }
  if (seconds < 3600) {
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return mins + "m " + secs + "s remaining";
  }
  var hours = Math.floor(seconds / 3600);
  var remainder = Math.floor((seconds % 3600) / 60);
  return hours + "h " + remainder + "m";
}

function animateProgressBar(elementId, percentage) {
  var bar = document.getElementById(elementId);
  if (!bar) {
    return;
  }
  var start = parseFloat(bar.style.width) || 0;
  var end = Math.min(percentage, 100);
  var startTime = null;

  function step(timestamp) {
    if (!startTime) {
      startTime = timestamp;
    }
    var progress = Math.min((timestamp - startTime) / 300, 1);
    var current = start + (end - start) * progress;
    bar.style.width = current.toFixed(1) + "%";
    bar.setAttribute("aria-valuenow", current.toFixed(1));
    if (progress < 1) {
      requestAnimationFrame(step);
    }
  }

  requestAnimationFrame(step);
}
