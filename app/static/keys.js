(function () {
  const help = document.getElementById("kb-help");
  if (!help) return;

  function items() {
    return Array.from(document.querySelectorAll(".kb-item"));
  }

  function focusedIndex() {
    const list = items();
    const active = document.activeElement;
    if (active && active.classList && active.classList.contains("kb-item")) {
      return list.indexOf(active);
    }
    const inside = active && active.closest ? active.closest(".kb-item") : null;
    return inside ? list.indexOf(inside) : -1;
  }

  function focusAt(index) {
    const list = items();
    if (!list.length) return;
    const next = list[(index + list.length) % list.length];
    next.setAttribute("tabindex", "0");
    next.focus();
    next.scrollIntoView({ block: "nearest" });
  }

  function inField(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented) return;
    const key = event.key;
    if (key === "?" || (key === "h" && event.shiftKey === false && event.altKey)) {
      if (!inField(event.target) || key === "?") {
        if (key === "?" && !inField(event.target)) {
          event.preventDefault();
          help.hidden = !help.hidden;
          return;
        }
      }
    }
    if (inField(event.target)) {
      if (key === "Escape") event.target.blur();
      return;
    }
    if (key === "Escape") {
      help.hidden = true;
      return;
    }
    if (key === "?" ) {
      event.preventDefault();
      help.hidden = !help.hidden;
      return;
    }
    if (key === "j") {
      event.preventDefault();
      focusAt(focusedIndex() + 1);
    } else if (key === "k") {
      event.preventDefault();
      const idx = focusedIndex();
      focusAt(idx <= 0 ? items().length - 1 : idx - 1);
    } else if (key === "Enter") {
      const card = document.activeElement && document.activeElement.closest
        ? document.activeElement.closest(".kb-item")
        : null;
      const btn = card && card.querySelector("form button[type=submit], form button:not(.danger)");
      if (btn && document.activeElement.tagName !== "A") {
        event.preventDefault();
        btn.click();
      }
    } else if (key === "n") {
      event.preventDefault();
      const cards = Array.from(document.querySelectorAll(".match-card"));
      if (!cards.length) return;
      const current = document.activeElement && document.activeElement.closest
        ? document.activeElement.closest(".match-card")
        : null;
      const i = current ? cards.indexOf(current) : -1;
      const next = cards[(i + 1) % cards.length];
      const target = next.querySelector(".kb-item") || next;
      target.setAttribute("tabindex", "0");
      target.focus();
      next.scrollIntoView({ block: "start" });
    } else if (key === "s") {
      const card = document.activeElement && document.activeElement.closest
        ? document.activeElement.closest(".match-card")
        : null;
      const searchBtn = card && card.querySelector("[data-open-search]");
      if (searchBtn) {
        event.preventDefault();
        searchBtn.click();
      }
    } else if (key === "/") {
      event.preventDefault();
      const input = document.querySelector(".search-form input[type=search], #year");
      if (input) input.focus();
    }
  });
})();
