import { EditorView, basicSetup } from "https://esm.sh/codemirror@6.0.2?deps=@codemirror/state@6.5.2";
import { Decoration } from "https://esm.sh/@codemirror/view@6.35.0?deps=@codemirror/state@6.5.2";
import { EditorState, StateEffect, StateField } from "https://esm.sh/@codemirror/state@6.5.2";
import { python } from "https://esm.sh/@codemirror/lang-python@6.1.7?deps=@codemirror/state@6.5.2";
import { createTwoFilesPatch } from "https://esm.sh/diff@5.2.0";

const setErrorMark = StateEffect.define();
const clearErrorMark = StateEffect.define();

const errorLineDeco = Decoration.line({ attributes: { style: "background: #fee;" } });
const errorPointDeco = Decoration.mark({ attributes: { style: "text-decoration: underline wavy #c33; text-underline-offset: 3px;" } });

const errorMarkField = StateField.define({
  create() { return Decoration.none; },
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(clearErrorMark)) deco = Decoration.none;
      if (e.is(setErrorMark)) {
        const { line, col } = e.value;
        const doc = tr.state.doc;
        if (line >= 1 && line <= doc.lines) {
          const l = doc.line(line);
          const from = Math.min(l.to, l.from + Math.max(0, (col || 1) - 1));
          const to = Math.min(l.to, from + 1);
          const marks = [errorLineDeco.range(l.from)];
          if (to > from) marks.push(errorPointDeco.range(from, to));
          deco = Decoration.set(marks);
        }
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const PYODIDE_VERSION = "v0.29.4";
const RETROFY_INDEX = "https://pelson.github.io/retrofy/simple/";
const SOURCES = {
  dev: {
    label: "Dev (main)",
    index_urls: [RETROFY_INDEX, "https://pypi.org/simple/"],
    pre: true,
  },
  released: {
    label: "Released (PyPI)",
    index_urls: ["https://pypi.org/simple/"],
    pre: false,
  },
};

const statusEl = document.getElementById("status");

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = cls;
}

async function installRetrofy(pyodide, source, { force = false } = {}) {
  const { label, index_urls, pre } = SOURCES[source];
  setStatus(`Installing retrofy — ${label}…`);
  const micropip = pyodide.pyimport("micropip");
  if (force) {
    pyodide.runPython(
      "import sys\n" +
      "for _m in [m for m in sys.modules if m == 'retrofy' or m.startswith('retrofy.')]:\n" +
      "    del sys.modules[_m]\n",
    );
  }
  await micropip.install(
    "retrofy",
    { index_urls, reinstall: force, pre },
  );
  pyodide.runPython("from retrofy._converters import convert");
  setStatus("Ready.", "ready");
}

async function bootPyodide() {
  setStatus("Booting Pyodide…");
  const { loadPyodide } = await import(
    `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/pyodide.mjs`
  );
  const pyodide = await loadPyodide({
    indexURL: `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`,
  });
  await pyodide.loadPackage("micropip");
  await installRetrofy(pyodide, currentSource);
  return pyodide;
}

let currentSource = "released";

export const pyodideReady = bootPyodide().catch((err) => {
  setStatus(`Boot failed: ${err.message}`, "error");
  throw err;
});

const SEED = `from typing import Union

def f(x: int | str) -> list[int]:
    match x:
        case int():
            return [x]
        case _:
            return []
`;

function makeEditor(parent, doc, readOnly) {
  const extensions = [basicSetup, python()];
  if (!readOnly) extensions.push(errorMarkField);
  if (readOnly) extensions.push(EditorState.readOnly.of(true));
  extensions.push(
    EditorView.updateListener.of((v) => {
      if (!readOnly && v.docChanged) {
        document.dispatchEvent(
          new CustomEvent("retrofy:input-change", {
            detail: v.state.doc.toString(),
          }),
        );
      }
    }),
  );
  return new EditorView({
    doc,
    parent,
    extensions,
  });
}

export const inputView = makeEditor(
  document.getElementById("input"),
  SEED,
  false,
);
export const outputView = makeEditor(
  document.getElementById("output"),
  "",
  true,
);

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function setOutput(text) {
  outputView.dispatch({
    changes: { from: 0, to: outputView.state.doc.length, insert: text },
  });
}

const CONVERT_SCRIPT = `
def _retrofy_playground_convert(src):
    from retrofy._converters import convert
    try:
        return ("ok", convert(src), None, None, None)
    except SyntaxError as exc:
        return ("err", None, exc.lineno, exc.offset, str(exc))
    except Exception as exc:
        return ("err", None, None, None, f"{type(exc).__name__}: {exc}")
`;

async function convert(source) {
  const pyodide = await pyodideReady;
  pyodide.runPython(CONVERT_SCRIPT);
  const fn = pyodide.globals.get("_retrofy_playground_convert");
  try {
    const result = fn(source);
    const arr = result.toJs();
    result.destroy();
    return arr;
  } finally {
    fn.destroy();
  }
}

const errorsEl = document.getElementById("input-errors");
const notesEl = document.getElementById("output-notes");
const overlayEl = document.getElementById("output-overlay");
const outputWrapEl = overlayEl.parentElement;

const diffElStale = document.getElementById("diff");
function setOverlay(kind, message) {
  if (!kind) {
    overlayEl.hidden = true;
    overlayEl.className = "";
    overlayEl.innerHTML = "";
    outputWrapEl.classList.remove("stale");
    diffElStale.classList.remove("stale");
    return;
  }
  overlayEl.hidden = false;
  overlayEl.className = kind;
  overlayEl.innerHTML = "";
  const banner = document.createElement("div");
  banner.className = "banner";
  banner.textContent = message;
  overlayEl.appendChild(banner);
  outputWrapEl.classList.add("stale");
  diffElStale.classList.add("stale");
}

function showErrors(text) {
  if (text) {
    errorsEl.textContent = text;
    errorsEl.hidden = false;
  } else {
    errorsEl.textContent = "";
    errorsEl.hidden = true;
  }
}

function showNotes(lines) {
  if (lines.length) {
    notesEl.innerHTML = "";
    for (const line of lines) {
      const div = document.createElement("div");
      div.textContent = `• ${line}`;
      notesEl.appendChild(div);
    }
    notesEl.hidden = false;
  } else {
    notesEl.innerHTML = "";
    notesEl.hidden = true;
  }
}

function derivedNotes(input, output) {
  const notes = [];
  const needs = (mod) =>
    new RegExp(`(?:^|\\n)\\s*(?:from\\s+${mod}\\s+import|import\\s+${mod})`).test(output) &&
    !new RegExp(`(?:^|\\n)\\s*(?:from\\s+${mod}\\s+import|import\\s+${mod})`).test(input);
  if (needs("typing_extensions"))
    notes.push("Adds a runtime dependency: typing_extensions (declare it in pyproject.toml).");
  if (needs("typing")) notes.push("Uses typing (available on the standard library).");
  if (/from\s+retrofy\._retrofy_rt|import\s+_retrofy_rt/.test(output))
    notes.push("Requires the retrofy runtime bundle (retrofy/_retrofy_rt) alongside the package.");
  if (/from\s+__future__\s+import\s+annotations/.test(output) &&
      !/from\s+__future__\s+import\s+annotations/.test(input))
    notes.push("Adds `from __future__ import annotations`.");
  return notes;
}

const runConvert = debounce(async (source) => {
  setOverlay("loading", "Converting…");
  try {
    const arr = await convert(source);
    if (arr[0] === "ok") {
      setOutput(arr[1]);
      setStatus("Ready.", "ready");
      showErrors("");
      inputView.dispatch({ effects: clearErrorMark.of(null) });
      showNotes(derivedNotes(source, arr[1]));
      setOverlay("", "");
      document.dispatchEvent(
        new CustomEvent("retrofy:converted", {
          detail: { input: source, output: arr[1] },
        }),
      );
    } else {
      const [, , line, col, msg] = arr;
      const where = line ? `line ${line}${col ? `, col ${col}` : ""}: ` : "";
      showErrors(`SyntaxError: ${where}${msg}`);
      setStatus("SyntaxError — see error box.", "error");
      setOverlay("error", `SyntaxError${line ? ` at ${where.trim().replace(/:$/, "")}` : ""} — output is out of date.`);
      if (line) {
        inputView.dispatch({ effects: setErrorMark.of({ line, col }) });
      } else {
        inputView.dispatch({ effects: clearErrorMark.of(null) });
      }
    }
  } catch (err) {
    showErrors(`Error: ${err.message}`);
    setStatus("Conversion failed — see error box.", "error");
    setOverlay("error", "Conversion failed — output is out of date.");
    inputView.dispatch({ effects: clearErrorMark.of(null) });
  }
}, 300);

document.addEventListener("retrofy:input-change", (e) => {
  setOverlay("loading", "Converting…");
  runConvert(e.detail);
});
setOverlay("loading", "Booting Pyodide + installing retrofy…");
pyodideReady.then(() => runConvert(inputView.state.doc.toString()));

const diffEl = document.getElementById("diff");

function renderDiff(input, output) {
  const patch = createTwoFilesPatch(
    "input.py",
    "output.py",
    input,
    output,
    "",
    "",
    { context: 3 },
  );
  diffEl.innerHTML = "";
  for (const raw of patch.split("\n")) {
    const line = document.createElement("div");
    line.textContent = raw;
    if (raw.startsWith("+") && !raw.startsWith("+++")) line.className = "add";
    else if (raw.startsWith("-") && !raw.startsWith("---")) line.className = "del";
    diffEl.appendChild(line);
  }
}

document.addEventListener("retrofy:converted", (e) => {
  renderDiff(e.detail.input, e.detail.output);
});

function makeHorizontalResizer(handle, leftEl) {
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startWidth = leftEl.getBoundingClientRect().width;
    const move = (ev) => {
      const total = leftEl.parentElement.getBoundingClientRect().width;
      const w = Math.min(total - 60, Math.max(60, startWidth + (ev.clientX - startX)));
      leftEl.style.flex = `0 0 ${w}px`;
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.releasePointerCapture(e.pointerId);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
}

function makeVerticalResizer(handle, belowEl) {
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const startY = e.clientY;
    const startHeight = belowEl.getBoundingClientRect().height;
    const move = (ev) => {
      const h = Math.max(40, startHeight - (ev.clientY - startY));
      belowEl.style.height = `${h}px`;
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.releasePointerCapture(e.pointerId);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
}

makeHorizontalResizer(
  document.getElementById("resizer-cols"),
  document.getElementById("input-col"),
);
makeVerticalResizer(
  document.getElementById("resizer-diff"),
  document.getElementById("diff"),
);

const EXAMPLES = [
  {
    label: "match — basic",
    code: `def label(x):
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"
`,
  },
  {
    label: "match — class patterns",
    code: `from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

def where(p):
    match p:
        case Point(x=0, y=0):
            return "origin"
        case Point(x=0, y=y):
            return f"y-axis at {y}"
        case Point(x=x, y=0):
            return f"x-axis at {x}"
        case _:
            return "elsewhere"
`,
  },
  {
    label: "walrus — basic",
    code: `def first_long(items):
    if (n := len(items)) > 5:
        return f"got {n} items"
    return None
`,
  },
  {
    label: "walrus — while loop",
    code: `def read_all(stream):
    chunks = []
    while chunk := stream.read(1024):
        chunks.append(chunk)
    return b"".join(chunks)
`,
  },
  {
    label: "lazy import — module-level",
    code: `lazy from json import loads

def parse(data):
    return loads(data)
`,
  },
  {
    label: "lazy import — function-scoped",
    code: `def render(fig):
    lazy import matplotlib.pyplot as plt
    plt.plot(fig)
`,
  },
  {
    label: "PEP 604 union types",
    code: `def clamp(x: int | float, lo: int | float, hi: int | float) -> int | float:
    return max(lo, min(hi, x))
`,
  },
  {
    label: "PEP 585 generics",
    code: `def group_by(items: list[dict[str, int]]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for item in items:
        for k, v in item.items():
            out.setdefault(k, []).append(v)
    return out
`,
  },
  {
    label: "type alias (PEP 695)",
    code: `type Vector = list[float]
type Matrix = list[Vector]

def zeros(n: int) -> Matrix:
    return [[0.0] * n for _ in range(n)]
`,
  },
  {
    label: "typing.final",
    code: `from typing import final

@final
class Sealed:
    def method(self) -> int:
        return 42
`,
  },
];

const examplesEl = document.getElementById("examples");
for (const [i, ex] of EXAMPLES.entries()) {
  const opt = document.createElement("option");
  opt.value = String(i);
  opt.textContent = ex.label;
  examplesEl.appendChild(opt);
}
examplesEl.addEventListener("change", () => {
  const i = examplesEl.value;
  if (i === "") return;
  const code = EXAMPLES[Number(i)].code;
  inputView.dispatch({
    changes: { from: 0, to: inputView.state.doc.length, insert: code },
  });
  examplesEl.value = "";
});

const sourceEl = document.getElementById("source");
sourceEl.value = currentSource;
sourceEl.addEventListener("change", async () => {
  const next = sourceEl.value;
  if (next === currentSource) return;
  const prev = currentSource;
  sourceEl.disabled = true;
  setOverlay("loading", `Installing retrofy — ${SOURCES[next].label}…`);
  try {
    const pyodide = await pyodideReady;
    await installRetrofy(pyodide, next, { force: true });
    currentSource = next;
    runConvert(inputView.state.doc.toString());
  } catch (err) {
    setStatus(`Install failed: ${err.message}`, "error");
    setOverlay("error", `Install failed: ${err.message}`);
    sourceEl.value = prev;
  } finally {
    sourceEl.disabled = false;
  }
});

window.__playground = { inputView, outputView, pyodideReady };
