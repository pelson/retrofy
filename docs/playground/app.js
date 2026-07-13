import { EditorView, basicSetup } from "https://esm.sh/codemirror@6.0.2?deps=@codemirror/state@6.5.2";
import { EditorState } from "https://esm.sh/@codemirror/state@6.5.2";
import { python } from "https://esm.sh/@codemirror/lang-python@6.1.7?deps=@codemirror/state@6.5.2";
import { createTwoFilesPatch } from "https://esm.sh/diff@5.2.0";

const PYODIDE_VERSION = "v0.29.4";
const RETROFY_INDEX = "https://pelson.github.io/retrofy/simple/";

const statusEl = document.getElementById("status");

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = cls;
}

async function bootPyodide() {
  setStatus("Booting Pyodide…");
  const { loadPyodide } = await import(
    `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/pyodide.mjs`
  );
  const pyodide = await loadPyodide({
    indexURL: `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`,
  });
  setStatus("Installing retrofy…");
  await pyodide.loadPackage("micropip");
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(
    "retrofy",
    { index_urls: [RETROFY_INDEX, "https://pypi.org/simple/"] },
  );
  pyodide.runPython("from retrofy._converters import convert");
  setStatus("Ready.", "ready");
  return pyodide;
}

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

const runConvert = debounce(async (source) => {
  try {
    const arr = await convert(source);
    if (arr[0] === "ok") {
      setOutput(arr[1]);
      setStatus("Ready.", "ready");
      document.dispatchEvent(
        new CustomEvent("retrofy:converted", {
          detail: { input: source, output: arr[1] },
        }),
      );
    } else {
      const [, , line, col, msg] = arr;
      const where = line ? ` (line ${line}${col ? `, col ${col}` : ""})` : "";
      setStatus(`SyntaxError${where}: ${msg}`, "error");
    }
  } catch (err) {
    setStatus(`Error: ${err.message}`, "error");
  }
}, 300);

document.addEventListener("retrofy:input-change", (e) => runConvert(e.detail));
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
