from __future__ import annotations

import html
import json
from pathlib import Path

from core.audit_catalog import audit_group_name

from .audit_service import normalize_audit_summary


def _embedded_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _with_extracted_fields(summary: dict[str, object], result_root: Path) -> dict[str, object]:
    """Embed item field and AI files so a file:// report remains fully inspectable."""
    payload = normalize_audit_summary(summary)
    audit = payload.get("audit")
    items = audit.get("items", []) if isinstance(audit, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        item["group_name"] = audit_group_name(str(item.get("item_id") or ""))
        item["extracted_fields"] = None
        relative = item.get("fields_file")
        if relative:
            target = (result_root / str(relative)).resolve()
            try:
                target.relative_to(result_root.resolve())
            except ValueError:
                pass
            else:
                if target.is_file():
                    try:
                        item["extracted_fields"] = json.loads(
                            target.read_text(encoding="utf-8-sig")
                        )
                    except (OSError, json.JSONDecodeError):
                        item["extracted_fields"] = {"读取状态": "字段文件无法读取"}

        narration = item.get("ai_narration")
        if not isinstance(narration, dict):
            item["ai_narration"] = None
        ai_relative = item.get("ai_file") or item.get("ai_narration_file")
        if not ai_relative and relative:
            ai_relative = str(Path(str(relative)).parent / "AI复述.json")
        if not ai_relative:
            continue
        ai_target = (result_root / str(ai_relative)).resolve()
        try:
            ai_target.relative_to(result_root.resolve())
        except ValueError:
            continue
        if ai_target.is_file():
            try:
                loaded = json.loads(ai_target.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                item["ai_narration"] = loaded
    return payload


def generate_report(summary: dict[str, object], output_path: Path) -> Path:
    target = Path(output_path)
    report_payload = _with_extracted_fields(summary, target.parent)
    system = report_payload.get("system", {})
    name = html.escape(str(system.get("name", "单系统稽核报告"))) if isinstance(system, dict) else "单系统稽核报告"
    document = _REPORT_TEMPLATE.replace("__SYSTEM_NAME__", name).replace(
        "__AUDIT_DATA__", _embedded_json(report_payload)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return target


_REPORT_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN" data-report-version="2">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>__SYSTEM_NAME__ · 稽核汇总报告</title>
  <style>
    :root{color-scheme:light;--ink:#0b2744;--muted:#71879f;--line:#d8e6f2;--blue:#087bd3;--cyan:#24a9e8;--green:#139a70;--red:#da3b58;--amber:#b77b13;--panel:rgba(255,255,255,.9)}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(circle at 8% 12%,rgba(62,154,224,.15),transparent 26%),radial-gradient(circle at 92% 8%,rgba(118,198,241,.2),transparent 30%),#f3f7fb}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.3;background-image:linear-gradient(rgba(69,127,173,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(69,127,173,.055) 1px,transparent 1px);background-size:30px 30px}
    button,input,select{font:inherit}.shell{position:relative;max-width:1500px;margin:auto;padding:38px 30px 70px}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:24px}.kicker{display:flex;align-items:center;gap:10px;color:#3279ad;font-size:12px;font-weight:700;letter-spacing:.12em}.kicker i{width:34px;height:2px;background:linear-gradient(90deg,var(--blue),transparent)}.top h1{max-width:1060px;margin:14px 0 8px;font-size:clamp(30px,4vw,54px);line-height:1.12;letter-spacing:-.035em}.subtitle{max-width:920px;overflow:hidden;color:var(--muted);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.stamp{flex:none;border:1px solid #ccdfed;border-radius:14px;padding:13px 16px;background:rgba(255,255,255,.72);box-shadow:0 10px 35px rgba(44,98,139,.08);color:#637f98;font-size:12px}
    .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:13px;margin:28px 0 16px}.card,.panel{border:1px solid rgba(181,207,226,.82);background:var(--panel);box-shadow:0 18px 55px rgba(52,92,125,.09),inset 0 1px rgba(255,255,255,.8);backdrop-filter:blur(14px)}.card{border-radius:18px;padding:17px 18px}.label{color:var(--muted);font-size:12px}.value{margin-top:7px;font-size:31px;font-weight:760}.value.green{color:var(--green)}.value.red{color:var(--red)}.value.amber{color:var(--amber)}.value.blue{color:var(--blue)}
    .dashboard{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px}.panel{border-radius:22px;padding:20px}.panel-title{font-weight:720}.donut-wrap{position:relative;width:184px;height:184px;margin:24px auto}.donut{--pass:0deg;--fail:0deg;width:184px;height:184px;border-radius:50%;background:conic-gradient(var(--green) 0 var(--pass),var(--red) var(--pass) var(--fail),#e6b24d var(--fail) 360deg);display:grid;place-items:center;box-shadow:0 18px 44px rgba(23,106,158,.12)}.donut:after{content:"";width:132px;height:132px;border-radius:50%;background:#f9fcfe;box-shadow:inset 0 0 0 1px #dae8f2}.donut-center{position:absolute;inset:0;z-index:1;display:grid;place-content:center;text-align:center;pointer-events:none}.donut-center strong{display:block;font-size:34px}.legend{display:grid;gap:11px;margin-top:15px}.legend-row{display:flex;justify-content:space-between;color:#5f7b92;font-size:13px}.dot{display:inline-block;width:8px;height:8px;margin-right:8px;border-radius:50%}.category-note{margin-top:22px;border-top:1px solid var(--line);padding-top:16px;color:#7b91a4;font-size:12px;line-height:1.8}
    .toolbar{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:16px}.toolbar input,.toolbar select{height:42px;border:1px solid #cfdfeb;border-radius:11px;background:#f9fcfe;color:var(--ink);padding:0 12px;outline:none}.toolbar input{flex:1;min-width:230px}.toolbar input:focus,.toolbar select:focus{border-color:#67acd9;box-shadow:0 0 0 3px rgba(42,142,207,.09)}
    .item-head,.item{display:grid;grid-template-columns:minmax(220px,1fr) 96px minmax(220px,1.2fr) minmax(180px,1fr);gap:12px;align-items:center}.item-head{padding:0 14px 8px;color:#8ba0b2;font-size:10px}.items{display:grid;gap:8px;max-height:710px;overflow:auto;padding-right:4px}.item{width:100%;border:1px solid #dce8f1;border-radius:14px;padding:14px;text-align:left;background:rgba(250,253,255,.9);color:var(--ink);cursor:pointer;transition:.18s}.item:hover{transform:translateY(-1px);border-color:#8cc5e7;box-shadow:0 10px 28px rgba(40,104,149,.09)}.identity{min-width:0}.identity .name{display:-webkit-box;overflow:hidden;font-size:13px;font-weight:700;line-height:1.45;-webkit-box-orient:vertical;-webkit-line-clamp:2}.identity .meta{display:block;margin-top:6px;color:#6d8ca4;font-size:10px}.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.badge{display:inline-flex;width:max-content;align-items:center;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:700}.passed{color:#087b58;background:#e5f6ef}.failed{color:#c12e49;background:#fdebf0}.not_applicable{color:#627b8f;background:#edf3f7}.extracted,.message{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px}.extracted{color:#4d7896}.extracted.none{color:#9baab7}.message{color:#536c81}
    dialog{width:min(920px,calc(100vw - 28px));max-height:88vh;border:1px solid #c9ddeb;border-radius:22px;padding:0;background:#f9fcfe;color:var(--ink);box-shadow:0 34px 120px rgba(35,72,102,.28)}dialog::backdrop{background:rgba(20,50,74,.28);backdrop-filter:blur(5px)}.dialog-head{display:flex;justify-content:space-between;align-items:center;padding:19px 22px;border-bottom:1px solid var(--line);background:white}.dialog-body{max-height:calc(88vh - 74px);overflow:auto;padding:22px}.close{width:34px;height:34px;border:1px solid #d3e1eb;border-radius:10px;background:#f4f8fb;color:#577288;cursor:pointer}.detail-grid{display:grid;grid-template-columns:120px 1fr;gap:12px;font-size:13px}.detail-grid dt{color:var(--muted)}.detail-grid dd{margin:0;word-break:break-word}.field-list{display:grid;gap:9px}.field{border:1px solid #d9e7f1;border-radius:13px;padding:13px;background:white}.field b{display:block;margin-bottom:8px;color:#35779f;font-size:12px}.field pre{max-height:340px;overflow:auto;margin:0;border-radius:9px;padding:12px;background:#f1f6fa;color:#36536a;white-space:pre-wrap;word-break:break-word;font-size:12px}.ai-narration{border:1px solid #cce1ef;border-radius:14px;padding:15px;background:#f0f8fd}.ai-narration pre{margin:0;color:#355a73;white-space:pre-wrap;word-break:break-word;font:13px/1.75 "PingFang SC","Microsoft YaHei",sans-serif}.ai-narration.failed{border-color:#efd9a7;background:#fff9ec;color:#856321}.ai-narration.skipped{border-color:#d8e2e9;background:#f3f6f8;color:#6d8190}.issue{margin-top:8px;border:1px solid #efd8a4;border-radius:11px;padding:11px 12px;background:#fff9ec;color:#795d24}.empty{padding:50px;text-align:center;color:var(--muted)}
    @media(max-width:1000px){.cards{grid-template-columns:repeat(2,1fr)}.dashboard{grid-template-columns:1fr}.donut-panel{display:none}}@media(max-width:720px){.shell{padding:24px 14px 50px}.top{display:block}.stamp{display:inline-block;margin-top:16px}.cards{grid-template-columns:1fr 1fr}.item-head{display:none}.item{grid-template-columns:1fr 90px}.identity,.extracted,.message{grid-column:1/-1}.extracted,.message{white-space:normal}.detail-grid{grid-template-columns:1fr}.detail-grid dt{margin-top:5px}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="top"><div><div class="kicker"><i></i>稽核汇总报告</div><h1 id="systemName"></h1><div class="subtitle" id="sourcePath"></div></div><div class="stamp" id="generatedAt"></div></header>
    <section class="cards" id="cards"></section>
    <section class="dashboard">
      <aside class="panel donut-panel"><div class="panel-title">状态分布</div><div class="donut-wrap"><div class="donut" id="donut"></div><div class="donut-center"><strong id="rate">0%</strong><span class="label">通过率</span></div></div><div class="legend" id="legend"></div><div class="category-note">分类使用材料中文名称，稽核编号仅作为单项追踪标识。</div></aside>
      <article class="panel"><div class="toolbar"><input id="search" aria-label="搜索稽核项" placeholder="搜索分类、稽核项、提取内容或结论…"><select id="group" aria-label="按分类筛选"><option value="">全部分类</option></select><select id="status" aria-label="按状态筛选"><option value="">全部状态</option></select></div><div class="item-head"><span>稽核项</span><span>状态</span><span>中间提取内容</span><span>稽核结论</span></div><div class="items" id="items"></div></article>
    </section>
  </main>
  <dialog id="detail"><div class="dialog-head"><div><strong id="detailId"></strong><span id="detailBadge" class="badge" style="margin-left:12px"></span></div><button class="close" onclick="detail.close()" aria-label="关闭">×</button></div><div class="dialog-body" id="detailBody"></div></dialog>
  <script type="application/json" id="audit-data">__AUDIT_DATA__</script>
  <script>
    const data=JSON.parse(document.getElementById('audit-data').textContent);
    const items=data.audit.items||[];
    const labels={passed:'通过',failed:'不通过',not_applicable:'不适用'};
    const count=s=>items.filter(x=>x.status===s).length,total=items.length,pass=count('passed'),fail=count('failed'),notApplicable=count('not_applicable'),applicable=pass+fail;
    document.getElementById('systemName').textContent=data.system.name;
    document.getElementById('sourcePath').textContent=data.system.source_path;
    document.getElementById('generatedAt').textContent='生成时间 · '+data.generated_at;
    const cardData=[['稽核项',total,'blue'],['通过',pass,'green'],['不通过',fail,'red'],['不适用',notApplicable,'amber'],['解析材料',data.parse.file_count||0,'blue']];
    document.getElementById('cards').innerHTML=cardData.map(x=>`<article class="card"><div class="label">${x[0]}</div><div class="value ${x[2]}">${x[1]}</div></article>`).join('');
    const rate=applicable?Math.round(pass/applicable*100):0,passDeg=total?pass/total*360:0,failDeg=total?(pass+fail)/total*360:0;
    document.getElementById('rate').textContent=rate+'%';document.getElementById('donut').style.cssText=`--pass:${passDeg}deg;--fail:${failDeg}deg`;
    document.getElementById('legend').innerHTML=`<div class="legend-row"><span><i class="dot" style="background:var(--green)"></i>通过</span><b>${pass}</b></div><div class="legend-row"><span><i class="dot" style="background:var(--red)"></i>不通过</span><b>${fail}</b></div><div class="legend-row"><span><i class="dot" style="background:#e6b24d"></i>不适用</span><b>${notApplicable}</b></div>`;
    const status=document.getElementById('status'),group=document.getElementById('group'),search=document.getElementById('search');
    [...new Set(items.map(x=>x.status))].sort().forEach(s=>status.insertAdjacentHTML('beforeend',`<option value="${s}">${labels[s]||s}</option>`));
    [...new Set(items.map(x=>x.group_name))].filter(Boolean).sort().forEach(s=>group.insertAdjacentHTML('beforeend',`<option value="${s}">${s}</option>`));
    function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
    function fieldPreview(x){return(x.extracted_summary||[]).map(v=>v.field+'：'+v.value).join(' ｜ ')||'未生成提取字段'}
    function render(){const q=search.value.trim().toLowerCase();const filtered=items.filter(x=>(!status.value||x.status===status.value)&&(!group.value||x.group_name===group.value)&&(!q||JSON.stringify({group_name:x.group_name,item_id:x.item_id,item_name:x.item_name,status:x.status,message:x.message,issues:x.issues,extracted_summary:x.extracted_summary}).toLowerCase().includes(q)));document.getElementById('items').innerHTML=filtered.length?filtered.map(x=>`<button class="item" data-id="${esc(x.item_id)}"><span class="identity"><b class="name">${esc(x.item_name||x.item_id)}</b><small class="meta">${esc(x.group_name)} · <span class="id">${esc(x.item_id)}</span></small></span><span class="badge ${esc(x.status)}">${esc(labels[x.status]||x.status)}</span><span class="extracted ${(x.extracted_summary||[]).length?'':'none'}">${esc(fieldPreview(x))}</span><span class="message">${esc(x.message)}</span></button>`).join(''):'<div class="empty">没有符合条件的稽核项</div>';document.querySelectorAll('.item').forEach(el=>el.onclick=()=>openDetail(items.find(x=>x.item_id===el.dataset.id)))}
    function renderFields(fields){if(!fields||typeof fields!=='object')return'<div class="label">该稽核项没有生成提取字段</div>';const entries=Object.entries(fields);return entries.length?`<div class="field-list">${entries.map(([name,value])=>`<div class="field"><b>${esc(name)}</b><pre>${esc(typeof value==='object'?JSON.stringify(value,null,2):value)}</pre></div>`).join('')}</div>`:'<div class="label">该稽核项没有生成提取字段</div>'}
    function renderNarration(x){const n=x.ai_narration;if(n.status==='completed'&&n.text)return`<div class="ai-narration"><pre>${esc(n.text)}</pre></div>`;if(n.status==='skipped')return`<div class="ai-narration skipped">${esc(n.text||n.error_message||'未生成补充说明')}</div>`;return`<div class="ai-narration failed">${esc(n.message||n.error_message||n.text||'稽核说明生成失败')}</div>`}
    function renderNarrationSection(x){return x.ai_narration&&typeof x.ai_narration==='object'?`<h3 style="margin-top:24px">稽核说明</h3>${renderNarration(x)}`:''}
    function openDetail(x){detailId.innerHTML=`<span style="display:grid;gap:5px"><b>${esc(x.item_name||x.item_id)}</b><small class="meta">${esc(x.group_name)} · <span class="id">${esc(x.item_id)}</span></small></span>`;detailBadge.className='badge '+x.status;detailBadge.textContent=labels[x.status]||x.status;detailBody.innerHTML=`<h3>稽核结论</h3><dl class="detail-grid"><dt>业务分类</dt><dd>${esc(x.group_name)}</dd><dt>稽核项描述</dt><dd>${esc(x.item_name||x.item_id)}</dd><dt>编号</dt><dd>${esc(x.item_id)}</dd><dt>结论</dt><dd>${esc(x.message)}</dd><dt>来源文件</dt><dd>${(x.source_documents||[]).map(esc).join('<br>')||'—'}</dd><dt>字段结果</dt><dd>${esc(x.fields_file||'—')}</dd><dt>稽核结果</dt><dd>${esc(x.result_file||'—')}</dd></dl><h3 style="margin-top:24px">关键提取结果</h3>${renderFields(x.extracted_fields)}${renderNarrationSection(x)}<h3 style="margin-top:24px">问题与证据</h3>${(x.issues||[]).length?x.issues.map(i=>`<div class="issue">${esc(i)}</div>`).join(''):'<div class="label">未记录问题</div>'}`;detail.showModal()}
    [search,status,group].forEach(el=>el.addEventListener('input',render));render();
  </script>
</body></html>'''
