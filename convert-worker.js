/* Converts PDFs on this device. Runs paper2phone.py with Pyodide (Python compiled to WebAssembly)
   and the official WebAssembly build of PyMuPDF, in a background worker so the app stays responsive.
   Nothing leaves the device: the PDF bytes go in, the reader file and EPUB come out. */
var PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
importScripts(PYODIDE + 'pyodide.js');

var ready = null;
function boot(post) {
  if (!ready) {
    ready = (async function () {
      post({ type: 'progress', text: 'Preparing the converter (first time only)' });
      var py = await loadPyodide({ indexURL: PYODIDE });
      await py.loadPackage('pymupdf');
      var src = await (await fetch('converter/paper2phone.py', { cache: 'no-cache' })).text();
      py.FS.mkdirTree('/app');
      py.FS.writeFile('/app/paper2phone.py', src);
      await py.runPythonAsync("import sys\nsys.path.insert(0, '/app')\nimport paper2phone as pp\npp.OFFLINE = True");
      return py;
    })();
    ready.catch(function () { ready = null; });
  }
  return ready;
}

function safeName(name) {
  name = String(name || 'paper.pdf').replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_');
  return /\.pdf$/i.test(name) ? name : name + '.pdf';
}

var chain = Promise.resolve();
self.onmessage = function (e) {
  var job = e.data;
  chain = chain.then(function () { return run(job); });      // one paper at a time
};

async function run(job) {
  var id = job.id;
  function post(m, transfer) { m.id = id; self.postMessage(m, transfer || []); }
  var dir = '/work/' + id;
  try {
    var py = await boot(post);
    post({ type: 'progress', text: 'Converting' });
    py.FS.mkdirTree(dir);
    var input = dir + '/' + safeName(job.name);
    py.FS.writeFile(input, new Uint8Array(job.bytes));
    py.globals.set('P2P_IN', input);
    py.globals.set('P2P_OUT', dir + '/out');
    var res = await py.runPythonAsync([
      'import json, gc',
      'p2p_msgs = []',
      'p2p_written, p2p_paper = pp.convert(P2P_IN, P2P_OUT, "both", "first-mention", 200, log=p2p_msgs.append)',
      'p2p_json = json.dumps({"files": [str(w) for w in p2p_written], "summary": p2p_paper.summary(),',
      '                       "warnings": p2p_paper.warnings, "title": p2p_paper.meta.get("title") or ""})',
      'del p2p_paper',
      'gc.collect()',
      'p2p_json'
    ].join('\n'));
    var r = JSON.parse(res);
    var files = r.files.map(function (f) { return { name: f.split('/').pop(), data: py.FS.readFile(f) }; });
    post({ type: 'done', files: files, summary: r.summary, warnings: r.warnings, title: r.title },
         files.map(function (f) { return f.data.buffer; }));
  } catch (err) {
    var msg = String((err && err.message) || err);
    var last = msg.trim().split('\n').pop();          // the Python exception line, not the whole traceback
    post({ type: 'error', message: last.slice(0, 400) });
  } finally {
    try {
      var py2 = await ready;
      py2.runPython('import shutil; shutil.rmtree("' + dir + '", ignore_errors=True)');
    } catch (e2) {}
  }
}
