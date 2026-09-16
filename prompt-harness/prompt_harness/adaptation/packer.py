"""改编层 v0 — pack 落盘 + inkjs 调试预览页（规格 §11）。

- write_pack：把全部 JSON（UTF-8、indent=2、ensure_ascii=False）+ ink/ + preview.html
  写入 `<book>/.ainovel/adaptation/<pack_id>/`。
- 校验失败（errors 非空）时调用方把目录名换成 `<pack_id>.failed/` 保留中间产物。
- preview.html：单文件内嵌 story.ink.json + vendored inkjs 运行时；支持旁白/对白/
  内心显示、选择、flag 调试面板、stage 演出提示、结局、重开。仅验收调试用。
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import PackData, ValidationReport

INKJS_RUNTIME = Path(__file__).resolve().parents[2] / "third_party" / "inkjs" / "inkjs" / "dist" / "ink.js"


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def collect_files(pack: PackData, ink_text: str, story_ink_json: str | None,
                  preview_html: str | None,
                  validation: "ValidationReport | None" = None) -> dict[str, str]:
    """PackData → {相对路径: 文本内容}。validation 传入时一并落盘 validation.json。"""
    files: dict[str, str] = {
        "pack.json": dumps(pack.info.model_dump(by_alias=True)),
        "story.json": dumps(pack.story.model_dump()),
        "characters.json": dumps(pack.characters.model_dump()),
        "world.json": dumps(pack.world.model_dump()),
        "assets.json": dumps(pack.assets.model_dump()),
        "interaction.json": dumps(pack.interaction.model_dump()),
        # llm_zones 的 global 是 Python 关键字别名，序列化时还原为 "global"
        "llm_zones.json": dumps({
            "zones": [z.model_dump() for z in pack.llm_zones.zones],
            "global": pack.llm_zones.global_.model_dump(),
        }),
        "ink/story.ink": ink_text,
    }
    if validation is not None:
        files["validation.json"] = dumps(validation.model_dump())
    if story_ink_json is not None:
        files["ink/story.ink.json"] = story_ink_json
    if preview_html is not None:
        files["preview.html"] = preview_html
    return files


def write_pack(pack_dir: Path, files: dict[str, str]) -> list[Path]:
    """落盘全部文件，返回写入路径列表（相对 pack_dir）。"""
    pack_dir = Path(pack_dir)
    written: list[Path] = []
    for rel, content in files.items():
        p = pack_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        written.append(p)
    return written


def make_preview_html(story_ink_json: str, flag_names: list[str],
                      inkjs_runtime: Path | None = None) -> str:
    """生成单文件 preview.html：内嵌 inkjs 运行时 + story.ink.json。"""
    runtime_path = inkjs_runtime or INKJS_RUNTIME
    runtime = runtime_path.read_text(encoding="utf-8") if runtime_path.is_file() else (
        "/* inkjs runtime 未找到（third_party/inkjs/inkjs/dist/ink.js 缺失），"
        "预览不可用；编译产物不受影响。 */")

    # ink 变量名不含点（flags.read_card → flags_read_card）
    flag_names = [str(n).replace(".", "_") for n in flag_names]
    story_json_safe = story_ink_json.replace("</script", "<\\/script")
    runtime_safe = runtime.replace("</script", "<\\/script")
    flags_safe = json.dumps(flag_names, ensure_ascii=False)

    return _PREVIEW_TEMPLATE \
        .replace("/*__INKJS_RUNTIME__*/", runtime_safe) \
        .replace("/*__STORY_JSON__*/", story_json_safe) \
        .replace("/*__FLAGS__*/", flags_safe)


_PREVIEW_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Adaptation Pack 预览（inkjs 调试）</title>
<style>
  :root { --bg:#141216; --panel:#1e1a22; --fg:#d8d2c8; --dim:#8d8798; --acc:#b9552f; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.9 "Noto Serif SC", Georgia, serif; }
  #app { display:flex; height:100vh; }
  #main { flex:1; overflow-y:auto; padding:32px 15%; }
  #side { width:260px; background:var(--panel); padding:20px; overflow-y:auto;
          border-left:1px solid #2e2936; }
  .line { margin:0 0 14px; }
  .dialogue { color:#e8e0d2; }
  .dialogue .speaker { color:var(--acc); margin-right:.6em; font-weight:600; }
  .inner { color:#9fb3c8; font-style:italic; }
  .stage { color:#c8a15a; font-size:13px; letter-spacing:.05em; margin:10px 0 18px; }
  .stage::before { content:"【演出】"; }
  #choices { margin-top:28px; }
  #choices button { display:block; width:100%; text-align:left; margin:0 0 10px;
    padding:10px 16px; background:#241f2b; color:var(--fg);
    border:1px solid #3a3344; border-radius:6px; cursor:pointer; font:inherit; }
  #choices button:hover { border-color:var(--acc); }
  #ending { margin-top:36px; padding:24px; border:1px solid var(--acc);
    border-radius:8px; text-align:center; }
  #ending h2 { margin:0 0 8px; color:var(--acc); }
  #ending .knot { color:var(--dim); font-size:13px; }
  #restart { margin-top:14px; padding:8px 28px; background:var(--acc); color:#fff;
    border:0; border-radius:6px; cursor:pointer; font:inherit; }
  #side h3 { margin:0 0 10px; font-size:14px; color:var(--dim); }
  .flag { display:flex; justify-content:space-between; font-size:13px;
    padding:4px 0; border-bottom:1px dashed #2e2936; }
  .flag.on { color:#7fbf7f; } .flag.off { color:var(--dim); }
  #err { color:#e06c5a; white-space:pre-wrap; }
</style>
</head>
<body>
<div id="app">
  <div id="main"><div id="story"></div></div>
  <div id="side">
    <h3>Flags 调试面板</h3><div id="flags"></div>
  </div>
</div>
<script>/*__INKJS_RUNTIME__*/</script>
<script id="story-json" type="application/json">/*__STORY_JSON__*/</script>
<script>
(function () {
  var FLAGS = /*__FLAGS__*/;
  var storyEl = document.getElementById("story");
  var flagsEl = document.getElementById("flags");
  var story;

  function newStory() {
    var raw = document.getElementById("story-json").textContent;
    var storyJson = JSON.parse(raw);
    story = new inkjs.Story(storyJson);
    story.BindExternalFunction("stage", function (cue) {
      var p = document.createElement("p");
      p.className = "stage";
      p.textContent = cue;
      storyEl.appendChild(p);
    });
    storyEl.innerHTML = "";
    renderFlags();
  }

  function renderFlags() {
    flagsEl.innerHTML = "";
    FLAGS.forEach(function (name) {
      var val;
      try { val = story.variablesState[name]; } catch (e) { val = null; }
      var row = document.createElement("div");
      row.className = "flag " + (val ? "on" : "off");
      row.innerHTML = "<span>" + name + "</span><span>" + (val ? "true" : "false") + "</span>";
      flagsEl.appendChild(row);
    });
  }

  function addLine(cls, speaker, text) {
    var p = document.createElement("p");
    p.className = "line " + cls;
    if (speaker) {
      var s = document.createElement("span");
      s.className = "speaker";
      s.textContent = speaker;
      p.appendChild(s);
    }
    p.appendChild(document.createTextNode(text));
    storyEl.appendChild(p);
  }

  function showEnding() {
    var knot = "";
    try { knot = story.state.currentKnotName || ""; } catch (e) {}
    var div = document.createElement("div");
    div.id = "ending";
    div.innerHTML = "<h2>—— 完 ——</h2><div class='knot'>结局：" + knot +
      "</div><button id='restart'>重新开始</button>";
    storyEl.appendChild(div);
    document.getElementById("restart").onclick = start;
    renderFlags();
  }

  function step() {
    try {
      while (story.canContinue) {
        var text = story.Continue().trim();
        if (!text) continue;
        addLine("", null, text);
      }
      if (story.currentChoices.length) {
        var box = document.createElement("div");
        box.id = "choices";
        story.currentChoices.forEach(function (c, i) {
          var b = document.createElement("button");
          b.textContent = c.text;
          b.onclick = function () {
            story.ChooseChoiceIndex(i);
            box.remove();
            step();
          };
          box.appendChild(b);
        });
        storyEl.appendChild(box);
      } else if (!story.canContinue) {
        showEnding();
      }
      renderFlags();
      var main = document.getElementById("main");
      main.scrollTop = main.scrollHeight;
    } catch (e) {
      var p = document.createElement("p");
      p.id = "err";
      p.textContent = "预览运行错误：" + e.message;
      storyEl.appendChild(p);
    }
  }

  function start() { newStory(); step(); }
  try {
    if (typeof inkjs === "undefined") throw new Error("inkjs 运行时未加载");
    start();
  } catch (e) {
    storyEl.innerHTML = "<p id='err'>初始化失败：" + e.message + "</p>";
  }
})();
</script>
</body>
</html>
"""
