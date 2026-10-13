"""The review screen's single page: markup, styles and script in one string.

It shares the look of the run report: ledger paper, green rules, one red.
The page holds no data of its own; it asks the local server for the state
and draws it, and every piece of text is set as text, never as markup.
"""

FONT_STACK = 'Charter,"Bitstream Charter",Cambria,"Sitka Text","Liberation Serif","Times New Roman",serif'

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Possible collisions</title>
<style>
:root { --paper:#fcfdfb; --bar:#e2f0e0; --ink:#16241a; --soft:#3d4a40; --dropped:#5f6b62; --rule:#3f6b4a;
        --deep:#24492f; --red:#b3261e;
        --serif:__FONT__; --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
* { box-sizing:border-box; }
::selection { background:var(--rule); color:var(--paper); }
html { background:var(--paper); color:var(--ink); font:17px/1.45 var(--serif); scrollbar-color:var(--rule) var(--bar); }
body { margin:0; }
:focus-visible { outline:2px solid var(--deep); outline-offset:3px; }
button, input, select { font:inherit; color:inherit; }
button { cursor:pointer; }

header { position:sticky; top:0; z-index:2; background:var(--paper); border-bottom:2px solid var(--rule); }
.bar { max-width:1180px; margin:0 auto; padding:14px 40px; display:flex; gap:8px 28px; align-items:baseline; flex-wrap:wrap; }
h1 { font-size:24px; font-weight:700; margin:0; letter-spacing:-.005em; }
.state b { font-weight:700; }
.state.ran b { color:var(--red); }
.right { margin-left:auto; display:flex; gap:20px; align-items:baseline; font-variant-numeric:tabular-nums lining-nums; }
.plain { background:none; border:0; padding:0; text-decoration:underline; text-decoration-color:var(--rule);
         text-underline-offset:4px; text-decoration-thickness:1.5px; }
.plain:hover { color:var(--rule); }

nav { max-width:1180px; margin:0 auto; padding:22px 40px 0; display:flex; gap:28px; flex-wrap:wrap; }
nav button { background:none; border:0; padding:4px 0; border-bottom:3px solid transparent; font-size:18px; color:var(--soft); }
nav button[aria-pressed="true"] { color:var(--ink); font-weight:700; border-bottom-color:var(--rule); }
nav button:hover { color:var(--ink); }
nav span { font-variant-numeric:tabular-nums lining-nums; margin-left:6px; }

main { max-width:1180px; margin:0 auto; padding:18px 40px 80px; }
p { max-width:68ch; margin:0 0 12px; }
.lead { color:var(--soft); margin:6px 0 22px; }
#error { color:var(--red); font-weight:700; min-height:1.45em; margin:0; }

.heads, .row { display:grid; grid-template-columns:1fr 1fr 250px; gap:0 36px; }
.heads { font-size:15px; font-weight:700; padding:8px 18px; border-bottom:2px solid var(--rule); }
.row { padding:18px 18px 20px; border-bottom:1px solid var(--rule); align-items:start; scroll-margin-top:90px; }
.row:nth-of-type(odd) { background:var(--bar); }
.row.current { box-shadow:inset 5px 0 0 var(--deep); }
.spelling { font-size:21px; line-height:1.3; margin:0 0 3px; max-width:none; overflow-wrap:anywhere; }
.spelling .dropped { color:var(--dropped); }
.spelling .same { text-decoration:underline dotted var(--dropped); text-underline-offset:5px; }
.context { font-size:15px; color:var(--soft); margin:8px 0 0; font-variant-numeric:lining-nums; }
.reads { grid-column:1 / 3; font-size:15px; color:var(--soft); margin:14px 0 0; }
.reads code { font:14px var(--mono); color:var(--ink); }
.acts { grid-column:3; grid-row:1 / span 3; display:grid; gap:8px; }
.btn { padding:10px 14px; border:1.5px solid var(--deep); background:var(--paper); display:flex;
       justify-content:space-between; align-items:center; gap:12px; font-weight:700; text-align:left; }
.btn:hover { background:var(--bar); }
.row:nth-of-type(odd) .btn:not(.main):hover { background:#cfe4cd; }
.btn.main { background:var(--deep); color:var(--paper); }
.btn.main:hover { background:var(--ink); }
.btn[disabled] { opacity:.45; cursor:default; }
kbd { font:13px var(--mono); font-weight:400; opacity:.8; margin-left:6px; }
.split { grid-column:1 / 3; margin-top:14px; padding-top:12px; border-top:1px solid var(--rule); }
.split label { display:flex; gap:12px; align-items:center; padding:4px 0; font-size:18px; }
select { padding:3px 6px; border:1.5px solid var(--deep); background:var(--paper); font-size:16px; }
.split .btn { display:inline-flex; margin-top:10px; }

table { width:100%; border-collapse:collapse; }
td { padding:11px 18px; border-bottom:1px solid var(--rule); vertical-align:baseline; }
tbody tr:nth-child(odd) td { background:var(--bar); }
td.act { text-align:right; white-space:nowrap; width:1%; }
td small { color:var(--soft); font-size:15px; }

.pick { display:grid; grid-template-columns:1fr 1fr; gap:36px; }
.pick h2, .end h2 { font-size:18px; margin:0 0 8px; }
input[type=search] { width:100%; padding:8px 10px; border:1.5px solid var(--deep); background:var(--paper); caret-color:var(--red); }
.pick ul { list-style:none; margin:10px 0 0; padding:0; border-top:2px solid var(--rule); max-height:420px; overflow:auto; }
.pick li button { width:100%; display:flex; justify-content:space-between; gap:14px; padding:9px 12px; background:none;
                  border:0; border-bottom:1px solid var(--rule); text-align:left; }
.pick li:nth-child(odd) button { background:var(--bar); }
.pick li button:hover { box-shadow:inset 5px 0 0 var(--rule); }
.pick li button[aria-pressed="true"] { background:var(--deep); color:var(--paper); }
.pick small { font-size:15px; white-space:nowrap; font-variant-numeric:tabular-nums lining-nums; opacity:.85; }
.more { color:var(--soft); font-size:15px; margin-top:8px; }
.join { margin-top:22px; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }

.end { margin-top:56px; border-top:2px solid var(--rule); border-bottom:6px double var(--rule); padding:18px 0 20px; }
.end pre { font:16px var(--mono); margin:8px 0 0; padding:10px 14px; background:var(--ink); color:var(--paper); display:inline-block; }
.mock { color:var(--red); font-weight:700; }

@media (max-width:760px) {
  .bar, nav, main { padding-left:16px; padding-right:16px; }
  .heads { display:none; }
  .row, .pick { grid-template-columns:1fr; gap:14px; }
  .acts, .reads, .split { grid-column:1; grid-row:auto; }
  .right { margin-left:0; }
  .side { font-size:14px; font-weight:700; margin:0 0 4px; }
}
@media (min-width:761px) { .side { display:none; } }
</style>
</head>
<body>
<header><div class="bar">
  <h1>Possible collisions</h1>
  <div class="state" id="state"></div>
  <div class="right"><span id="count"></span><button class="plain" id="undo" hidden>Undo last <kbd>U</kbd></button></div>
</div></header>
<nav aria-label="Views" id="nav"></nav>
<main>
  <p id="error" role="status" aria-live="polite"></p>
  <div id="view"></div>
  <section class="end">
    <h2>When you have read them all</h2>
    <p>Your decisions are saved to <code id="file"></code> as you make them. This screen does not start phase two. You do, in the terminal:</p>
    <pre>leadlist phase2</pre>
    <p style="margin-top:14px" class="mock">Mock data: every person and organisation here was generated.</p>
  </section>
</main>
<script>
const TOKEN = "__TOKEN__";
const SHOWN = 60;
let S = null, view = "review", current = 0, splitFor = null, picked = {programme: null, crm: null},
    query = {programme: "", crm: ""};

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props || {})) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else if (value !== false && value != null) node.setAttribute(name, value);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}

async function load() {
  const response = await fetch("/api/state");
  const answer = await response.json();
  if (!response.ok) { document.getElementById("error").textContent = answer.error; return; }
  S = answer;
  draw();
}

async function send(action, body) {
  const response = await fetch("/api/" + action, {
    method: "POST", headers: {"Content-Type": "application/json", "X-Review-Token": TOKEN},
    body: JSON.stringify(body || {}),
  });
  const answer = await response.json();
  document.getElementById("error").textContent = response.ok ? "" : "Not saved: " + answer.error;
  if (response.ok) { S = answer; splitFor = null; draw(); }
}

function spelling(item) {
  const parts = item.parts.flatMap((part, i) => [i ? " " : null, el("span", {class: part[1]}, part[0])]);
  return el("p", {class: "spelling"}, parts);
}

function same(c) { send("same", {spellings: c.spellings, canonical: c.canonical}); }

function apart(c) {
  if (c.forms.length === 2 && !c.kept_apart.length) send("apart", {groups: c.forms});
  else { splitFor = c.key; draw(); }
}

function splitChooser(c) {
  const choices = c.forms.map((form, i) => {
    const select = el("select", {"aria-label": "Organisation for " + form[0]},
      c.forms.map((_, n) => el("option", {value: n, selected: n === i}, "Organisation " + (n + 1))));
    return {select, form, row: el("label", null, select, form[0])};
  });
  const confirm = () => {
    const groups = c.forms.map(() => []);
    for (const choice of choices) groups[+choice.select.value].push(...choice.form);
    send("apart", {groups: groups.filter(group => group.length)});
  };
  return el("div", {class: "split"},
    el("p", null, c.kept_apart.length
      ? "Some of these names are already kept apart. Give the new spelling the same number as the one it belongs to."
      : "There are " + c.forms.length + " spellings here. Give the ones that belong together the same number."),
    choices.map(choice => choice.row),
    el("button", {class: "btn main", onclick: confirm}, "Keep these apart"));
}

function reviewView() {
  const list = S.to_review;
  if (!list.length) return [el("p", {class: "lead"}, "Nothing is left to review. Every possible collision has a decision in the alias table.")];
  current = Math.min(current, list.length - 1);
  const rows = list.map((c, i) => el("section", {class: "row" + (i === current ? " current" : ""), "aria-label": c.key,
      onclick: () => { if (current !== i) { current = i; draw(false); } }},
    el("div", null, el("p", {class: "side"}, "In the programme"), c.programme.map(spelling),
      el("p", {class: "context"}, c.people + (c.people === 1 ? " person" : " people") + " at the event")),
    el("div", null, el("p", {class: "side"}, "In the CRM"), c.crm.map(spelling),
      el("p", {class: "context"}, c.crm.length ? c.context : "Not in the CRM. These spellings are from the programme."),
      c.kept_apart.length ? el("p", {class: "context"}, "Looks like names kept apart: " + c.kept_apart.join("; ")) : null),
    el("div", {class: "acts"},
      el("button", {class: "btn main", disabled: c.kept_apart.length > 0, onclick: () => same(c),
        title: c.kept_apart.length ? "This looks like names that are kept apart. Say which one it is." : null},
        "Same organisation", i === current && !c.kept_apart.length ? el("kbd", null, "S") : null),
      el("button", {class: "btn", disabled: c.forms.length < 2 && !c.kept_apart.length, onclick: () => apart(c),
        title: c.forms.length < 2 ? "These differ only in punctuation or case, so they cannot be told apart" : null},
        "Keep apart", i === current && c.forms.length > 1 ? el("kbd", null, "A") : null)),
    el("p", {class: "reads"}, "All of these read as ", el("code", null, c.key)),
    splitFor === c.key ? splitChooser(c) : null));
  return [
    el("p", {class: "lead"}, "These spellings land on the same name once case, punctuation and company forms are set aside. " +
      "The grey words are what was set aside. Say whether each group is one organisation."),
    el("div", {class: "heads"}, el("div", null, "In the programme"), el("div", null, "In the CRM"), el("div")),
    rows,
  ];
}

function decidedView() {
  if (!S.decided.length) return [el("p", {class: "lead"}, "The alias table is empty.")];
  return [
    el("p", {class: "lead"}, "Everything in the alias table, whether it was decided here or written by hand."),
    el("table", null, el("tbody", null, S.decided.map(d => el("tr", null,
      el("td", null, el("b", null, d.name), d.kind === "same"
        ? [" is the same as ", el("small", null, d.variants.join("; ") || "(no other spelling listed)")]
        : " is kept apart from names that look like it"),
      el("td", {class: "act"}, el("button", {class: "plain", onclick: () => send("forget", {kind: d.kind, name: d.name})}, "Take back")))))),
  ];
}

function pickList(side, title) {
  const all = S.unmatched[side];
  const hits = all.filter(item => item.name.toLowerCase().includes(query[side].toLowerCase()));
  const search = el("input", {type: "search", value: query[side], placeholder: "Type part of a name", "aria-label": "Search " + title,
    oninput: event => { query[side] = event.target.value; draw(false); focusSearch(side); }});
  search.dataset.side = side;
  return el("div", null, el("h2", null, title), search,
    el("ul", null, hits.slice(0, SHOWN).map(item => el("li", null,
      el("button", {"aria-pressed": picked[side] === item.name ? "true" : "false",
        onclick: () => { picked[side] = picked[side] === item.name ? null : item.name; draw(false); }},
        el("span", null, item.name), el("small", null, item.note))))),
    el("p", {class: "more"}, hits.length > SHOWN ? "Showing " + SHOWN + " of " + hits.length + ". Type to narrow it down."
      : hits.length + (hits.length === 1 ? " name" : " names")));
}

function focusSearch(side) {
  const input = document.querySelector('input[data-side="' + side + '"]');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}

function unmatchedView() {
  for (const side of ["programme", "crm"])
    if (!S.unmatched[side].some(item => item.name === picked[side])) picked[side] = null;
  const ready = picked.programme && picked.crm;
  return [
    el("p", {class: "lead"}, "Organisations at the event with no account, and accounts with nobody at the event. " +
      "If two of them are the same organisation, pick one from each side. The program suggests nothing here."),
    el("div", {class: "pick"}, pickList("programme", "In the programme"), pickList("crm", "In the CRM")),
    el("div", {class: "join"},
      el("button", {class: "btn main", disabled: !ready,
        onclick: () => send("link", {programme: picked.programme, crm: picked.crm})}, "Link as the same organisation"),
      el("span", null, ready ? ["“" + picked.programme + "” will be read as ", el("b", null, picked.crm), "."] : "Pick one name on each side.")),
  ];
}

function draw(scroll = true) {
  const views = {review: ["To review", S.to_review.length, reviewView], decided: ["Alias table", S.decided.length, decidedView],
                 unmatched: ["Matched nothing", S.unmatched.programme.length, unmatchedView]};
  document.getElementById("nav").replaceChildren(...Object.entries(views).map(([name, [label, count]]) =>
    el("button", {"aria-pressed": view === name ? "true" : "false", onclick: () => { view = name; draw(); }}, label, el("span", null, count))));
  const state = document.getElementById("state");
  state.className = "state" + (S.phase_two_ran ? " ran" : "");
  state.replaceChildren("Phase one is done. ", el("b", null, S.phase_two_ran
    ? "Phase two has run since. Run it again to use new decisions." : "Phase two has not started."));
  document.getElementById("count").textContent = S.to_review.length + " left to review";
  document.getElementById("undo").hidden = !S.can_undo;
  document.getElementById("file").textContent = S.alias_file;
  document.getElementById("view").replaceChildren(...views[view][2]().flat());
  const row = document.querySelector(".row.current");
  if (row && scroll) row.scrollIntoView({block: "nearest"});
}

document.getElementById("undo").addEventListener("click", () => send("undo"));
document.addEventListener("keydown", event => {
  if (event.target.matches?.("input, select, textarea") || event.metaKey || event.ctrlKey || event.altKey || !S) return;
  const key = event.key.toLowerCase();
  if (key === "u" && S.can_undo) return send("undo");
  if (view !== "review" || !S.to_review.length) return;
  const c = S.to_review[current];
  if (key === "j" || key === "arrowdown") { current = Math.min(current + 1, S.to_review.length - 1); splitFor = null; draw(); event.preventDefault(); }
  else if (key === "k" || key === "arrowup") { current = Math.max(current - 1, 0); splitFor = null; draw(); event.preventDefault(); }
  else if (key === "s" && !c.kept_apart.length) same(c);
  else if (key === "a" && (c.forms.length > 1 || c.kept_apart.length)) apart(c);
});
load();
</script>
</body>
</html>
""".replace("__FONT__", FONT_STACK)
