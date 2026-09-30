"""Self-contained HTML report: find, compare and verify company facts on desktop and mobile.

All values are rendered with textContent (no innerHTML of data), so page content cannot inject markup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SECTION_TITLES = {
    "legal_identity_and_brand": "Identity & brand",
    "annual_accounts": "Annual accounts",
    "leadership_and_workplaces": "Leadership & workplaces",
    "website_and_profiles": "Website & profiles",
    "hiring_and_activity": "Hiring & activity",
}


def _slim(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "org": envelope["organisation_number"], "name": envelope.get("name"), "status": envelope.get("company_status"),
        "sections": {k: v["state"] for k, v in envelope.get("sections", {}).items()},
        "summary": envelope.get("summary", {}).get("summary"),
        "statements": envelope.get("summary", {}).get("statements", []),
        "narrative": (envelope.get("summary", {}).get("narrative") or {}).get("sentences", []),
        "claims": [{k: c.get(k) for k in ("id", "field", "section", "label", "value", "availability", "reason",
                                          "evidence_ids", "as_of", "stale")} for c in envelope.get("claims", [])],
        "evidence": {e["id"]: {k: e.get(k) for k in ("source_url", "human_url", "source_class", "retrieved_at",
                                                      "content_sha256", "claim_span", "json_path")}
                     for e in envelope.get("evidence", [])},
        "changes": [{k: c.get(k) for k in ("label", "field", "change", "change_type", "old", "new", "detected_at")}
                    for c in envelope.get("changes", [])],
        "checked": envelope.get("refresh", {}).get("checked_at"),
    }


def write_report(path: Path, envelopes: list[dict[str, Any]], report: dict[str, Any]) -> None:
    data = json.dumps([_slim(e) for e in envelopes], ensure_ascii=False).replace("</", "<\\/")
    meta = json.dumps({"run_id": report.get("run_id"), "completed_at": report.get("completed_at"),
                       "companies": report.get("emitted_envelopes"), "sections": SECTION_TITLES,
                       "requests": (report.get("operations") or {}).get("requests")},
                      ensure_ascii=False).replace("</", "<\\/")
    path.write_text(_TEMPLATE.replace("__DATA__", data).replace("__META__", meta), encoding="utf-8")


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost company profiles</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1b16;--muted:#6b665c;--line:#e3ded3;--accent:#1f5f8b;
--ok:#1e7a4c;--warn:#a86b00;--bad:#b3261e;--na:#8a8478;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1e2125;--ink:#ecebe7;--muted:#a19d94;--line:#30343a;
--accent:#7db7e0;--ok:#5cc28c;--warn:#e0a84a;--bad:#f0877f;--na:#8f8b83}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.45}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}h1{font-size:1.5rem;margin:0}
.meta{color:var(--muted);font-size:.85rem}.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
input,select,button{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
input{flex:1 1 180px;min-width:0}select{max-width:100%}button{cursor:pointer}button:hover,button:focus-visible{border-color:var(--accent);outline:none}
main{max-width:1200px;margin:auto;padding:8px 16px 40px;display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:16px}
@media (max-width:820px){main{grid-template-columns:minmax(0,1fr)}}main>*{min-width:0}.meta{overflow-wrap:anywhere}
.list{display:flex;flex-direction:column;gap:6px;max-height:80vh;overflow:auto}
@media (max-width:820px){.list{max-height:40vh}}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;text-align:left;width:100%}
.item[aria-current=true]{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.item b{display:block}.dots{display:flex;gap:4px;margin-top:6px}.dot{width:10px;height:10px;border-radius:50%;background:var(--na)}
.dot.available{background:var(--ok)}.dot.blocked,.dot.failed{background:var(--bad)}.dot.ambiguous{background:var(--warn)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.panel h2{margin:.2rem 0}.summary{margin:.6rem 0 1rem}
section{border-top:1px solid var(--line);padding-top:10px;margin-top:12px}section h3{margin:0 0 6px;font-size:1rem}
.claim{display:grid;grid-template-columns:minmax(120px,200px) 1fr;gap:8px;padding:6px 0;border-bottom:1px dashed var(--line)}
@media (max-width:560px){.claim{grid-template-columns:1fr}}
.label{color:var(--muted);font-size:.85rem}.value{overflow-wrap:anywhere}
.badge{display:inline-block;font-size:.72rem;padding:1px 6px;border-radius:999px;border:1px solid currentColor;margin-left:4px}
.b-available{color:var(--ok)}.b-not_available,.b-not_applicable{color:var(--na)}.b-blocked,.b-failed{color:var(--bad)}.b-ambiguous{color:var(--warn)}
details.ev{font-size:.8rem;color:var(--muted);margin-top:2px}details.ev a{color:var(--accent)}
.stmt{margin:.3rem 0}.stmt small{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:.85rem}td,th{border:1px solid var(--line);padding:6px;vertical-align:top;text-align:left;overflow-wrap:anywhere}
.cmp{overflow:auto}.empty{color:var(--muted)}
</style></head><body>
<header><h1>Signalpost company profiles</h1><div class="meta" id="meta"></div>
<div class="bar"><input id="q" type="search" placeholder="Search name or org number" aria-label="Search companies">
<select id="f" aria-label="Filter"><option value="">All companies</option></select>
<button id="cmpBtn" type="button">Compare selected (0)</button></div></header>
<main><nav class="list" id="list" aria-label="Companies"></nav><article class="panel" id="panel" aria-live="polite"></article></main>
<script>
const DATA=__DATA__;const META=__META__;
const $=s=>document.querySelector(s);
function h(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(k==="class")e.className=v;else if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}for(const k of kids.flat()){if(k==null)continue;e.append(k instanceof Node?k:document.createTextNode(String(k)))}return e}
function fmt(v){if(v==null)return"";if(Array.isArray(v))return v.map(fmt).join("; ");if(typeof v==="object"){if("amount"in v&&"currency"in v)return v.currency+" "+Number(v.amount).toLocaleString("en")+(v.year?" ("+v.year+")":"");if(v.text)return v.text;if(v.name)return v.name+(v.role?" ("+v.role+")":"");if(v.final_url)return v.final_url+" · "+(v.verification||"");if(v.title)return(v.date||v.published||"")+" "+v.title;if(v.year&&v.copy_url)return v.year;if(v.description&&v.date)return v.date+" "+v.description;if(v.code)return v.code+" "+(v.description||"");return Object.entries(v).map(([k,x])=>k+": "+fmt(x)).join(", ")}return String(v)}
function safeUrl(u){try{const p=new URL(u);return p.protocol==="https:"||p.protocol==="http:"?p.href:null}catch(e){return null}}
const selected=new Set();let current=null;
$("#meta").textContent=`${META.companies} companies · run ${META.run_id||""} · completed ${META.completed_at||""} · ${META.requests||0} requests · every value links to its source`;
for(const[k,t]of Object.entries(META.sections)){$("#f").append(h("option",{value:k},"Has "+t.toLowerCase()))}
function renderList(){const q=$("#q").value.trim().toLowerCase(),f=$("#f").value;const list=$("#list");list.replaceChildren();
const rows=DATA.filter(d=>(!q||(d.name||"").toLowerCase().includes(q)||d.org.includes(q.replace(/\s/g,"")))&&(!f||d.sections[f]==="available"));
for(const d of rows.slice(0,400)){const dots=h("span",{class:"dots","aria-label":"section coverage"},Object.keys(META.sections).map(s=>h("span",{class:"dot "+(d.sections[s]||""),title:META.sections[s]+": "+(d.sections[s]||"")})));
const cb=h("input",{type:"checkbox","aria-label":"Select for comparison",onclick:ev=>{ev.stopPropagation();ev.target.checked?selected.add(d.org):selected.delete(d.org);$("#cmpBtn").textContent=`Compare selected (${selected.size})`}});if(selected.has(d.org))cb.checked=true;
list.append(h("button",{class:"item",type:"button","aria-current":String(current===d.org),onclick:()=>show(d.org)},h("b",{},d.name||d.org),h("span",{class:"meta"},d.org+" · "+(d.status||"")," "),cb,dots))}
if(!rows.length)list.append(h("p",{class:"empty"},"No companies match."))}
function evidence(d,c){const out=h("details",{class:"ev"},h("summary",{},"Source"));for(const id of c.evidence_ids||[]){const e=d.evidence[id];if(!e)continue;const url=safeUrl(e.source_url);const human=safeUrl(e.human_url);
out.append(h("div",{},url?h("a",{href:url,target:"_blank",rel:"noopener noreferrer"},e.source_url):e.source_url," · ",e.source_class||""," · retrieved ",e.retrieved_at||""),
human?h("div",{},h("a",{href:human,target:"_blank",rel:"noopener noreferrer"},"Open in Brønnøysund viewer")):null,
e.claim_span?h("div",{},"Span: “"+e.claim_span+"”"):null,e.json_path?h("div",{},"Field: "+e.json_path):null,h("div",{},"SHA-256 "+(e.content_sha256||"").slice(0,16)+"…"))}return out}
function show(org){current=org;const d=DATA.find(x=>x.org===org);const p=$("#panel");p.replaceChildren();renderList();
p.append(h("h2",{},d.name||d.org),h("div",{class:"meta"},d.org+" · "+(d.status||"")+" · checked "+(d.checked||"")),h("p",{class:"summary"},d.summary||""));
if(d.narrative&&d.narrative.length){p.append(h("h3",{},"Overview"));p.append(h("p",{class:"summary"},d.narrative.map(s=>[s.text+" ",h("small",{},"["+s.claim_ids.join(", ")+"] ")])))}
if(d.statements.length){p.append(h("h3",{},"Key points"));for(const s of d.statements)p.append(h("p",{class:"stmt"},s.text," ",h("small",{},s.claim_ids.length?"["+s.claim_ids.join(", ")+"]":"")))}
if(d.changes.length){const sec=h("section",{},h("h3",{},"Changes since previous check"));for(const c of d.changes)sec.append(h("div",{class:"claim"},h("div",{class:"label"},(c.label||c.field)+" · "+c.change_type),h("div",{class:"value"},c.change==="added"?"New: "+fmt(c.new):c.change==="removed"?"Removed: "+fmt(c.old):fmt(c.old)+" → "+fmt(c.new))));p.append(sec)}
for(const[s,title]of Object.entries(META.sections)){const sec=h("section",{},h("h3",{},title,h("span",{class:"badge b-"+d.sections[s]},d.sections[s])));
for(const c of d.claims.filter(c=>c.section===s)){const val=c.availability==="available"?h("div",{class:"value",id:c.id},fmt(c.value),c.stale?h("span",{class:"badge b-ambiguous"},"stale"):null,evidence(d,c)):h("div",{class:"value empty"},h("span",{class:"badge b-"+c.availability},c.availability)," ",c.reason||"");
sec.append(h("div",{class:"claim"},h("div",{class:"label"},(c.label||c.field)+" "+(c.as_of?"· "+c.as_of:"")),val))}p.append(sec)}}
function compare(){const ids=[...selected].slice(0,4);if(ids.length<2){alert("Select at least two companies with the checkboxes.");return}
const rows=DATA.filter(d=>ids.includes(d.org));const fields=["organization_form","business_address","industry_codes","employees","revenue","net_profit","equity","ceo","board_chair","official_website","social_profiles","job_postings"];
const p=$("#panel");p.replaceChildren(h("h2",{},"Comparison"));const t=h("table",{},h("tr",{},h("th",{},"Field"),rows.map(r=>h("th",{},r.name||r.org))));
for(const f of fields){t.append(h("tr",{},h("td",{},f.replace(/_/g," ")),rows.map(r=>{const c=r.claims.find(c=>c.field===f);return h("td",{},!c?"":c.availability==="available"?fmt(c.value):c.availability)})))}
p.append(h("div",{class:"cmp"},t))}
$("#q").addEventListener("input",renderList);$("#f").addEventListener("change",renderList);$("#cmpBtn").addEventListener("click",compare);
renderList();if(DATA.length)show(DATA[0].org);
</script></body></html>
"""
