/* Offline calendar for snapshot-backed and changes-only reports. Positions refer
   to elapsed time, so repeated local hours stay distinct during a DST change. */
const ReviewGrid = (() => {
  const minute = 60000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const unassigned = Symbol("meeting without an attendee roster");
  const labels = {
    existing: "Scheduled", added: "Proposed meeting", conditional: "Conditional meeting",
    moved: "Proposed new time", original: "Original time"
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function formatters(zone) {
    const formatter = options => new Intl.DateTimeFormat("en-GB", {timeZone: zone, ...options});
    const parts = formatter({year: "numeric", month: "2-digit", day: "2-digit", era: "short"});
    const clock = {hour: "2-digit", minute: "2-digit", hourCycle: "h23"};
    return {
      key(instant) {
        const values = {};
        for (const part of parts.formatToParts(instant)) values[part.type] = part.value;
        // Timezone conversion may move the first/last supported UTC calendar
        // year across an era boundary. Date.parse accepts ISO extended years.
        const year = values.era === "BC" ? 1 - Number(values.year) : Number(values.year);
        const isoYear = year >= 0 && year <= 9999 ? String(year).padStart(4, "0") :
          (year < 0 ? "-" : "+") + String(Math.abs(year)).padStart(6, "0");
        return [isoYear, values.month, values.day].join("-");
      },
      date: formatter({weekday: "short", month: "short", day: "numeric", year: "numeric"}),
      offset: formatter({timeZoneName: "shortOffset"}),
      clock: formatter(clock),
      zonedClock: formatter({...clock, timeZoneName: "shortOffset"}),
      full: formatter({...clock, month: "short", day: "numeric", year: "numeric", timeZoneName: "shortOffset"})
    };
  }

  function startOfDay(key, fmt) {
    // Resolve local midnight through the actual zone history, including days
    // whose first valid wall-clock time is later than 00:00.
    const utc = Date.parse(key + "T00:00Z");
    let low = utc - 2 * day;
    let high = utc + 2 * day;
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2);
      if (Date.parse(fmt.key(middle) + "T00:00Z") < utc) low = middle;
      else high = middle;
    }
    return high;
  }

  function nextDay(key) {
    return new Date(Date.parse(key + "T00:00Z") + day).toISOString().split("T")[0];
  }

  function entries(current, option, changesOnly) {
    const moves = option?.moves || [];
    const moving = new Set(moves.map(move => move.before.id));
    const fixed = changesOnly ? [] : current.filter(item => !moving.has(item.id));
    const base = fixed.map(event => ({event, kind: "existing"}));
    const original = moves.map(move => ({event: move.before, kind: "original"}));
    const target = moves.map(move => ({event: move.after, kind: "moved"}));
    const newKind = option?.availability === "conditional" ? "conditional" : "added";
    const added = (option?.placements || []).map(event => ({event, kind: newKind}));
    return {before: [...base, ...original], after: [...base, ...target, ...added], ghosts: original};
  }

  function dayRangeIndex(entries, fmt) {
    const ranges = entries.map(({event}) => [
      Date.parse(fmt.key(Date.parse(event.start)) + "T00:00Z"),
      Date.parse(fmt.key(Date.parse(event.end) - 1) + "T00:00Z")
    ]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [first, last] of ranges) {
      const previous = merged.at(-1);
      if (previous && first <= previous[1] + day) previous[1] = Math.max(previous[1], last);
      else merged.push([first, last]);
    }
    const key = value => new Date(value).toISOString().split("T")[0];
    const utc = value => Date.parse(value + "T00:00Z");
    const count = merged.reduce((sum, [first, last]) => sum + (last - first) / day + 1, 0);
    const dates = [];
    // A long-running commitment may span years. A native date picker avoids
    // allocating a menu option for every day in that case.
    for (const [first, last] of merged) {
      if (count <= 90) {
        for (let t = first; t <= last; t += day) dates.push(key(t));
      } else {
        dates.push(key(first));
        if (last !== first) dates.push(key(last));
      }
    }
    return {dates, count,
      includes(date) {
        const value = utc(date);
        return merged.some(([first, last]) => first <= value && value <= last);
      },
      neighbor(date, direction) {
        const target = utc(date) + direction * day;
        const range = direction === 1 ? merged.find(([, last]) => target <= last)
          : [...merged].reverse().find(([first]) => target >= first);
        return range && key(Math.max(range[0], Math.min(target, range[1])));
      }
    };
  }

  function clip(items, left, right) {
    const intervals = [];
    for (const {event, kind} of items) {
      const start = Math.max(left, Date.parse(event.start));
      const end = Math.min(right, Date.parse(event.end));
      if (end > start) intervals.push({event, kind, start, end});
    }
    return intervals;
  }

  function byPerson(entries, people) {
    const columns = new Map(people.map(person => [person, []]));
    for (const entry of entries) {
      const attendees = entry.event.attendees.length ? entry.event.attendees : [unassigned];
      for (const person of attendees) {
        if (!columns.has(person)) columns.set(person, []);
        columns.get(person).push(entry);
      }
    }
    return columns;
  }

  function lanes(intervals) {
    // Divide only the connected group of overlapping intervals. Sequential
    // appointments reclaim the full width of their attendee column.
    const sorted = [...intervals].sort((a,b) => a.start - b.start || b.end - a.end);
    const result = [];
    let activeUntil = [], group = [], groupEnd = -Infinity;
    function finish() {
      for (const item of group) result.push({...item, count: activeUntil.length});
      group = [];
      activeUntil = [];
    }
    for (const item of sorted) {
      if (item.start >= groupEnd) finish();
      let lane = activeUntil.findIndex(end => end <= item.start);
      if (lane < 0) lane = activeUntil.length;
      activeUntil[lane] = item.end;
      group.push({...item, lane});
      groupEnd = Math.max(groupEnd, item.end);
    }
    finish();
    return result;
  }

  function visibleRange(items, start, end) {
    let earliest = end, latest = start;
    for (const item of items) {
      earliest = Math.min(earliest, item.start);
      latest = Math.max(latest, item.end);
    }
    let left = start + Math.floor((earliest - start) / hour) * hour;
    let right = Math.min(end, start + Math.ceil((latest - start) / hour) * hour);
    if (right - left < 4 * hour) {
      right = Math.min(end, left + 4 * hour);
      left = Math.max(start, right - 4 * hour);
    }
    return [left, right];
  }

  function card(item, range, height, fmt, showDetails) {
    const {event, kind, start, end, lane, count} = item;
    const button = node("button", "rg-event rg-" + kind);
    const title = event.title || event.id;
    const when = fmt.full.format(Date.parse(event.start)) + " – " + fmt.full.format(Date.parse(event.end));
    const description = [title, labels[kind], when, event.attendees.join(", "),
      event.organizer && "Organizer: " + event.organizer].filter(Boolean);
    button.type = "button";
    button.title = description.join(" · ");
    button.setAttribute("aria-label", button.title);
    const total = range[1] - range[0];
    button.style.top = (100 * (start - range[0]) / total) + "%";
    button.style.height = (100 * (end - start) / total) + "%";
    button.style.left = "calc(" + (100 * lane / count) + "% + 2px)";
    button.style.width = "calc(" + (100 / count) + "% - 4px)";
    const generic = event.id === "Requested meeting" && title === event.id;
    button.append(node("span", "rg-card-title", generic ? "Meeting" : title));
    if (height * (end - start) / total >= 36) {
      button.append(node("span", "rg-card-time",
        fmt.clock.format(Date.parse(event.start)) + "–" + fmt.clock.format(Date.parse(event.end))));
    }
    button.addEventListener("click", () => showDetails(button, description));
    return button;
  }

  function drawPanel(records, people, requestedCount, range, fmt, label, dayLabel, zone, showDetails) {
    const panel = node("div", "rg-panel");
    if (label) panel.append(node("h4", "rg-panel-title", label));
    const scroll = node("div", "rg-scroll");
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", (label ? label + ", " : "") + dayLabel + " calendar");
    scroll.tabIndex = 0;
    const grid = node("div", "rg-timesheet");
    const total = range[1] - range[0];
    const height = Math.min(600, Math.max(240, (total / hour) * 50));
    grid.style.setProperty("--rg-count", people.length);
    grid.style.setProperty("--rg-height", height + "px");
    const showGroups = requestedCount > 0 && requestedCount < people.length;
    if (showGroups) {
      const requested = node("div", "rg-group", "Requested attendees");
      requested.style.gridColumn = "2 / span " + requestedCount;
      const other = node("div", "rg-group rg-other", "Other calendars");
      other.style.gridColumn = "span " + (people.length - requestedCount);
      grid.append(requested, other);
    }
    const axis = node("div", "rg-axis");
    const clockHead = node("div", "rg-column-head rg-clock-label", zone.length <= 5 ? zone : "");
    axis.append(clockHead);
    const times = node("div", "rg-track");
    const marks = [];
    const tickStep = total / height > hour / 32 ? 2 * hour : hour;
    for (let t = range[0]; t < range[1]; t += tickStep) marks.push(t);
    const offsetAt = instant => fmt.offset.formatToParts(instant).find(part => part.type === "timeZoneName").value;
    const ambiguous = offsetAt(range[0]) !== offsetAt(range[1] - 1);
    const clock = ambiguous ? fmt.zonedClock : fmt.clock;
    if (ambiguous) grid.classList.add("rg-offsets");
    for (const t of marks) {
      const tick = node("span", "rg-tick", clock.format(t));
      tick.style.top = (100 * (t - range[0]) / total) + "%";
      times.append(tick);
    }
    grid.style.setProperty("--rg-step", (100 * tickStep / total) + "%");
    axis.append(times);
    grid.append(axis);
    const grouped = byPerson(records, people);
    for (const [index, person] of people.entries()) {
      const column = node("div", "rg-column");
      const name = person === unassigned ? "Meeting" : person;
      column.dataset.person = name;
      if (showGroups && index >= requestedCount) column.classList.add("rg-other");
      column.append(node("div", "rg-column-head", name));
      const track = node("div", "rg-track");
      for (const item of lanes(grouped.get(person))) track.append(card(item, range, height, fmt, showDetails));
      column.append(track);
      grid.append(column);
    }
    scroll.append(grid);
    panel.append(scroll);
    return panel;
  }

  function render(container, settings) {
    const {current = [], option, zone = "UTC", mode = "proposed", changesOnly = false, people = []} = settings;
    const fmt = formatters(zone);
    const series = entries(current, option, changesOnly);
    const all = [...series.before, ...series.after];
    const dayIndex = dayRangeIndex(all, fmt);
    const dates = dayIndex.dates;
    container.replaceChildren();
    if (!dates.length) {
      container.append(node("p", "rg-empty", changesOnly ? "No changes to display." : "No reported events."));
      return {day: null, dayLabel: "", dates};
    }
    const preferred = option?.placements?.[0] || option?.moves?.[0]?.after;
    const preferredDay = preferred && fmt.key(Date.parse(preferred.start));
    const selected = dayIndex.includes(settings.day) ? settings.day : dayIndex.includes(preferredDay) ? preferredDay : dates[0];
    const left = startOfDay(selected, fmt);
    const right = startOfDay(nextDay(selected), fmt);
    const dayLabel = fmt.date.format(left);
    const header = node("div", "rg-heading");
    const selectDay = key => {
      if (settings.onDayChange) settings.onDayChange(key);
      else render(container, {...settings, day: key});
      container.querySelector(".rg-date")?.focus({preventScroll: true});
    };
    if (dayIndex.count > 1) {
      const compact = dayIndex.count <= 90;
      const datePicker = node(compact ? "select" : "input", "rg-date");
      datePicker.setAttribute("aria-label", "Calendar day");
      if (compact) {
        dates.forEach(key => datePicker.add(new Option(fmt.date.format(startOfDay(key, fmt)), key)));
      } else {
        // A timezone can put the earliest or latest supported instant outside
        // the range of the native date input. ISO text still permits navigation.
        const nativeRange = dates[0] >= "0001-01-01" && /^\d{4}-/.test(dates.at(-1));
        datePicker.type = nativeRange ? "date" : "text";
        datePicker.size = 13;
        datePicker.min = dates[0];
        datePicker.max = dates.at(-1);
      }
      datePicker.value = selected;
      datePicker.addEventListener("change", () => {
        if (dayIndex.includes(datePicker.value)) selectDay(datePicker.value);
        else datePicker.value = selected;
      });
      header.append(datePicker);
      for (const [offset, arrow, title] of [[-1, "‹", "Previous day with events"], [1, "›", "Next day with events"]]) {
        const button = node("button", "rg-nav", arrow);
        button.type = "button";
        button.setAttribute("aria-label", title);
        const destination = dayIndex.neighbor(selected, offset);
        button.disabled = !destination;
        button.addEventListener("click", () => selectDay(destination));
        header.append(button);
      }
      container.append(header);
    }
    const before = clip(series.before, left, right);
    const after = clip(series.after, left, right);
    // Keep the ruler still as the user compares options. Only records on this
    // local day affect its extent, including when "Changes only" is selected.
    const candidates = (settings.options || (option ? [option] : [])).flatMap(plan =>
      [...plan.placements, ...plan.moves.flatMap(move => [move.before, move.after])]);
    const extent = clip([...current, ...candidates].map(event => ({event})), left, right);
    const range = visibleRange(extent, left, right);
    const columns = [...byPerson([...before, ...after], people).keys()];
    const detail = node("div", "rg-detail");
    detail.hidden = true;
    function showDetails(trigger, description) {
      const close = node("button", "rg-close", "Close");
      close.type = "button";
      close.addEventListener("click", () => { detail.hidden = true; trigger.focus(); });
      detail.replaceChildren(node("strong", "", description[0]), node("span", "", description.slice(1).join(" · ")), close);
      detail.hidden = false;
      close.focus({preventScroll: true});
      detail.scrollIntoView({block: "nearest"});
    }
    const panels = node("div", mode === "compare" ? "rg-panels rg-compare" : "rg-panels");
    if (mode === "current" || mode === "compare") {
      panels.append(drawPanel(before, columns, people.length, range, fmt, mode === "compare" ? "Current" : "", dayLabel, zone, showDetails));
    }
    if (mode !== "current") {
      const ghosts = mode === "compare" ? [] : clip(series.ghosts, left, right);
      panels.append(drawPanel([...after, ...ghosts], columns, people.length, range, fmt, mode === "compare" ? "Proposed" : "", dayLabel, zone, showDetails));
    }
    container.append(panels, detail);
    const shown = mode === "current" ? before : mode === "compare" ? [...before,...after] : [...after,...clip(series.ghosts,left,right)];
    const kinds = new Set(shown.map(item => item.kind));
    const key = node("div", "rg-key");
    key.setAttribute("aria-label", "Calendar legend");
    for (const kind of Object.keys(labels)) {
      if (kinds.has(kind)) key.append(node("span", "rg-key-item rg-" + kind, labels[kind]));
    }
    container.append(key);
    return {day: selected, dayLabel, dates};
  }

  return {render};
})();
