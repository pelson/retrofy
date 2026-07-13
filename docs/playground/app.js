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
