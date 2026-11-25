/* Interactions for a standalone, saved solver decision. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId("calendar-data").textContent);
  const single = data.kind === "single";
  const options = [...data.options];
  if (single) options.sort((a, b) => a.placements[0].start.localeCompare(b.placements[0].start));
  const byOption = new Map(options.map(option => [option.id, option]));
  const zones = byId("zone");
  for (const zone of new Set([data.defaultTimezone, ...data.timezones, "UTC"])) {
    if (!zone) continue;
    try { new Intl.DateTimeFormat("en-GB", {timeZone: zone}); }
    catch { continue; }
    zones.add(new Option(zone.replaceAll("_", " "), zone));
  }
  let selected = byOption.get(data.primary) || options[0];
  let mode = selected ? "proposed" : "current";
  let day = null;
  const cards = new Map();
  const format = (value, fields) => new Intl.DateTimeFormat("en-GB", {
    ...fields, timeZone: zones.value
  }).format(new Date(value));
  const clock = value => format(value, {hour:"2-digit", minute:"2-digit", hourCycle:"h23"});
  const date = value => format(value, {day:"numeric", month:"short", year:"numeric"});
  const dayLabel = value => format(value, {weekday:"short", day:"numeric", month:"short"});
  const count = (n, noun) => n + " " + noun + (n === 1 ? "" : "s");
  const list = (items, target) => target.replaceChildren(...items.map(text => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }));
  function range(start, end) {
    return clock(start) + "–" + (date(start) === date(end) ? "" : dayLabel(end) + " ") + clock(end);
  }
  function multipleDates() {
    return single && new Set(options.map(o => date(o.placements[0].start))).size > 1;
  }
  function cardTitle(option) {
    if (!single) return option.label;
    const start = option.placements[0].start;
    return (multipleDates() ? dayLabel(start) + " · " : "") + clock(start);
  }
  function showCards() {
    const picks = byId("picks");
    picks.hidden = options.length < 2;
    if (picks.hidden) return;
    for (const option of options) {
      const conditional = option.availability === "conditional";
      const moves = option.moves.length;
      const detail = conditional ? "Needs confirmation" : moves ? count(moves, "move") : "No moves";
      const card = plannerChoice({
        title:cardTitle(option), detail, selected:option === selected,
        state:conditional ? "conditional" : moves ? "conflict" : "clear"
      }, cards.get(option.id));
      if (!cards.has(option.id)) {
        card.addEventListener("click", () => select(option));
        cards.set(option.id, card);
        picks.append(card);
      }
    }
  }
  function showHeading() {
    const baseline = byOption.get(data.baseline);
    const earlier = single && baseline && !multipleDates() &&
      options.some(o => o.placements[0].start < baseline.placements[0].start);
    const headline = earlier ? "Why wait until " + clock(baseline.placements[0].start) + "?" : data.headline;
    byId("headline").textContent = headline;
    document.title = headline + " · Scheduling Planner";
    byId("subtitle").textContent = data.subtitle;
  }
  function showGrid() {
    const changed = selected && (selected.placements.length || selected.moves.length);
    byId("view-mode").hidden = !changed;
    for (const button of byId("view-mode").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(mode === button.dataset.mode));
    }
    byId("scope-note").hidden = data.fullSnapshot;
    byId("filter-label").hidden = !(data.current.length && changed);
    const result = ReviewGrid.render(byId("calendar-grid"), {
      current:data.current, option:selected, options, zone:zones.value, mode, people:data.participants,
      day, changesOnly:byId("filter").checked, onDayChange:newDay => {day = newDay; showGrid();}
    });
    day = result.day;
    byId("date").textContent = result.dayLabel || "";
  }
  function changeRow(title, detail) {
    const li = document.createElement("li"), name = document.createElement("strong");
    const time = document.createElement("span");
    name.className = "change-title";
    name.textContent = title;
    time.className = "change-time";
    time.textContent = detail;
    li.append(name, time);
    return li;
  }
  function timeOnDay(time, anchor) {
    return date(time) === date(anchor) ? clock(time) : dayLabel(time) + ", " + clock(time);
  }
  function showDecision() {
    const status = byId("decision-state"), title = byId("insight-title"), note = byId("insight-note");
    const next = byId("compare-time");
    byId("choose").hidden = !selected;
    next.hidden = options.length < 2;
    byId("reasoning").hidden = !selected;
    if (!selected) {
      status.dataset.state = "blocked";
      status.textContent = data.status;
      title.textContent = "No proposed time";
      note.textContent = "The saved result contains no meeting placements to share.";
      return;
    }
    const conditional = selected.availability === "conditional";
    status.textContent = conditional ? "Needs confirmation" :
      single ? "Verified calendar fit" : selected.proofStatus === "optimal" ? "Optimal schedule" : "Feasible schedule";
    status.dataset.state = conditional ? "conditional" : "clear";
    const slots = [...selected.placements].sort((a, b) => a.start.localeCompare(b.start));
    const moves = selected.moves, first = slots[0], last = slots[slots.length - 1];
    const contiguous = slots.length && slots.every((p, i) => i === 0 || slots[i-1].end === p.start);
    title.textContent = contiguous ? range(first.start, last.end) :
      slots.length ? count(slots.length, "meeting") : moves.length ? "Make room for focus" : "Keep the current schedule";
    const names = [...new Set(slots.flatMap(p => p.attendees))];
    if (contiguous) {
      note.textContent = dayLabel(first.start) + (single ? (names.length ? " · " + count(names.length, "attendee") : "") :
        " · " + count(slots.length, "meeting") + (slots.length > 1 ? " back to back" : ""));
    } else {
      note.textContent = names.length ? count(names.length, "attendee") + " across the schedule" : "";
    }
    const metric = byId("metric");
    metric.textContent = moves.length ? count(moves.length, "meeting") + " moved" : "No existing meetings moved";
    const impact = selected.impact;
    if (impact) {
      const extra = document.createElement("span");
      extra.className = "total-impact";
      extra.textContent = impact;
      metric.append(extra);
    }
    const rows = [];
    const anchor = first?.start || moves[0]?.after.start;
    if (!single) {
      for (const event of slots) {
        rows.push(changeRow(event.title, timeOnDay(event.start, anchor) + " · " + event.attendees.join(", ")));
      }
    }
    for (const move of moves) {
      const who = [...new Set([...move.after.attendees, move.after.organizer].filter(Boolean))];
      rows.push(changeRow(move.after.title, timeOnDay(move.before.start, anchor) + " → " +
        timeOnDay(move.after.start, anchor) + (who.length ? " · " + who.join(", ") : "")));
    }
    byId("changes").replaceChildren(...rows);
    const question = byId("question");
    question.hidden = !conditional;
    question.textContent = conditional ? selected.notes[0] || "Confirm availability before sharing this time." : "";
    byId("evidence").textContent = selected.evidence;
    list([...selected.facts.filter(f => f !== impact), ...selected.notes.slice(conditional ? 1 : 0)], byId("facts"));
    if (!next.hidden) {
      const alternative = nextChoice();
      next.textContent = (single ? "Explore " : "Compare with ") + cardTitle(alternative);
    }
    byId("proposal").textContent = selected.proposal;
  }
  function nextChoice() {
    const i = options.indexOf(selected);
    return single && i > 0 ? options[i - 1] : options[(i + 1) % options.length];
  }
  function showComparison() {
    byId("comparison").hidden = !data.comparison.length;
    byId("headers").replaceChildren(...data.headers.map((header, i) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = single && i === 2 ? "Earliest start (" + zones.value + ")" : header;
      return th;
    }));
    byId("caps").replaceChildren(...data.comparison.map(row => {
      const tr = document.createElement("tr");
      row.cells.forEach((value, i) => {
        const cell = document.createElement(i ? "td" : "th");
        if (!i) cell.scope = "row";
        if (!i && byOption.has(row.option)) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = value;
          button.setAttribute("aria-label", "Show schedule for a budget of " + count(value, "move"));
          button.addEventListener("click", () => {
            select(byOption.get(row.option));
            (cards.get(row.option) || byId("choose")).focus();
          });
          cell.append(button);
        } else {
          cell.textContent = single && i === 2 && value ? dayLabel(value) + " " + clock(value) : value ?? "—";
        }
        tr.append(cell);
      });
      return tr;
    }));
  }
  function select(option) {
    selected = option;
    mode = "proposed";
    day = null;
    byId("handoff").hidden = true;
    showCards();
    showDecision();
    showGrid();
  }
  function refresh() {
    showHeading();
    showCards();
    showDecision();
    showGrid();
    showComparison();
  }
  for (const button of byId("view-mode").querySelectorAll("button")) {
    button.addEventListener("click", () => {mode = button.dataset.mode; showGrid();});
  }
  byId("compare-time").addEventListener("click", () => select(nextChoice()));
  byId("choose").addEventListener("click", () => {
    byId("handoff").hidden = false;
    byId("handoff-title").focus();
  });
  byId("close").addEventListener("click", () => {
    byId("handoff").hidden = true;
    byId("choose").focus();
  });
  byId("proposal-download").addEventListener("click", () =>
    downloadPlannerFile("meeting-proposal.txt", selected.proposal, "text/plain;charset=utf-8"));
  byId("filter").addEventListener("change", showGrid);
  zones.addEventListener("change", () => {day = null; refresh();});
  list(data.explainers, byId("explainers"));
  byId("profiles").hidden = !data.explainers.length;
  refresh();
})();
