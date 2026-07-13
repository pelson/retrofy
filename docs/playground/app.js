import { EditorView, basicSetup } from "https://esm.sh/codemirror@6.0.1";
import { EditorState } from "https://esm.sh/@codemirror/state@6.5.2";
import { python } from "https://esm.sh/@codemirror/lang-python@6.1.7";

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
  pyodide.runPython("import retrofy");
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
