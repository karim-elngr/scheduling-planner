"use strict";

function plannerChoice({title, detail, selected = false, state = "clear"}, button) {
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.className = "candidate";
    for (const part of ["candidate-time", "candidate-state"]) {
      const span = document.createElement("span");
      span.className = part;
      button.append(span);
    }
  }
  button.querySelector(".candidate-time").textContent = title;
  const badge = button.querySelector(".candidate-state");
  badge.textContent = detail || "";
  badge.hidden = !detail;
  button.dataset.state = state;
  button.setAttribute("aria-pressed", String(selected));
  return button;
}

function downloadPlannerFile(filename, contents, mimeType) {
  const url = URL.createObjectURL(new Blob([contents], {type: mimeType}));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
