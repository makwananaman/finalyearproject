/* Meetings AI */
document.addEventListener("DOMContentLoaded", function () {
  var tabButtons = document.querySelectorAll("[data-tab-target]");

  if (tabButtons.length) {
    var form = document.getElementById("meetings-form");
    var fileInput = document.getElementById("meeting-file");
    var fileLabel = document.getElementById("meeting-file-label");
    var alertBox = document.getElementById("meetings-inline-alert");
    var submitButton = document.getElementById("meetings-submit-button");
    var ajaxStatus = document.getElementById("meeting-ajax-status");
    var statusText = document.getElementById("status-text");

    var startStatusUpdates = function () {
      if (!ajaxStatus || !statusText) return;
      ajaxStatus.classList.remove("is-hidden");
      statusText.textContent = "Connecting...";

      return setInterval(function () {
        fetch("/meetings/status/", {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        })
          .then(function (res) {
            return res.json();
          })
          .then(function (data) {
            if (data && data.status) {
              statusText.textContent = data.status;
            }
          })
          .catch(function () {});
      }, 1500);
    };

    var stopStatusUpdates = function (intervalId) {
      if (intervalId) clearInterval(intervalId);
      if (ajaxStatus) ajaxStatus.classList.add("is-hidden");
    };

    var summaryCount = document.getElementById("summary-count");
    var tasksCount = document.getElementById("tasks-count");
    var priorityCount = document.getElementById("priority-count");
    var summaryContent = document.getElementById("summary-content");
    var tasksContent = document.getElementById("tasks-content");
    var priorityContent = document.getElementById("priority-content");

    var activateTab = function (targetId) {
      tabButtons.forEach(function (button) {
        var isActive = button.getAttribute("data-tab-target") === targetId;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-selected", isActive ? "true" : "false");
      });

      document
        .querySelectorAll(".meetings-tab-panel")
        .forEach(function (panel) {
          var isActive = panel.id === targetId;
          panel.classList.toggle("is-active", isActive);
          panel.hidden = !isActive;
        });
    };

    tabButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        activateTab(button.getAttribute("data-tab-target"));
      });
    });

    var escapeHtml = function (value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    };

    var setAlertMessage = function (message) {
      if (!alertBox) return;
      if (message) {
        alertBox.textContent = message;
        alertBox.classList.remove("is-hidden");
      } else {
        alertBox.textContent = "";
        alertBox.classList.add("is-hidden");
      }
    };

    var updateCount = function (element, text) {
      if (!element) return;
      if (text) {
        element.textContent = text;
        element.classList.remove("is-hidden");
      } else {
        element.textContent = "";
        element.classList.add("is-hidden");
      }
    };

    var renderSummary = function (summary) {
      if (!summaryContent) return;
      if (summary.length) {
        summaryContent.innerHTML =
          '<ul class="meetings-summary-list">' +
          summary
            .map(function (point) {
              return "<li>" + escapeHtml(point) + "</li>";
            })
            .join("") +
          "</ul>";
      } else {
        summaryContent.innerHTML =
          '<p class="meetings-empty-copy">Run the analysis to view a clean summary of decisions and important discussion.</p>';
      }
    };

    var renderTasks = function (tasks) {
      if (!tasksContent) return;
      if (tasks.length) {
        tasksContent.innerHTML =
          '<div class="meetings-task-list">' +
          tasks
            .map(function (task) {
              var priorityClass =
                "meetings-priority-" +
                String(task.priority || "medium").toLowerCase();
              return (
                '<article class="meetings-task-row">' +
                '<div class="meetings-task-main">' +
                "<strong>" +
                escapeHtml(task.task || "") +
                "</strong>" +
                "<span>" +
                escapeHtml(task.owner || "") +
                "</span>" +
                "</div>" +
                '<span class="meetings-task-priority ' +
                priorityClass +
                '">' +
                escapeHtml(task.priority || "Medium") +
                "</span>" +
                "</article>"
              );
            })
            .join("") +
          "</div>";
      } else {
        tasksContent.innerHTML =
          '<p class="meetings-empty-copy">Action items will appear here once the meeting transcript is processed.</p>';
      }
    };

    var renderPriorityTasks = function (tasks) {
      if (!priorityContent) return;
      if (tasks.length) {
        priorityContent.innerHTML =
          '<div class="meetings-priority-grid">' +
          tasks
            .map(function (task) {
              return (
                '<article class="meetings-priority-card">' +
                '<span class="meetings-priority-dot" aria-hidden="true"></span>' +
                "<div>" +
                "<strong>" +
                escapeHtml(task.task || "") +
                "</strong>" +
                "<p>" +
                escapeHtml(task.owner || "") +
                "</p>" +
                "</div>" +
                "</article>"
              );
            })
            .join("") +
          "</div>";
      } else {
        priorityContent.innerHTML =
          '<p class="meetings-empty-copy">High-priority tasks will be isolated here after analysis.</p>';
      }
    };

    var renderMeetingResult = function (payload) {
      var summary = Array.isArray(payload.summary) ? payload.summary : [];
      var tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
      var highPriority = Array.isArray(payload.high_priority_tasks)
        ? payload.high_priority_tasks
        : [];

      setAlertMessage(payload.error || "");
      updateCount(
        summaryCount,
        summary.length ? summary.length + " points" : "",
      );
      updateCount(tasksCount, tasks.length ? tasks.length + " tasks" : "");
      updateCount(
        priorityCount,
        highPriority.length ? highPriority.length + " urgent" : "",
      );

      renderSummary(summary);
      renderTasks(tasks);
      renderPriorityTasks(highPriority);
    };

    if (fileInput && fileLabel) {
      fileInput.addEventListener("change", function () {
        fileLabel.textContent =
          fileInput.files && fileInput.files[0]
            ? fileInput.files[0].name
            : "Supports `.txt` and `.mp3`";
      });
    }

    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();

        var formData = new FormData(form);
        submitButton.disabled = true;
        submitButton.textContent = "Analyzing...";
        setAlertMessage("");
        var statusInterval = startStatusUpdates();

        fetch(form.action || window.location.href, {
          method: "POST",
          body: formData,
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        })
          .then(function (response) {
            if (!response.ok) {
              throw new Error("Request failed with status " + response.status);
            }
            return response.json();
          })
          .then(function (payload) {
            renderMeetingResult(payload);
          })
          .catch(function () {
            setAlertMessage(
              "Meeting processing failed. Please check your input.",
            );
          })
          .finally(function () {
            stopStatusUpdates(statusInterval);
            submitButton.disabled = false;
            submitButton.textContent = "Analyze Meeting";
          });
      });
    }

    activateTab(tabButtons[0].getAttribute("data-tab-target"));
  }

  /* Email AI — chat UI */
  var emailRoot = document.querySelector(".email-ai-page");
  if (!emailRoot) {
    return;
  }

  var chatForm = document.getElementById("chat-form");
  var chatInput = document.getElementById("user_input");
  var chatMessages = document.getElementById("chat-messages");
  var chatStatus = document.getElementById("chat-status");
  var sendEmailForm = document.getElementById("send-email-form");
  var draftText = document.getElementById("draft_text");
  var newChatForm = document.getElementById("new-chat-form");
  var newChatBtn = document.querySelector(".app-sidebar-new-chat");
  var draftModal = document.getElementById("email-ai-draft-modal");
  var draftModalClose = draftModal
    ? draftModal.querySelector(".email-ai-draft-modal__close")
    : null;
  var draftModalBackdrop = draftModal
    ? draftModal.querySelector(".email-ai-draft-modal__backdrop")
    : null;
  var draftModalTo = document.getElementById("draft-modal-to");
  var draftModalSubject = document.getElementById("draft-modal-subject");
  var chatSendBtn = chatForm ? chatForm.querySelector(".chat-send-btn") : null;

  if (chatForm && chatMessages && chatInput) {
    var scrollChatToBottom = function () {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    var clearStatus = function () {
      if (chatStatus) {
        chatStatus.innerHTML = "";
      }
    };

    var setStatus = function (kind, message) {
      if (!chatStatus) return;
      var alertClass =
        kind === "success" ? "email-ai-alert--ok" : "email-ai-alert--warn";
      chatStatus.innerHTML = "";
      var div = document.createElement("div");
      div.className = "email-ai-alert " + alertClass;
      div.textContent = message;
      chatStatus.appendChild(div);
    };

    var closeDraftModal = function () {
      if (!draftModal) return;
      draftModal.classList.remove("is-open");
      draftModal.setAttribute("aria-hidden", "true");
    };

    var openDraftModal = function (payload) {
      if (!draftModal || !draftText) return;
      var body = "";
      if (payload) {
        if (payload.draft_body != null && String(payload.draft_body).length) {
          body = String(payload.draft_body);
        } else if (
          payload.draft_text != null &&
          String(payload.draft_text).length
        ) {
          body = String(payload.draft_text);
        } else if (payload.assistant_turn) {
          body = String(payload.assistant_turn);
        }
      }
      if (draftModalTo) {
        draftModalTo.textContent = (payload && payload.draft_to) || "";
      }
      if (draftModalSubject) {
        draftModalSubject.textContent =
          (payload && payload.draft_subject) || "";
      }
      draftText.value = body;
      draftModal.classList.add("is-open");
      draftModal.setAttribute("aria-hidden", "false");
    };

    var appendTurn = function (role, content) {
      var emptyHint = chatMessages.querySelector(".email-ai-empty-hint");
      if (emptyHint) {
        emptyHint.remove();
      }

      var wrapper = document.createElement("div");
      wrapper.className = role === "user" ? "user-message" : "ai-message";
      var box = document.createElement("div");
      box.className = "message-box";
      box.textContent = content;
      wrapper.appendChild(box);
      chatMessages.appendChild(wrapper);
      scrollChatToBottom();
    };

    var showTypingIndicator = function () {
      var indicator = document.createElement("div");
      indicator.className = "typing-indicator";
      indicator.id = "typing-indicator";
      indicator.innerHTML =
        '<div class="typing-dot"></div>' +
        '<div class="typing-dot"></div>' +
        '<div class="typing-dot"></div>';
      chatMessages.appendChild(indicator);
      scrollChatToBottom();
    };

    var hideTypingIndicator = function () {
      var indicator = document.getElementById("typing-indicator");
      if (indicator) {
        indicator.remove();
      }
    };

    var resetChatUI = function () {
      chatMessages.innerHTML =
        '<p class="email-ai-empty-hint">' +
        "Ask about your inbox or say what you want to write — I'll help with Gmail-aware replies and drafts." +
        "</p>";
      closeDraftModal();
      if (draftText) {
        draftText.value = "";
      }
      clearStatus();
    };

    chatForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var message = chatInput.value.trim();
      if (!message) {
        setStatus("error", "Enter a message before sending it to Email AI.");
        return;
      }

      clearStatus();
      var formData = new FormData(chatForm);
      appendTurn("user", message);
      chatInput.value = "";
      chatInput.disabled = true;
      if (chatSendBtn) {
        chatSendBtn.disabled = true;
      }

      showTypingIndicator();

      fetch(chatForm.action, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
        credentials: "same-origin",
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          hideTypingIndicator();
          if (!result.ok) {
            if (chatMessages.lastElementChild) {
              chatMessages.removeChild(chatMessages.lastElementChild);
            }
            setStatus(
              "error",
              result.payload.error_message || "Unable to process your request.",
            );
            return;
          }

          if (result.payload.assistant_turn) {
            appendTurn("assistant", result.payload.assistant_turn);
          }

          if (result.payload.requires_action) {
            openDraftModal(result.payload);
          } else {
            closeDraftModal();
            if (draftText) {
              draftText.value = "";
            }
          }
        })
        .catch(function () {
          hideTypingIndicator();
          if (chatMessages.lastElementChild) {
            chatMessages.removeChild(chatMessages.lastElementChild);
          }
          setStatus(
            "error",
            "A network error occurred while contacting Email AI.",
          );
        })
        .finally(function () {
          chatInput.disabled = false;
          if (chatSendBtn) {
            chatSendBtn.disabled = false;
          }
          chatInput.focus();
        });
    });

    if (sendEmailForm && draftText) {
      sendEmailForm.addEventListener("submit", function (event) {
        event.preventDefault();
        clearStatus();

        var formData = new FormData(sendEmailForm);

        fetch(sendEmailForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
          body: formData,
          credentials: "same-origin",
        })
          .then(function (response) {
            return response.json().then(function (payload) {
              return { ok: response.ok, payload: payload };
            });
          })
          .then(function (result) {
            if (!result.ok) {
              setStatus(
                "error",
                result.payload.error_message || "Unable to send the email.",
              );
              return;
            }

            if (result.payload.success_message) {
              setStatus("success", result.payload.success_message);
            }

            closeDraftModal();
            draftText.value = "";
          })
          .catch(function () {
            setStatus(
              "error",
              "A network error occurred while sending the email.",
            );
          });
      });
    }

    if (draftModalClose) {
      draftModalClose.addEventListener("click", function () {
        closeDraftModal();
      });
    }

    if (draftModalBackdrop) {
      draftModalBackdrop.addEventListener("click", function () {
        closeDraftModal();
      });
    }

    document.addEventListener("keydown", function (ev) {
      if (
        ev.key === "Escape" &&
        draftModal &&
        draftModal.classList.contains("is-open")
      ) {
        closeDraftModal();
      }
    });

    var startNewChat = function () {
      if (!newChatForm) {
        window.location.reload();
        return;
      }

      var formData = new FormData(newChatForm);

      fetch(newChatForm.action, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
        credentials: "same-origin",
      })
        .then(function (response) {
          if (!response.ok) {
            setStatus("error", "Unable to start a new chat right now.");
            return;
          }
          resetChatUI();
          chatInput.focus();
        })
        .catch(function () {
          setStatus(
            "error",
            "A network error occurred while starting a new chat.",
          );
        });
    };

    if (newChatBtn) {
      newChatBtn.addEventListener("click", function () {
        startNewChat();
      });
    }

    scrollChatToBottom();
  }
});
