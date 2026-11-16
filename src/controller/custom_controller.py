import urllib.parse
import re
import random
from typing import Optional, Type, Callable, Dict, Any, Union, Awaitable, TypeVar
from pydantic import BaseModel
from browser_use.agent.views import ActionResult, ActionModel
from browser_use.browser.context import BrowserContext
from browser_use.controller.service import Controller
from browser_use.controller.registry.service import RegisteredAction
import logging
import asyncio
import os
from datetime import datetime
from langchain_core.language_models.chat_models import BaseChatModel

from src.utils.mcp_client import create_tool_param_model, setup_mcp_client_and_tools
from browser_use.utils import time_execution_sync

logger = logging.getLogger(__name__)

Context = TypeVar('Context')


def _clean_text_field(val: str) -> str:
    if not val:
        return ""
    cleaned = re.sub(r'[\ue000-\uf8ff\x00-\x1f\x7f-\x9f]+', '', str(val))
    cleaned = re.sub(
        r'\b(bookmark_border|bookmark|more_vert|update|electric_bolt_fill|accessible|arrow_drop_down|keyboard_arrow_down|search)\b',
        '',
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'^[©®™•·\-\s|]+', '', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _is_obviously_irrelevant_card_title(title: str) -> Optional[str]:
    """Pre-screens job titles on the listing page to avoid opening non-IT/excluded ads."""
    t = (title or "").lower()
    if not t or t == "iş ilanları":
        return "Geçersiz kart başlığı"

    # Hard CV exclusions from candidate_profile.md
    cv_excluded = [
        ("sap ", "SAP ilanı"),
        ("sap/", "SAP ilanı"),
        ("abap", "ABAP ilanı"),
        ("fiori", "SAP Fiori ilanı"),
        ("s/4hana", "SAP S/4HANA ilanı"),
        ("salesforce", "Salesforce ilanı"),
        ("cobol", "COBOL ilanı"),
        ("ios ", "Sadece iOS ilanı"),
        ("swift", "Sadece Swift/iOS ilanı"),
        ("android", "Sadece Android ilanı"),
        ("kotlin", "Sadece Kotlin ilanı"),
        ("team lead", "Team Leader (Kıdem/Yönetici) ilanı"),
        ("takım lideri", "Takım Lideri ilanı"),
        ("ekip lideri", "Ekip Lideri ilanı"),
        ("engineering manager", "Engineering Manager ilanı"),
        ("yazılım müdürü", "Yönetici/Müdür ilanı"),
        ("director", "Director ilanı"),
        ("stajyer", "Stajyer ilanı"),
        ("intern", "Intern/Stajyer ilanı"),
        ("autosar", "Gömülü Otomotiv (AUTOSAR) ilanı"),
        ("plc ", "PLC/Otomasyon Donanım ilanı"),
    ]
    for kw, reason in cv_excluded:
        if kw in t:
            return reason

    # Non-software sponsored ads that Kariyer.net injects at the top of search pages
    non_it_keywords = [
        "hasta", "hemşire", "doktor", "hekim", "diş", "poliklinik", "eczacı", "ameliyathane",
        "mekanik teknisyen", "elektrik teknisyen", "bakım teknisyen", "makine mühendis",
        "inşaat mühendis", "ziraat", "gıda mühendis", "çevre mühendis", "tekstil",
        "muhasebe", "mali müşavir", "finans uzman", "insan kaynakları", "bordro",
        "satış danışman", "satış temsilci", "mağaza müdür", "kasiyer", "depo", "şoför",
        "kurye", "güvenlik görevli", "temizlik", "aşçı", "garson", "resepsiyon",
        "çağrı merkezi", "müşteri temsilci", "sekreter", "asistan", "öğretmen", "avukat",
    ]
    for kw in non_it_keywords:
        if kw in t:
            return f"Yazılım dışı ilan ({kw})"

    return None


# ==============================================================================
# DETERMINISTIC CV FIT ANALYSIS (save_job guard — LLM cannot save "olur herhalde")
# ==============================================================================
_CV_CORE_SKILLS = {
    "Python": r"\bpython\b",
    "PHP": r"\bphp\b",
    "Laravel": r"\blaravel\b",
    "FastAPI": r"\bfastapi\b",
    "Django": r"\bdjango\b",
    "Flask": r"\bflask\b",
    "Node.js": r"\bnode(?:\.?js)?\b|\bnestjs\b|\bexpress(?:\.js)?\b",
    "C#": r"(?<![a-z])c#|\bc sharp\b",
    ".NET": r"\.net\b|\basp\.net\b|\bdotnet\b",
    "Rust": r"\brust\b",
    "Tauri": r"\btauri\b",
    "C++": r"(?<![a-z])c\+\+",
    "OpenCV/YOLO/CV": r"\bopencv\b|\byolo\b|computer vision|görüntü işleme",
    "AI/LLM/RAG": r"\bllm\b|\brag\b|large language model|yapay zek[aâ]|\bgenai\b|generative ai|\bopenai\b|langchain",
}
_CV_FRONT_SKILLS = {
    "React": r"\breact(?:\.js|js)?\b|\bnext\.?js\b",
    "React Native": r"react native",
    "TypeScript": r"\btypescript\b|(?<![a-z])ts(?![a-z])",
    "JavaScript": r"\bjavascript\b|(?<![a-z])js(?![a-z])",
    "Electron": r"\belectron\b",
}
_CV_EXTRA_SKILLS = {
    "SQL": r"\bsql\b|\bpostgres(?:ql)?\b|\bmysql\b|\bmssql\b|sql server|t-sql",
    "MongoDB": r"\bmongo(?:db)?\b",
    "Docker": r"\bdocker\b",
    "REST API": r"\brest(?:ful)?\b|\bapi\b",
    "Linux": r"\blinux\b",
}
_HARD_EXCLUDE = {
    "SAP/ABAP": r"\bsap\b|\babap\b|\bfiori\b|s/4\s?hana",
    "Salesforce": r"\bsalesforce\b|\bapex\b",
    "COBOL": r"\bcobol\b",
    "Gömülü/AUTOSAR/PLC": r"\bautosar\b|\bplc\b|firmware|gömülü yazılım|embedded c\b",
}
_FOREIGN_BACKEND = r"\bjava\b|\bspring(?: boot)?\b|\bkotlin\b|\bswift\b|\bgolang\b|\bgo developer\b|\bscala\b|\bruby\b|\bobjective-c\b"
_SENIOR_TITLE = r"\bsenior\b|\bsr\.?\b|kıdemli|\blead\b|\blider\b|\bprincipal\b|\bstaff\b|\barchitect\b|\bmimar\b|\bmanager\b|müdür|\bhead of\b|direktör|\bdirector\b|\bchief\b|\bcto\b"
_JUNIOR_INTERN_TITLE = r"\bstaj|\bintern\b|\binternship\b"


def _tr_lower(s: str) -> str:
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def _extract_required_years(text: str) -> Optional[int]:
    """Finds minimum required experience years (ignores 'preferred / tercihen' mentions)."""
    low = _tr_lower(text)
    pattern = re.compile(
        r"(?:en az|minimum|min\.?|at least|least)?\s*(\d{1,2})\s*(?:\+|\'?\s*(?:-|–|to|ila)\s*\d{1,2})?\s*\+?\s*"
        r"(?:yıl|yil|sene|years?|yrs?)"
    )
    found = []
    for m in pattern.finditer(low):
        n = int(m.group(1))
        if n < 1 or n > 20:
            continue
        ctx = low[max(0, m.start() - 80): m.end() + 90]
        if not re.search(r"deneyim|tecrübe|tecrube|experience|çalışmış|calismis|background", ctx):
            continue
        if re.search(r"tercih|preferred|is a plus|nice to have|avantaj|bonus", ctx):
            continue
        found.append(n)
    return max(found) if found else None


def _analyze_job_fit(title: str, description: str) -> Dict[str, Any]:
    t_low = _tr_lower(title)
    d_low = _tr_lower(description)
    full = t_low + "\n" + d_low

    core = [k for k, rx in _CV_CORE_SKILLS.items() if re.search(rx, full)]
    front = [k for k, rx in _CV_FRONT_SKILLS.items() if re.search(rx, full)]
    extra = [k for k, rx in _CV_EXTRA_SKILLS.items() if re.search(rx, full)]
    excluded = [k for k, rx in _HARD_EXCLUDE.items() if re.search(rx, full)]
    foreign = sorted(set(m.group(0).strip() for m in re.finditer(_FOREIGN_BACKEND, full)))
    req_years = _extract_required_years(description)

    reject = []
    if re.search(_SENIOR_TITLE, t_low):
        reject.append("Başlık Senior/Kıdemli/Lead/Yönetici seviyesinde")
    if re.search(_JUNIOR_INTERN_TITLE, t_low):
        reject.append("Staj/Intern ilanı")
    if excluded:
        reject.append("CV dışı ekosistem: " + ", ".join(excluded))
    if req_years is not None and req_years >= 5:
        reject.append(f"{req_years}+ yıl deneyim şartı (aday ~3 yıl)")
    if foreign and not core:
        reject.append("Ana backend dili CV dışı (" + ", ".join(foreign) + ") ve CV'deki backend dillerinden hiçbiri yok")
    if not core and not front:
        reject.append("İlan metninde CV teknolojilerinden hiçbiri geçmiyor")

    desc_ok = len((description or "").strip()) >= 400
    if reject:
        verdict = "UYGUN DEĞİL"
    elif not desc_ok:
        verdict = "BELİRSİZ"
    else:
        verdict = "UYGUN"

    # Rule-based max score the LLM is allowed to give
    rule_score = 40 + min(len(core), 3) * 12 + min(len(front), 2) * 5 + min(len(extra), 3) * 2
    if req_years is not None and req_years <= 4:
        rule_score += 5
    rule_score = min(rule_score, 95)
    if verdict != "UYGUN":
        rule_score = min(rule_score, 60)

    return {
        "verdict": verdict,
        "core": core,
        "front": front,
        "extra": extra,
        "foreign": foreign,
        "excluded": excluded,
        "req_years": req_years,
        "reject_reasons": reject,
        "desc_len": len((description or "").strip()),
        "rule_score": rule_score,
    }


def _format_fit_analysis(a: Dict[str, Any]) -> str:
    years = f"{a['req_years']} yıl" if a.get("req_years") is not None else "belirtilmemiş"
    lines = [
        "=== OTOMATİK CV UYUM ÖN ANALİZİ (KOD TARAFINDAN, İLAN METNİNİN TAMAMI OKUNARAK) ===",
        f"- Sonuç: {a['verdict']} | Kural puanı tavanı: {a['rule_score']}",
        f"- Eşleşen CV backend/dil: {', '.join(a['core']) or 'YOK'}",
        f"- Eşleşen CV frontend: {', '.join(a['front']) or 'YOK'}",
        f"- Diğer eşleşmeler: {', '.join(a['extra']) or 'YOK'}",
        f"- CV dışı teknolojiler: {', '.join(a['foreign'] + a['excluded']) or 'YOK'}",
        f"- İstenen deneyim: {years} | Okunan ilan metni: {a['desc_len']} karakter",
    ]
    if a["reject_reasons"]:
        lines.append("- ELEME NEDENLERİ: " + " ; ".join(a["reject_reasons"]))
    return "\n".join(lines)


def _append_job_to_markdown(platform: str, position: str, company: str, location: str, score: str, reason: str, url: str):
    try:
        if platform == "kariyer":
            target_dir = "/app/data/saved_jobs_kariyer"
            header_title = "# Kaydedilen Kariyer.net İş İlanları"
        else:
            target_dir = "/app/data/saved_jobs_linkedin"
            if not os.path.exists(target_dir) and os.path.exists("/app/data/saved_jobs"):
                target_dir = "/app/data/saved_jobs"
            header_title = "# Kaydedilen LinkedIn İş İlanları"

        os.makedirs(target_dir, exist_ok=True)
        md_path = os.path.join(target_dir, "saved_jobs.md")

        if not os.path.exists(md_path):
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"{header_title}\n\n")
                f.write("| Tarih | Pozisyon Adı | Şirket | Konum / Çalışma Modeli | Uyum Puanı (%) | Kısa Eşleşme Nedeni | Doğrudan İlan Linki |\n")
                f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        else:
            existing = open(md_path, encoding="utf-8").read()
            if (position in existing and company in existing):
                return

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        safe_pos = position.replace("|", "-")
        safe_comp = company.replace("|", "-")
        safe_loc = location.replace("|", "-")
        safe_reason = reason.replace("|", "-")
        row = f"| {date_str} | {safe_pos} | {safe_comp} | {safe_loc} | {score} | {safe_reason} | {url} |\n"
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(row)
    except Exception as e:
        logger.error(f"Failed writing saved_jobs.md ({platform}): {e}")


class CustomController(Controller):
    def __init__(self, exclude_actions: list[str] = [],
                 output_model: Optional[Type[BaseModel]] = None,
                 ask_assistant_callback: Optional[Union[Callable[[str, BrowserContext], Dict[str, Any]], Callable[
                     [str, BrowserContext], Awaitable[Dict[str, Any]]]]] = None,
                 ):
        super().__init__(exclude_actions=exclude_actions, output_model=output_model)
        self.ask_assistant_callback = ask_assistant_callback
        self.mcp_client = None
        self.mcp_server_config = None
        self.agent_ref = None
        self.webui_manager = None
        self.waiting_for_captcha_confirmation = False
        self.operator_confirmed_gate = False
        self.reset_session()
        self._register_custom_actions()
        self._prune_default_actions()

    def _parse_chain_order(self, chain_mode: Optional[str] = None, task_text: Optional[str] = None) -> list[str]:
        if task_text:
            low_task = task_text.split("---")[0].lower()
            if "önce kariyer" in low_task or "kariyer -> linkedin" in low_task or "kariyer ➔ linkedin" in low_task:
                return ["kariyer", "linkedin"]
            if "sadece kariyer" in low_task:
                return ["kariyer"]
            if "sadece linkedin" in low_task:
                return ["linkedin"]

        mode_str = (chain_mode or os.getenv("CHAIN_ORDER", "linkedin,kariyer")).strip().lower()
        if "kariyer.net ➔ linkedin" in mode_str or mode_str.startswith("kariyer,linkedin") or mode_str.startswith("kariyer->linkedin"):
            return ["kariyer", "linkedin"]
        if "sadece linkedin" in mode_str or mode_str == "linkedin":
            return ["linkedin"]
        if "sadece kariyer" in mode_str or mode_str == "kariyer":
            return ["kariyer"]
        return ["linkedin", "kariyer"]

    def reset_session(
        self,
        chain_mode: Optional[str] = None,
        jobs_per_platform: Optional[int] = None,
        task_text: Optional[str] = None,
        agent_ref: Any = None,
        webui_manager: Any = None,
    ):
        if agent_ref is not None:
            self.agent_ref = agent_ref
        if webui_manager is not None:
            self.webui_manager = webui_manager
        self.waiting_for_captcha_confirmation = False
        self.operator_confirmed_gate = False
        self.platforms_chain = self._parse_chain_order(chain_mode, task_text)
        self.current_platform_idx = 0
        try:
            self.jobs_per_platform = int(jobs_per_platform or os.getenv("JOBS_PER_PLATFORM", "20"))
        except Exception:
            self.jobs_per_platform = 20

        self.total_visited_count = 0
        self.platform_visited_count = 0
        self.visited_card_keys = set()
        self.current_page_num = 1
        self.current_query_idx = 0

        self.linkedin_queries = [
            "Full Stack Developer Türkiye",
            "Software Developer İzmir",
            "Backend Developer Türkiye",
            "Python Developer Türkiye",
            "React Node.js Developer Türkiye",
            "Laravel Developer Türkiye",
        ]
        self.kariyer_queries = [
            "Full Stack Developer",
            "Software Developer",
            "Backend Developer",
            "Python Developer",
            "React Node.js Developer",
            "Laravel Developer",
        ]

        # Kariyer.net page state
        self.kariyer_search_initialized = False
        self.kariyer_search_url = ""
        self.kariyer_next_page_url = ""
        self.kariyer_cards_cache = []
        self.kariyer_skipped_on_list = []

        self.inspected_by_platform = {"linkedin": [], "kariyer": []}
        self.saved_by_platform = {"linkedin": [], "kariyer": []}
        self.last_fit = None

        self.last_card_meta = {
            "platform": self.current_platform,
            "position": "",
            "company": "",
            "location": "",
            "work_mode": "",
            "url": "",
        }
        logger.info(
            f"Chain session initialized: order={self.platforms_chain}, jobs_per_platform={self.jobs_per_platform}"
        )

    @property
    def current_platform(self) -> str:
        if 0 <= self.current_platform_idx < len(self.platforms_chain):
            return self.platforms_chain[self.current_platform_idx]
        return self.platforms_chain[-1]

    @property
    def current_platform_label(self) -> str:
        return "Kariyer.net" if self.current_platform == "kariyer" else "LinkedIn"

    def _switch_to_next_platform_state(self) -> bool:
        if self.current_platform_idx + 1 < len(self.platforms_chain):
            self.current_platform_idx += 1
            self.platform_visited_count = 0
            self.current_page_num = 1
            self.current_query_idx = 0
            self.kariyer_search_initialized = False
            self.kariyer_cards_cache = []
            self.kariyer_search_url = ""
            self.kariyer_next_page_url = ""
            return True
        return False

    def _build_final_summary(self, extra_note: str = "") -> str:
        li_saved = ", ".join(self.saved_by_platform["linkedin"]) or "Yok"
        ka_saved = ", ".join(self.saved_by_platform["kariyer"]) or "Yok"
        li_count = len(self.inspected_by_platform["linkedin"])
        ka_count = len(self.inspected_by_platform["kariyer"])
        chain_str = " ➔ ".join("Kariyer.net" if p == "kariyer" else "LinkedIn" for p in self.platforms_chain)
        prefix = f"{extra_note}\n" if extra_note else ""
        return (
            f"{prefix}HEDEF TAMAMLANDI! Tüm Zincir ({chain_str}) tamamlandı.\n"
            f"- LinkedIn: {li_count} ilan incelendi | Kaydedilen ({len(self.saved_by_platform['linkedin'])}): {li_saved}\n"
            f"- Kariyer.net: {ka_count} ilan incelendi (+ {len(self.kariyer_skipped_on_list)} alakasız/sponsorlu ilan listeden hızlı elendi) | Kaydedilen ({len(self.saved_by_platform['kariyer'])}): {ka_saved}\n"
            f"Şimdi başka hiçbir aksiyon çağırmadan doğrudan 'done' aksiyonunu çağır ve her iki platformun Türkçe özet raporunu sun!"
        )

    def _prune_default_actions(self):
        allowed = {
            "done",
            "read_job_by_order",
            "save_job",
            "next_jobs_page",
            "search_jobs",
            "switch_to_next_platform",
            "wait",
        }
        current_keys = list(self.registry.registry.actions.keys())
        for k in current_keys:
            if k not in allowed and not k.startswith("mcp."):
                del self.registry.registry.actions[k]

    # ==========================================================================
    # LINKEDIN ENGINE
    # ==========================================================================
    async def _linkedin_advance_page_or_query(self, page) -> str:
        has_next = await page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]'));
            if (cards.length > 0) {
                let scroller = cards[0].parentElement;
                while (scroller) {
                    const s = window.getComputedStyle(scroller);
                    if (scroller.scrollHeight > scroller.clientHeight + 20 && ['auto', 'scroll'].includes(s.overflowY)) {
                        scroller.scrollTop = scroller.scrollHeight;
                        break;
                    }
                    scroller = scroller.parentElement;
                }
            }
            const nextBtn = document.querySelector('button[data-testid="pagination-controls-next-button-visible"]');
            if (nextBtn && !nextBtn.disabled) {
                nextBtn.style.outline = '3px solid #057642';
                nextBtn.scrollIntoView({block: 'center'});
                return true;
            }
            return false;
        }""")
        await asyncio.sleep(1.0)
        if has_next:
            try:
                next_btn = page.locator('button[data-testid="pagination-controls-next-button-visible"]').first
                await next_btn.click(timeout=5000)
                self.current_page_num += 1
                await asyncio.sleep(3.5)
                await page.evaluate("""() => {
                    const cards = Array.from(document.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]'));
                    if (cards.length > 0) {
                        let scroller = cards[0].parentElement;
                        while (scroller) {
                            const s = window.getComputedStyle(scroller);
                            if (scroller.scrollHeight > scroller.clientHeight + 20 && ['auto', 'scroll'].includes(s.overflowY)) {
                                scroller.scrollTop = 0;
                                break;
                            }
                            scroller = scroller.parentElement;
                        }
                    }
                }""")
                return f"LinkedIn Sayfa {self.current_page_num}'e geçildi"
            except Exception as e:
                logger.warning(f"LinkedIn next page click failed: {e}")

        self.current_query_idx = (self.current_query_idx + 1) % len(self.linkedin_queries)
        self.current_page_num = 1
        next_q = self.linkedin_queries[self.current_query_idx]
        q_enc = urllib.parse.quote(next_q)
        await page.goto(f"https://www.linkedin.com/jobs/search-results/?keywords={q_enc}", wait_until="domcontentloaded")
        await asyncio.sleep(3.5)
        return f"LinkedIn yeni sorguya geçildi: '{next_q}' (Sayfa 1)"

    async def _expand_linkedin_description(self, page) -> Dict[str, Any]:
        """Finds the job description box in the right pane, clicks its 'daha fazla / see more' button, and verifies expansion."""
        try:
            prep = await page.evaluate("""() => {
                document.querySelectorAll('[data-ai-job-desc]').forEach(e => e.removeAttribute('data-ai-job-desc'));
                document.querySelectorAll('[data-ai-expand-btn]').forEach(e => e.removeAttribute('data-ai-expand-btn'));

                const boxes = Array.from(document.querySelectorAll('[data-testid="expandable-text-box"]'))
                    .filter(b => b.getBoundingClientRect().left > 420 && (b.innerText || '').trim().length > 20);
                if (!boxes.length) return {found: false};

                // Pick the main job description box (usually the first/largest in right pane)
                let targetBox = boxes[0];
                for (const b of boxes) {
                    const parentTxt = (b.parentElement ? b.parentElement.innerText : '') || '';
                    if (parentTxt.includes('İş ilanı hakkında') || parentTxt.includes('About the job')) {
                        targetBox = b;
                        break;
                    }
                }
                targetBox.setAttribute('data-ai-job-desc', '1');

                // Scroll right pane slightly so description & expand button are in view
                let scroller = targetBox.parentElement;
                while (scroller) {
                    const s = window.getComputedStyle(scroller);
                    if (scroller.scrollHeight > scroller.clientHeight + 40 && ['auto', 'scroll'].includes(s.overflowY)) {
                        scroller.scrollTop = Math.max(0, scroller.scrollTop + 260);
                        break;
                    }
                    scroller = scroller.parentElement;
                }

                const btn = targetBox.querySelector('button[data-testid="expandable-text-button"]')
                    || (targetBox.parentElement && targetBox.parentElement.querySelector('button[data-testid="expandable-text-button"]'));
                const hBefore = Math.round(targetBox.getBoundingClientRect().height);
                const lenBefore = (targetBox.innerText || '').trim().length;
                if (btn) {
                    btn.setAttribute('data-ai-expand-btn', '1');
                    return {found: true, hasBtn: true, hBefore, lenBefore};
                }
                return {found: true, hasBtn: false, hBefore, lenBefore};
            }""")
            if not prep or not prep.get("found"):
                return {"expanded": False, "reason": "no_box"}

            if prep.get("hasBtn"):
                for _ in range(2):
                    btn_loc = page.locator('[data-ai-expand-btn="1"]').first
                    if await btn_loc.count() > 0:
                        try:
                            await btn_loc.scroll_into_view_if_needed(timeout=2500)
                            await btn_loc.click(timeout=3000)
                        except Exception:
                            await page.evaluate("() => { const b = document.querySelector('[data-ai-expand-btn=\"1\"]'); if (b) b.click(); }")
                        await asyncio.sleep(0.8)
                    still_has = await page.evaluate("""() => {
                        const box = document.querySelector('[data-ai-job-desc="1"]');
                        if (!box) return false;
                        const b = box.querySelector('button[data-testid="expandable-text-button"]');
                        return Boolean(b && b.offsetParent !== null);
                    }""")
                    if not still_has:
                        break

            after = await page.evaluate("""() => {
                const box = document.querySelector('[data-ai-job-desc="1"]');
                if (!box) return {hAfter: 0, lenAfter: 0, hasMoreBtn: false};
                const b = box.querySelector('button[data-testid="expandable-text-button"]');
                return {
                    hAfter: Math.round(box.getBoundingClientRect().height),
                    lenAfter: (box.innerText || '').trim().length,
                    hasMoreBtn: Boolean(b && b.offsetParent !== null)
                };
            }""")
            expanded = (not prep.get("hasBtn")) or (not after.get("hasMoreBtn")) or (after.get("hAfter", 0) > prep.get("hBefore", 0) + 20)
            return {"expanded": expanded, "lenAfter": after.get("lenAfter", 0)}
        except Exception as e:
            logger.warning(f"LinkedIn description expand warning: {e}")
            return {"expanded": False, "reason": str(e)}

    async def _read_linkedin_job(self, page, transition_prefix: str = "") -> ActionResult:
        if "linkedin.com/jobs/search" not in page.url:
            init_q = self.linkedin_queries[self.current_query_idx]
            await page.goto(
                f"https://www.linkedin.com/jobs/search-results/?keywords={urllib.parse.quote(init_q)}",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(3.5)

        page_transition_note = transition_prefix

        async def find_unvisited_card():
            return await page.evaluate("""(visitedKeys) => {
                const vSet = new Set(visitedKeys);
                document.querySelectorAll('button[data-view-name="dismiss-job"]').forEach(b => b.style.display = 'none');
                const cards = Array.from(document.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]'));
                for (let i = 0; i < cards.length; i++) {
                    const key = 'li_' + (cards[i].getAttribute('componentkey') || `idx-${i}`);
                    if (!vSet.has(key)) {
                        return {found: true, index: i, key: key, total: cards.length};
                    }
                }
                return {found: false, index: -1, key: '', total: cards.length};
            }""", list(self.visited_card_keys))

        cand = await find_unvisited_card()

        if not cand["found"]:
            for _ in range(3):
                scrolled = await page.evaluate("""() => {
                    const cards = Array.from(document.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]'));
                    if (!cards.length) return false;
                    let scroller = cards[0].parentElement;
                    while (scroller) {
                        const s = window.getComputedStyle(scroller);
                        if (scroller.scrollHeight > scroller.clientHeight + 20 && ['auto', 'scroll'].includes(s.overflowY)) {
                            const prev = scroller.scrollTop;
                            scroller.scrollBy({top: 650, behavior: 'smooth'});
                            return scroller.scrollTop > prev;
                        }
                        scroller = scroller.parentElement;
                    }
                    return false;
                }""")
                await asyncio.sleep(1.2)
                cand = await find_unvisited_card()
                if cand["found"] or not scrolled:
                    break

        if not cand["found"]:
            transition = await self._linkedin_advance_page_or_query(page)
            page_transition_note += f"[OTOMATİK SAYFA GEÇİŞİ: {transition}] "
            cand = await find_unvisited_card()

        if not cand["found"] or cand["total"] == 0:
            if self._switch_to_next_platform_state():
                return await self._read_kariyer_job(
                    page,
                    transition_prefix="[🔗 ZİNCİR GEÇİŞİ: LinkedIn'de incelenecek ilan kalmadı, otomatik olarak Kariyer.net etabına geçildi!] ",
                )
            return ActionResult(
                extracted_content=self._build_final_summary("LinkedIn sayfasında incelenecek yeni iş ilanı bulunamadı."),
                include_in_memory=False,
            )

        target_idx = cand["index"]
        target_key = cand["key"]
        total_on_page = cand["total"]

        self.visited_card_keys.add(target_key)
        self.platform_visited_count += 1
        self.total_visited_count += 1

        current_q = self.linkedin_queries[self.current_query_idx]
        chain_pos = f"{self.current_platform_idx + 1}/{len(self.platforms_chain)}"
        card_meta = await page.evaluate("""(args) => {
            const {targetIdx, visitedCount, maxJobs, pageNum, totalOnPage, savedCount, queryStr, chainPos} = args;
            const cards = Array.from(document.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]'));
            cards.forEach((c, idx) => {
                if (idx < targetIdx) {
                    c.style.outline = '1px solid #999';
                    c.style.opacity = '0.82';
                    c.style.backgroundColor = '';
                } else if (idx === targetIdx) {
                    c.style.outline = '3px solid #0a66c2';
                    c.style.opacity = '1';
                    c.style.backgroundColor = 'rgba(10, 102, 194, 0.10)';
                } else {
                    c.style.outline = '';
                    c.style.opacity = '1';
                    c.style.backgroundColor = '';
                }
            });
            const card = cards[targetIdx];
            let rawLines = [];
            if (card) {
                rawLines = (card.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                let leftScroller = card.parentElement;
                while (leftScroller) {
                    const s = window.getComputedStyle(leftScroller);
                    if (leftScroller.scrollHeight > leftScroller.clientHeight + 20 && ['auto', 'scroll'].includes(s.overflowY)) {
                        const cRect = card.getBoundingClientRect();
                        const sRect = leftScroller.getBoundingClientRect();
                        const relTop = cRect.top - sRect.top + leftScroller.scrollTop;
                        leftScroller.scrollTop = Math.max(0, relTop - 65);
                        break;
                    }
                    leftScroller = leftScroller.parentElement;
                }
            }
            let banner = document.getElementById('ai-job-scout-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'ai-job-scout-banner';
                document.body.appendChild(banner);
            }
            banner.style.cssText = 'position:fixed;bottom:12px;left:12px;z-index:999999;background:#0a66c2;color:#fff;padding:8px 14px;border-radius:8px;font-family:sans-serif;font-size:13px;font-weight:bold;box-shadow:0 4px 12px rgba(0,0,0,0.3);pointer-events:none;';
            banner.textContent = `🤖 AI Job Scout [Zincir ${chainPos}: LinkedIn] | Sorgu: ${queryStr} | Sayfa: ${pageNum} | İlan: #${visitedCount}/${maxJobs} (Kart: ${targetIdx+1}/${totalOnPage}) | Kaydedilen: ${savedCount}`;
            return {rawLines};
        }""", {
            "targetIdx": target_idx,
            "visitedCount": self.platform_visited_count,
            "maxJobs": self.jobs_per_platform,
            "pageNum": self.current_page_num,
            "totalOnPage": total_on_page,
            "savedCount": len(self.saved_by_platform["linkedin"]),
            "queryStr": current_q,
            "chainPos": chain_pos,
        })

        raw_lines = (card_meta or {}).get("rawLines") or []
        parsed_pos = _clean_text_field(raw_lines[1] if len(raw_lines) > 1 else (raw_lines[0] if raw_lines else ""))
        parsed_pos = re.sub(r'\s*\(Doğrulanmış iş ilanı\)\s*', '', parsed_pos, flags=re.IGNORECASE)
        parsed_pos = re.sub(r'^Seçildi,\s*', '', parsed_pos, flags=re.IGNORECASE).strip()
        parsed_comp = _clean_text_field(raw_lines[2] if len(raw_lines) > 2 else "")
        parsed_loc = _clean_text_field(raw_lines[3] if len(raw_lines) > 3 else "")

        cards = page.locator('div[role="button"][componentkey^="job-card-component-ref-"]')
        card_title = ""
        try:
            card_title = (await cards.nth(target_idx).inner_text()).replace("\n", " | ").strip()[:110]
            await cards.nth(target_idx).click(timeout=5000)
        except Exception as e:
            return ActionResult(error=f"LinkedIn ilan kartına tıklanamadı: {e}")

        await asyncio.sleep(2.2)

        expand_info = await self._expand_linkedin_description(page)
        details_raw = await page.evaluate("""async () => {
            const btns = Array.from(document.querySelectorAll('button'));
            const saveBtn = btns.find(b => {
                const t = (b.innerText || '').trim().toLowerCase();
                const a = (b.getAttribute('aria-label') || '').toLowerCase();
                const dv = (b.getAttribute('data-view-name') || '').toLowerCase();
                const r = b.getBoundingClientRect();
                return (r.left > 450 && r.width > 20 && r.height > 20 && r.top > 0 && (
                    t === 'kaydet' || t === 'kaydedildi' || t === 'save' || t === 'saved' ||
                    a.includes('ilanını kaydet') || a.includes('kaydedildi') || a.includes('save')
                )) || dv === 'job-save-button' || b.classList.contains('jobs-save-button');
            });
            let saveState = 'Kaydet (Kaydedilmemiş)';
            if (saveBtn) {
                const bText = (saveBtn.innerText || '').trim();
                const bAria = (saveBtn.getAttribute('aria-label') || '').toLowerCase();
                if (bText.toLowerCase().includes('kaydedildi') || bText.toLowerCase().includes('saved') || bAria.includes('geri al')) {
                    saveState = 'Kaydedildi';
                }
            }

            // Right panel header text (title / seniority / applicants etc.)
            let headerText = '';
            const h = Array.from(document.querySelectorAll('h1, h2')).find(e => e.getBoundingClientRect().left > 450 && (e.innerText || '').trim().length > 3);
            if (h) headerText = (h.innerText || '').trim();

            const box = document.querySelector('[data-ai-job-desc="1"]');
            let fullDesc = box ? (box.innerText || box.textContent || '').trim() : '';
            fullDesc = fullDesc.replace(/…\\s*daha fazla\\s*$/i, '').replace(/…\\s*see more\\s*$/i, '').trim();

            if (fullDesc.length < 200) {
                const bodyText = document.body.innerText || '';
                let idx = bodyText.indexOf('İş ilanı hakkında');
                if (idx === -1) idx = bodyText.indexOf('About the job');
                if (idx !== -1) {
                    let endIdx = bodyText.indexOf('Premium iş arama', idx);
                    const compIdx = bodyText.indexOf('Şirket hakkında', idx);
                    if (endIdx === -1 || (compIdx !== -1 && compIdx < endIdx)) endIdx = compIdx;
                    const rawDesc = endIdx !== -1 ? bodyText.slice(idx, endIdx) : bodyText.slice(idx, idx + 15000);
                    fullDesc = rawDesc.replace(/…\\s*daha fazla/gi, '').trim();
                }
            }
            return {saveState, headerText, desc: fullDesc.slice(0, 15000)};
        }""")
        save_state = (details_raw or {}).get("saveState") or "Kaydet (Kaydedilmemiş)"
        full_desc = (details_raw or {}).get("desc") or ""
        header_text = (details_raw or {}).get("headerText") or ""

        fit_title = parsed_pos or header_text or card_title
        fit = _analyze_job_fit(fit_title, full_desc)
        if not expand_info.get("expanded"):
            fit["reject_reasons"].append("İlan açıklaması tam açılamadı ('daha fazla' genişletilemedi)") if fit["verdict"] == "UYGUN" and len(full_desc) < 800 else None
            if fit["verdict"] == "UYGUN" and len(full_desc) < 800:
                fit["verdict"] = "BELİRSİZ"
        self.last_fit = {"platform": "linkedin", "url": page.url, "title": fit_title, "fit": fit, "saved": False}

        desc_for_llm = full_desc[:6000]
        details = (
            f"[KAYIT DURUMU: {save_state}]\n"
            f"[AÇIKLAMA GENİŞLETME: {'Tam açıldı' if expand_info.get('expanded') else 'Açılamadı/kısmi'} | {len(full_desc)} karakter okundu]\n"
            f"---\nİş ilanı hakkında (TAM METİN)\n\n{desc_for_llm}"
        )

        self.last_card_meta = {
            "platform": "linkedin",
            "position": parsed_pos,
            "company": parsed_comp,
            "location": parsed_loc,
            "work_mode": "",
            "url": page.url,
        }

        short_label = (parsed_pos or card_title.split("|")[0].strip())[:45]
        self.inspected_by_platform["linkedin"].append(f"#{self.platform_visited_count}: {short_label}")
        recent_history = ", ".join(self.inspected_by_platform["linkedin"][-8:])
        saved_str = ", ".join(self.saved_by_platform["linkedin"]) or "Henüz yok"
        next_num = self.total_visited_count + 1

        fit_summary = _format_fit_analysis(fit)
        return ActionResult(
            extracted_content=(
                f"{page_transition_note}=== [ZİNCİR {chain_pos}: LINKEDIN] İLAN #{self.platform_visited_count} / {self.jobs_per_platform} "
                f"(Sayfa {self.current_page_num}, Kart {target_idx + 1}/{total_on_page}) ===\n"
                f"İLAN BİLGİSİ: Pozisyon: '{parsed_pos}' | Şirket: '{parsed_comp}' | Konum: '{parsed_loc}'\n"
                f"İLERLEME (LinkedIn): {self.platform_visited_count}/{self.jobs_per_platform} | Kaydedilenler ({len(self.saved_by_platform['linkedin'])}): {saved_str}\n"
                f"SON İNCELENENLER: {recent_history}\n"
                f"URL: {page.url}\n"
                f"{fit_summary}\n\n"
                f"{details}\n\n"
                f"KARAR TALİMATI:\n"
                f"- Yukarıdaki ÖN ANALİZ sonucu 'UYGUN' ise ve ilan metni de CV ile gerçekten örtüşüyorsa (>= 75 puan, KAYIT DURUMU 'Kaydedildi' değilse) ŞİMDİ save_job(position='{parsed_pos}', company='{parsed_comp}', location='{parsed_loc}', ...) aksiyonunu çağır.\n"
                f"- Sonuç 'UYGUN DEĞİL' veya 'BELİRSİZ' ise (ya da metinde uyumsuzluk görüyorsan) ASLA save_job ÇAĞIRMA! Doğrudan read_job_by_order(job_order={next_num}) aksiyonunu çağır!"
            ),
            include_in_memory=False,
        )

    async def _save_linkedin_job_impl(self, page, clean_pos: str, clean_comp: str, full_location: str, score: int, reason: str) -> str:
        url = (page.url if page else "") or self.last_card_meta.get("url") or "https://www.linkedin.com/jobs/"
        linkedin_status = "LinkedIn Kaydet butonu kontrol edildi"
        if page:
            try:
                click_res = await page.evaluate("""async () => {
                    const allDivs = Array.from(document.querySelectorAll('div'));
                    for (const d of allDivs) {
                        const s = window.getComputedStyle(d);
                        if (d.scrollHeight > d.clientHeight + 50 && ['auto', 'scroll'].includes(s.overflowY)) {
                            const hasCards = d.querySelectorAll('div[role="button"][componentkey^="job-card-component-ref-"]').length;
                            if (hasCards === 0 && d.getBoundingClientRect().left > 600) {
                                d.scrollTop = 0;
                                break;
                            }
                        }
                    }
                    await new Promise(r => setTimeout(r, 350));

                    const findSaveBtn = () => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        return btns.find(b => {
                            const t = (b.innerText || '').trim().toLowerCase();
                            const a = (b.getAttribute('aria-label') || '').toLowerCase();
                            const dv = (b.getAttribute('data-view-name') || '').toLowerCase();
                            const r = b.getBoundingClientRect();
                            return (r.left > 450 && r.width > 20 && r.height > 20 && r.top > 0 && (
                                t === 'kaydet' || t === 'kaydedildi' || t === 'save' || t === 'saved' ||
                                a.includes('ilanını kaydet') || a.includes('kaydedildi') || a.includes('save')
                            )) || dv === 'job-save-button' || b.classList.contains('jobs-save-button');
                        });
                    };

                    const btn = findSaveBtn();
                    if (!btn) return {status: 'not_found'};
                    const tBefore = (btn.innerText || '').trim();
                    const aBefore = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (tBefore.toLowerCase().includes('kaydedildi') || tBefore.toLowerCase().includes('saved') || aBefore.includes('geri al')) {
                        return {status: 'already_saved'};
                    }
                    btn.style.outline = '3px solid #057642';
                    btn.click();
                    await new Promise(r => setTimeout(r, 1200));
                    return {status: 'clicked'};
                }""")
                st = (click_res or {}).get("status")
                if st == "already_saved":
                    linkedin_status = "İlan LinkedIn'de zaten 'Kaydedildi' durumunda"
                elif st == "clicked":
                    linkedin_status = "LinkedIn 'Kaydet' butonuna tıklandı ve Kaydedilen İşler'e eklendi"
                else:
                    linkedin_status = "LinkedIn 'Kaydet' butonu bulunamadı"
            except Exception as e:
                linkedin_status = f"LinkedIn Kaydet tıklama hatası: {e}"

        _append_job_to_markdown("linkedin", clean_pos, clean_comp, full_location, f"{score}%", reason, url)
        self.saved_by_platform["linkedin"].append(f"{clean_pos} @ {clean_comp} ({score}%)")
        return linkedin_status

    # ==========================================================================
    # KARIYER.NET ENGINE (WITH PERIMETERX / "BASILI TUT" PAUSE & SMART FILTER)
    # ==========================================================================
    def _build_kariyer_search_url(self, query: str, page_num: int = 1) -> str:
        q_enc = urllib.parse.quote(query.strip())
        base = f"https://www.kariyer.net/is-ilanlari?kw={q_enc}"
        if page_num > 1:
            base += f"&cp={page_num}"
        return base

    def confirm_security_gate(self):
        """Called by Web UI when operator clicks Resume / Doğrulamayı Yaptım."""
        self.operator_confirmed_gate = True
        if self.agent_ref is not None and hasattr(self.agent_ref, "state"):
            self.agent_ref.state.paused = False

    def _notify_operator_in_chat(self, message: str):
        """Immediately appends an operator notification to the Web UI chat history."""
        try:
            if self.webui_manager is not None and hasattr(self.webui_manager, "bu_chat_history"):
                self.webui_manager.bu_chat_history.append(
                    {"role": "assistant", "content": message}
                )
        except Exception as e:
            logger.warning(f"Could not append operator notification to chat: {e}")

    async def _is_kariyer_security_gate_open(self, page) -> bool:
        """
        Detects Kariyer.net's PerimeterX ('Basılı Tut' / '#px-captcha-modal' / '#px-captcha')
        or Cloudflare challenge, including when rendered as a full-screen iframe overlay on top of /is-ilanlari.
        NEVER clicks or interacts with the captcha.
        """
        try:
            return bool(await page.evaluate("""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') === 0) {
                        return false;
                    }
                    const r = el.getBoundingClientRect();
                    return r.width > 20 && r.height > 15;
                };

                // 1. Check overlay/modal captcha widgets FIRST (even if job cards exist in background DOM)
                const modalEl = document.getElementById('px-captcha-modal');
                if (isVisible(modalEl)) {
                    return true;
                }

                const widgetSelectors = [
                    '#px-captcha',
                    '[id*="px-captcha"]',
                    '[class*="px-captcha"]',
                    '.cf-turnstile',
                    'iframe[src*="challenges.cloudflare.com"]',
                    'iframe[src*="px-cloud"]',
                    'iframe[src*="perimeterx"]'
                ];
                for (const sel of widgetSelectors) {
                    const els = Array.from(document.querySelectorAll(sel));
                    if (els.some(isVisible)) {
                        return true;
                    }
                }

                // Also check same-origin iframes for PerimeterX 'Basılı Tut' text
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const f of iframes) {
                    if (!isVisible(f)) continue;
                    try {
                        const doc = f.contentDocument;
                        const win = f.contentWindow;
                        if (win && win._pxModal) return true;
                        if (doc && doc.body) {
                            const fText = (doc.body.innerText || '').toLocaleLowerCase('tr-TR');
                            if (
                                fText.includes('basılı tut') ||
                                fText.includes('güvenlik doğrulaması') ||
                                fText.includes('doğrulamayı') ||
                                fText.includes('press & hold') ||
                                fText.includes('press and hold') ||
                                doc.getElementById('px-captcha')
                            ) {
                                return true;
                            }
                        }
                    } catch (e) {}
                }

                // 2. Check full-page challenge when job content is not present
                const jobLinks = document.querySelectorAll('a[href*="/is-ilani/"]').length;
                const body = (document.body ? document.body.innerText : '').trim();
                const lowBody = body.toLocaleLowerCase('tr-TR');
                if (
                    jobLinks > 2 ||
                    lowBody.includes('genel nitelikler') ||
                    lowBody.includes('iş tanımı') ||
                    lowBody.includes('aday kriterleri')
                ) {
                    return false;
                }

                const title = (document.title || '').toLocaleLowerCase('tr-TR');
                return (
                    title.includes('just a moment') ||
                    title.includes('bir dakika') ||
                    title.includes('attention required') ||
                    title.includes('erişim engellendi') ||
                    lowBody.includes('kısa bir güvenlik doğrulaması') ||
                    lowBody.includes('aşağıdaki butona basılı tutarak') ||
                    (lowBody.includes('basılı tut') && lowBody.length < 3000) ||
                    (lowBody.includes('press & hold') && lowBody.length < 3000) ||
                    (lowBody.includes('press and hold') && lowBody.length < 3000) ||
                    (lowBody.includes('insan olduğunuzu doğrulayın') && lowBody.length < 3000) ||
                    (lowBody.includes('insan olduğunu') && lowBody.length < 3000) ||
                    (lowBody.includes('verify you are human') && lowBody.length < 3000) ||
                    (lowBody.includes('olağandışı trafik') && lowBody.length < 3000)
                );
            }"""))
        except Exception:
            return False

    async def _wait_for_kariyer_security_gate(self, page, stage_desc: str = "Kariyer.net işlemi") -> bool:
        """
        If Kariyer.net's 'Basılı Tut' captcha is on screen:
        1. Immediately pauses the bot (never touches or clicks the captcha).
        2. Notifies the operator in Web UI chat + top banner on noVNC.
        3. Waits until the operator completes the 'Basılı Tut' challenge AND confirms
           (either via the green button in noVNC or via Resume / Doğrulamayı Yaptım button in Web UI).
        4. Returns True if a security gate was encountered and resolved, False if no gate was present.
        """
        if not await self._is_kariyer_security_gate_open(page):
            return False

        logger.warning(f"Kariyer.net 'Basılı Tut' security gate detected during: {stage_desc}. Pausing for operator.")
        self.waiting_for_captcha_confirmation = True
        self.operator_confirmed_gate = False
        if self.agent_ref is not None and hasattr(self.agent_ref, "state"):
            self.agent_ref.state.paused = True

        try:
            await page.evaluate("() => { window.__ai_captcha_operator_confirmed = false; }")
        except Exception:
            pass

        self._notify_operator_in_chat(
            "🚨 **KARİYER.NET 'BASILI TUT' (CAPTCHA) EKRANI ALGILANDI — BOT DURDURULDU!**\n"
            f"- **Aşama:** `{stage_desc}`\n"
            "- **Durum:** Bot güvenlik doğrulamasını görünce tüm işlemlerini duraklattı ve sizin onayınızı bekliyor.\n\n"
            "👉 **Lütfen şu adımları uygulayın:**\n"
            "1. Canlı Tarayıcı (**noVNC - `http://localhost:6080`**) ekranına geçip ortadaki **'Basılı Tut'** butonuna basılı tutarak doğrulamayı tamamlayın.\n"
            "2. Doğrulama geçildikten sonra botun kaldığı yerden devam etmesi için **onay verin**:\n"
            "   - Doğrudan noVNC ekranının en üstündeki yeşil **'✅ Basılı Tut Doğrulamasını Yaptım — Devam Et'** butonuna tıklayabilir,\n"
            "   - Veya bu kontrol panelindeki (`http://localhost:7788`) **'✅ Doğrulamayı Yaptım, Devam Et'** / **'▶️ Resume (Devam Et)'** butonuna basabilirsiniz."
        )

        notified_gate_cleared = False

        while True:
            if self.agent_ref is not None and hasattr(self.agent_ref, "state") and self.agent_ref.state.stopped:
                self.waiting_for_captcha_confirmation = False
                return False

            gate_still_open = await self._is_kariyer_security_gate_open(page)

            # Render/update the top operator bar in noVNC (both on main document and inside modal iframe if accessible)
            novnc_confirmed = False
            try:
                novnc_confirmed = bool(await page.evaluate("""(gateStillOpen) => {
                    const ensureBar = (doc, win) => {
                        if (!doc || !doc.body) return;
                        let bar = doc.getElementById('ai-security-gate-alert');
                        if (!bar) {
                            bar = doc.createElement('div');
                            bar.id = 'ai-security-gate-alert';
                            doc.body.appendChild(bar);
                        } else if (doc.body.lastElementChild !== bar) {
                            doc.body.appendChild(bar);
                        }
                        const bg = gateStillOpen ? '#b31412' : '#0d652d';
                        bar.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483647;background:${bg};color:#fff;padding:10px 18px;font-family:sans-serif;font-size:14px;font-weight:bold;box-shadow:0 4px 16px rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:space-between;gap:14px;`;

                        const msgSpanId = 'ai-security-gate-msg';
                        let msgSpan = doc.getElementById(msgSpanId);
                        if (!msgSpan) {
                            msgSpan = doc.createElement('div');
                            msgSpan.id = msgSpanId;
                            bar.appendChild(msgSpan);
                        }
                        msgSpan.innerHTML = gateStillOpen
                            ? '🚨 BOT DURDURULDU: Lütfen aşağıdaki "Basılı Tut" doğrulamasını tamamlayın, ardından sağdaki onay butonuna basın 👉'
                            : '🎉 "Basılı Tut" doğrulaması geçildi! Botun işine devam etmesi için sağdaki onay butonuna tıklayın 👉';

                        const btnId = 'ai-security-gate-confirm-btn';
                        let btn = doc.getElementById(btnId);
                        if (!btn) {
                            btn = doc.createElement('button');
                            btn.id = btnId;
                            btn.type = 'button';
                            btn.onclick = (e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                window.__ai_captcha_operator_confirmed = true;
                                try {
                                    if (window.parent) window.parent.__ai_captcha_operator_confirmed = true;
                                } catch (err) {}
                                btn.textContent = '⏳ Onay Alındı, Kontrol Ediliyor...';
                            };
                            bar.appendChild(btn);
                        }
                        btn.style.cssText = 'background:#22c55e;color:#fff;border:2px solid #fff;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:bold;cursor:pointer;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
                        if (!window.__ai_captcha_operator_confirmed) {
                            btn.textContent = '✅ Basılı Tut Doğrulamasını Yaptım — Devam Et';
                        }
                    };

                    ensureBar(document, window);
                    const modal = document.getElementById('px-captcha-modal');
                    if (modal) {
                        try {
                            if (modal.contentDocument && modal.contentWindow) {
                                ensureBar(modal.contentDocument, modal.contentWindow);
                                if (modal.contentWindow.__ai_captcha_operator_confirmed) {
                                    window.__ai_captcha_operator_confirmed = true;
                                }
                            }
                        } catch (e) {}
                    }
                    return Boolean(window.__ai_captcha_operator_confirmed);
                }""", gate_still_open))
            except Exception:
                pass

            if not gate_still_open and not notified_gate_cleared:
                notified_gate_cleared = True
                self._notify_operator_in_chat(
                    "🎉 **Kariyer.net 'Basılı Tut' ekranının kapandığı algılandı!**\n"
                    "Botun kaldığı yerden işine devam etmesi için lütfen **'✅ Doğrulamayı Yaptım, Devam Et'** (veya noVNC üst barındaki yeşil onay) butonuna basın."
                )

            webui_confirmed = self.operator_confirmed_gate or (
                self.agent_ref is not None and hasattr(self.agent_ref, "state") and not self.agent_ref.state.paused
            )

            if novnc_confirmed or webui_confirmed:
                # Re-verify if captcha is still open on screen
                if await self._is_kariyer_security_gate_open(page):
                    await asyncio.sleep(1.2)
                if await self._is_kariyer_security_gate_open(page):
                    # Captcha is still on screen; keep paused and inform operator
                    self.operator_confirmed_gate = False
                    if self.agent_ref is not None and hasattr(self.agent_ref, "state"):
                        self.agent_ref.state.paused = True
                    try:
                        await page.evaluate("() => { window.__ai_captcha_operator_confirmed = false; }")
                    except Exception:
                        pass
                    self._notify_operator_in_chat(
                        "⚠️ **'Basılı Tut' doğrulama ekranı hâlâ açık görünüyor!**\n"
                        "Lütfen önce Canlı Tarayıcı (`http://localhost:6080`) ekranında ortadaki **'Basılı Tut'** butonuna basılı tutarak doğrulama penceresinin kapanmasını sağlayın, ardından tekrar onay verin."
                    )
                    await asyncio.sleep(0.6)
                    continue

                # Both captcha is cleared AND operator confirmed!
                try:
                    await page.evaluate("""() => {
                        window.__ai_captcha_operator_confirmed = false;
                        const bar = document.getElementById('ai-security-gate-alert');
                        if (bar) bar.remove();
                    }""")
                except Exception:
                    pass

                self.waiting_for_captcha_confirmation = False
                self.operator_confirmed_gate = False
                if self.agent_ref is not None and hasattr(self.agent_ref, "state"):
                    self.agent_ref.state.paused = False

                self._notify_operator_in_chat(
                    f"✅ **Operatör onayı alındı!** Kariyer.net doğrulaması tamamlandı (`{stage_desc}`), bot kaldığı yerden işine devam ediyor..."
                )
                logger.info(f"Operator confirmed Kariyer.net security gate resolution during: {stage_desc}. Resuming.")
                await asyncio.sleep(1.5)
                return True

            await asyncio.sleep(0.4)

    async def _dismiss_kariyer_cookie_banner(self, page):
        try:
            await page.evaluate("""() => {
                // Remove onboarding header-tour overlay that intercepts search box clicks
                document.querySelectorAll('[data-test="header-tour"], .kl-header-tour, .kl-header-tour-desktop').forEach(el => {
                    try { el.remove(); } catch(e) {}
                });
                const btns = Array.from(document.querySelectorAll('button, a'));
                for (const b of btns) {
                    const t = (b.innerText || '').trim().toLowerCase();
                    if (t === 'kabul et' || t === 'tümünü kabul et' || t === 'anladım' || t === 'tamam' || b.id === 'onetrust-accept-btn-handler') {
                        try { b.click(); } catch(e) {}
                    }
                }
            }""")
        except Exception:
            pass

    async def _execute_kariyer_keyword_search(self, page, query: str):
        """Performs keyword search on Kariyer.net and pauses for operator if 'Basılı Tut' appears."""
        await self._wait_for_kariyer_security_gate(page, f"Kariyer.net arama öncesi ({query})")
        await self._dismiss_kariyer_cookie_banner(page)

        target_url = self._build_kariyer_search_url(query, 1)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3.2)
        except Exception as e:
            logger.warning(f"Kariyer search navigation warning ({target_url}): {e}")

        gate_resolved = await self._wait_for_kariyer_security_gate(page, f"Kariyer.net '{query}' araması")
        if gate_resolved and "kw=" not in page.url:
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3.0)
                await self._wait_for_kariyer_security_gate(page, f"Kariyer.net '{query}' araması tekrar")
            except Exception as e:
                logger.warning(f"Kariyer search reload after gate warning: {e}")

        await self._dismiss_kariyer_cookie_banner(page)
        self.kariyer_search_initialized = True
        self.kariyer_search_url = page.url
        return None

    async def _collect_kariyer_cards(self, page) -> list[dict]:
        await self._wait_for_kariyer_security_gate(page, "Kariyer.net ilan listesi okuma")
        await self._dismiss_kariyer_cookie_banner(page)

        for _ in range(3):
            try:
                await page.evaluate("window.scrollBy({top: 700, behavior: 'smooth'});")
                await asyncio.sleep(0.9)
            except Exception:
                break

        try:
            await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'});")
            await asyncio.sleep(0.6)
        except Exception:
            pass

        await self._wait_for_kariyer_security_gate(page, "Kariyer.net ilan kartları toplama")

        extracted = await page.evaluate("""() => {
            const results = [];
            const seenKeys = new Set();

            // Only match real job detail anchors ending with a numeric job ID (-1234567)
            const anchors = Array.from(document.querySelectorAll(
                '.list-items-wrapper a[href*="/is-ilani/"], a.k-ad-card[href*="/is-ilani/"], div[data-test="ad-card"] a[href*="/is-ilani/"], a[href*="/is-ilani/"]'
            ));

            const ignoreLineRegex = /^(sponsorlu ilan|sponsorlu|yeni|bugün|dün|acil|hızlı başvuru|incelendi|tam zamanlı|yarı zamanlı|freelance|dönemsel|stajyer|bookmark_border|bookmark|more_vert|update|electric_bolt_fill|accessible|ort\\.\\s*\\d+\\s*günde\\s*dönüyor|ort\\.\\s*\\d+\\s*günde\\s*dönüş|\\d+\\s*(saat|dakika|gün))$/i;

            for (const a of anchors) {
                const rawHref = a.getAttribute('href') || '';
                if (!rawHref || !rawHref.includes('/is-ilani/')) continue;

                let fullUrl = '';
                try {
                    fullUrl = new URL(rawHref, 'https://www.kariyer.net').href;
                } catch (e) {
                    continue;
                }

                // Must have a real numeric job ID at the end of URL
                const idMatch = fullUrl.match(/-(\\d{5,})(?:[/?#]|$)/);
                if (!idMatch) continue;

                const key = 'ka_' + idMatch[1];
                if (seenKeys.has(key)) continue;

                const cardEl = a.closest('[data-test="ad-card"], .k-ad-card, .list-items') || a;
                const rawText = (cardEl.innerText || a.innerText || '').trim();
                if (rawText.length < 8) continue;

                const lowRaw = rawText.toLocaleLowerCase('tr-TR');
                const isSponsored = lowRaw.includes('sponsorlu');
                const cleanLines = rawText
                    .split('\\n')
                    .map(l => l.trim())
                    .filter(l => {
                        if (l.length <= 1) return false;
                        const lowL = l.toLocaleLowerCase('tr-TR');
                        return !ignoreLineRegex.test(lowL);
                    });

                if (cleanLines.length === 0) continue;

                const position = cleanLines[0] || '';
                const company = cleanLines[1] || '';
                let location = cleanLines[2] || '';
                let workMode = cleanLines[3] || '';

                if (location.includes('•')) {
                    const parts = location.split('•').map(s => s.trim());
                    location = parts[0] || location;
                    workMode = parts[1] || workMode;
                }

                seenKeys.add(key);
                results.push({
                    key: key,
                    url: fullUrl,
                    position: position,
                    company: company,
                    location: location,
                    work_mode: workMode,
                    sponsored: isSponsored,
                });
            }

            let nextPageHref = '';
            const nextSelectors = [
                'a[aria-label*="Next"]',
                'a[aria-label*="Sonraki"]',
                '.pagination a.next',
                'ul.pagination li.page-item:not(.disabled) a[rel="next"]'
            ];
            for (const sel of nextSelectors) {
                const el = document.querySelector(sel);
                if (el && el.getAttribute('href')) {
                    try {
                        nextPageHref = new URL(el.getAttribute('href'), window.location.href).href;
                        break;
                    } catch (e) {}
                }
            }

            return {cards: results, nextPageHref: nextPageHref};
        }""")

        raw_cards = (extracted or {}).get("cards") or []
        self.kariyer_next_page_url = (extracted or {}).get("nextPageHref") or ""

        # Pre-filter cards on the listing page so we never waste navigations on non-IT/excluded ads
        filtered_cards = []
        for c in raw_cards:
            skip_reason = _is_obviously_irrelevant_card_title(c.get("position", ""))
            if skip_reason:
                if c["key"] not in self.visited_card_keys:
                    self.visited_card_keys.add(c["key"])
                    self.kariyer_skipped_on_list.append(f"{c.get('position', '')} ({skip_reason})")
                continue
            filtered_cards.append(c)

        self.kariyer_cards_cache = filtered_cards
        return filtered_cards

    async def _kariyer_advance_page_or_query(self, page, force_next_query: bool = False) -> str:
        if not force_next_query:
            next_num = self.current_page_num + 1
            current_q = self.kariyer_queries[self.current_query_idx]
            next_page_url = self.kariyer_next_page_url
            if not next_page_url and next_num <= 4 and len(self.kariyer_cards_cache) >= 8:
                next_page_url = self._build_kariyer_search_url(current_q, next_num)

            if next_page_url:
                try:
                    await page.goto(next_page_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(3.0)
                    gate_resolved = await self._wait_for_kariyer_security_gate(
                        page, f"Kariyer.net Sayfa {next_num} geçişi"
                    )
                    if gate_resolved:
                        await page.goto(next_page_url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(3.0)
                    self.current_page_num = next_num
                    self.kariyer_search_url = page.url
                    cards = await self._collect_kariyer_cards(page)
                    unvisited = [c for c in cards if c["key"] not in self.visited_card_keys]
                    if unvisited:
                        return f"Kariyer.net Sayfa {self.current_page_num}'e geçildi ({len(unvisited)} uygun aday ilan)"
                except Exception as e:
                    logger.warning(f"Kariyer direct next page failed: {e}")

            if self.kariyer_search_url and next_num <= 4:
                try:
                    if "/is-ilani/" in page.url or page.url != self.kariyer_search_url:
                        await page.goto(self.kariyer_search_url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(2.8)
                    await self._wait_for_kariyer_security_gate(page, f"Kariyer.net Sayfa {next_num} hazırlığı")
                    clicked = await page.evaluate("""(targetPageNum) => {
                        const targetStr = String(targetPageNum);
                        const pagContainers = Array.from(document.querySelectorAll('.pagination, [class*="pagination"], nav[aria-label*="pagination"]'));
                        const candidates = pagContainers.length
                            ? pagContainers.flatMap(c => Array.from(c.querySelectorAll('a, button, li')))
                            : Array.from(document.querySelectorAll('ul.pagination a, ul.pagination button'));
                        for (const el of candidates) {
                            const txt = (el.innerText || '').trim();
                            if (txt === targetStr && !el.disabled && !el.classList.contains('disabled')) {
                                el.scrollIntoView({block: 'center'});
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""", next_num)
                    if clicked:
                        await asyncio.sleep(3.2)
                        await self._wait_for_kariyer_security_gate(page, f"Kariyer.net Sayfa {next_num} yüklemesi")
                        self.current_page_num = next_num
                        self.kariyer_search_url = page.url
                        cards = await self._collect_kariyer_cards(page)
                        unvisited = [c for c in cards if c["key"] not in self.visited_card_keys]
                        if unvisited:
                            return f"Kariyer.net Sayfa {self.current_page_num}'e geçildi ({len(unvisited)} uygun aday ilan)"
                except Exception as e:
                    logger.warning(f"Kariyer pagination click failed: {e}")

        self.current_query_idx = (self.current_query_idx + 1) % len(self.kariyer_queries)
        self.current_page_num = 1
        next_q = self.kariyer_queries[self.current_query_idx]
        await self._execute_kariyer_keyword_search(page, next_q)
        await self._collect_kariyer_cards(page)
        return f"Kariyer.net yeni sorguya geçildi: '{next_q}' (Sayfa 1)"

    async def _expand_kariyer_description(self, page) -> Dict[str, Any]:
        """Clicks the 'Daha Fazla Göster' button on a Kariyer.net job detail page and verifies expansion."""
        try:
            prep = await page.evaluate("""() => {
                document.querySelectorAll('[data-ai-kariyer-expand]').forEach(e => e.removeAttribute('data-ai-kariyer-expand'));

                const isExpandLabel = (txt) => {
                    const low = (txt || '').trim().toLocaleLowerCase('tr-TR');
                    return (
                        low === 'daha fazla göster' ||
                        low === 'daha fazla' ||
                        low === 'devamını gör' ||
                        low === 'show more' ||
                        low === 'see more'
                    );
                };

                const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], span, div'));
                const matches = [];
                for (const el of candidates) {
                    if (el.offsetParent === null) continue;
                    const txt = (el.innerText || '').trim();
                    if (!txt || txt.length > 30) continue;
                    if (!isExpandLabel(txt)) continue;
                    // Prefer buttons inside the main job detail area (left/center column, not sidebar)
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 20 || rect.height < 12) continue;
                    // Avoid matching outer wrapper divs that also contain other elements
                    if (el.children.length > 2) continue;
                    matches.push({el, top: rect.top});
                }

                if (!matches.length) return {found: false};
                matches.sort((a, b) => a.top - b.top);
                matches[0].el.setAttribute('data-ai-kariyer-expand', '1');
                return {found: true, count: matches.length};
            }""")

            if not prep or not prep.get("found"):
                return {"expanded": True, "clicked": False, "reason": "no_button"}

            clicked = False
            for _ in range(2):
                btn_loc = page.locator('[data-ai-kariyer-expand="1"]').first
                if await btn_loc.count() > 0:
                    try:
                        await btn_loc.scroll_into_view_if_needed(timeout=2500)
                        await asyncio.sleep(0.25)
                        await btn_loc.click(timeout=3000)
                        clicked = True
                    except Exception:
                        await page.evaluate("""() => {
                            const b = document.querySelector('[data-ai-kariyer-expand="1"]');
                            if (b) { b.scrollIntoView({block: 'center'}); b.click(); }
                        }""")
                        clicked = True
                    await asyncio.sleep(0.75)

                still_collapsed = await page.evaluate("""() => {
                    const b = document.querySelector('[data-ai-kariyer-expand="1"]');
                    if (!b || b.offsetParent === null) return false;
                    const low = (b.innerText || '').trim().toLocaleLowerCase('tr-TR');
                    return low.includes('daha fazla') || low.includes('show more') || low.includes('see more');
                }""")
                if not still_collapsed:
                    break

            return {"expanded": True, "clicked": clicked}
        except Exception as e:
            logger.warning(f"Kariyer.net description expand warning: {e}")
            return {"expanded": False, "clicked": False, "reason": str(e)}

    async def _read_kariyer_job(self, page, transition_prefix: str = "") -> ActionResult:
        page_transition_note = transition_prefix

        # First check if a security gate is currently open and wait for operator confirmation
        await self._wait_for_kariyer_security_gate(page, "Kariyer.net başlangıç kontrolü")

        if not self.kariyer_search_initialized:
            init_q = self.kariyer_queries[self.current_query_idx]
            await self._execute_kariyer_keyword_search(page, init_q)
            await self._collect_kariyer_cards(page)

        def get_next_unvisited():
            for idx, c in enumerate(self.kariyer_cards_cache):
                if c["key"] not in self.visited_card_keys:
                    return idx, c
            return -1, None

        target_idx, target_card = get_next_unvisited()

        attempts = 0
        while target_card is None and attempts < len(self.kariyer_queries):
            attempts += 1
            transition = await self._kariyer_advance_page_or_query(page, force_next_query=(attempts > 1))
            page_transition_note += f"[OTOMATİK SAYFA/SORGU GEÇİŞİ: {transition}] "
            target_idx, target_card = get_next_unvisited()

        if target_card is None:
            if self._switch_to_next_platform_state():
                return await self._read_linkedin_job(
                    page,
                    transition_prefix="[🔗 ZİNCİR GEÇİŞİ: Kariyer.net etabı tamamlandı, otomatik olarak LinkedIn etabına geçildi!] ",
                )
            return ActionResult(
                extracted_content=self._build_final_summary("Kariyer.net üzerinde incelenecek yeni iş ilanı kalmadı."),
                include_in_memory=False,
            )

        total_on_page = len(self.kariyer_cards_cache)

        # Highlight on listing page if currently there
        if "kariyer.net/is-ilanlari" in page.url:
            try:
                await page.evaluate("""(targetUrl) => {
                    const links = Array.from(document.querySelectorAll('a[href*="/is-ilani/"]'));
                    for (const a of links) {
                        if (targetUrl.includes(a.getAttribute('href') || '___none___')) {
                            const card = a.closest('[data-test="ad-card"], .k-ad-card, .list-items') || a;
                            card.style.outline = '3px solid #8316b5';
                            card.style.backgroundColor = 'rgba(131, 22, 181, 0.08)';
                            card.scrollIntoView({block: 'center', behavior: 'smooth'});
                            break;
                        }
                    }
                }""", target_card["url"])
                await asyncio.sleep(0.8)
            except Exception:
                pass

        # Human-like pacing before opening detail URL to avoid triggering PerimeterX rate limits
        await asyncio.sleep(random.uniform(1.4, 2.4))

        try:
            await page.goto(target_card["url"], wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            return ActionResult(error=f"Kariyer.net ilan detay sayfası açılamadı ({target_card['url']}): {e}")

        await asyncio.sleep(2.2)

        # If Kariyer.net redirected back to /is-ilanlari (because PerimeterX blocked the job API), give modal 1s to mount
        if "/is-ilani/" not in page.url:
            await asyncio.sleep(1.0)

        # If Kariyer.net shows "Basılı Tut", pause, notify operator, wait for confirmation, then re-open target_card["url"]!
        gate_resolved = await self._wait_for_kariyer_security_gate(
            page, f"İlan detayı açılışı ({target_card.get('position', 'İlan')})"
        )
        if gate_resolved or "/is-ilani/" not in page.url:
            try:
                await page.goto(target_card["url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2.4)
                await self._wait_for_kariyer_security_gate(
                    page, f"İlan detayı tekrar açılışı ({target_card.get('position', 'İlan')})"
                )
            except Exception as e:
                logger.warning(f"Kariyer detail reload after gate warning: {e}")

        self.visited_card_keys.add(target_card["key"])
        self.platform_visited_count += 1
        self.total_visited_count += 1

        await self._dismiss_kariyer_cookie_banner(page)
        expand_info = await self._expand_kariyer_description(page)

        current_q = self.kariyer_queries[self.current_query_idx]
        chain_pos = f"{self.current_platform_idx + 1}/{len(self.platforms_chain)}"
        detail_data = await page.evaluate("""async (args) => {
            const {visitedCount, maxJobs, pageNum, cardIdx, totalOnPage, savedCount, queryStr, fallbackMeta, chainPos} = args;

            const oldAlert = document.getElementById('ai-security-gate-alert');
            if (oldAlert) oldAlert.remove();

            let banner = document.getElementById('ai-job-scout-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'ai-job-scout-banner';
                document.body.appendChild(banner);
            }
            banner.style.cssText = 'position:fixed;bottom:12px;left:12px;z-index:999999;background:#8316b5;color:#fff;padding:8px 14px;border-radius:8px;font-family:sans-serif;font-size:13px;font-weight:bold;box-shadow:0 4px 12px rgba(0,0,0,0.35);pointer-events:none;';
            banner.textContent = `🤖 AI Job Scout [Zincir ${chainPos}: Kariyer.net] | Sorgu: ${queryStr} | Sayfa: ${pageNum} | İlan: #${visitedCount}/${maxJobs} (Aday Kart: ${cardIdx}/${totalOnPage}) | Kaydedilen: ${savedCount}`;

            const h1El = document.querySelector('h1, [data-test="job-title"], .job-title');
            const compEl = document.querySelector('a.company-name, [data-test="company-name"], .company-name, .company-title');
            const locEl = document.querySelector('[data-test="company-location"], .company-location, .location-name, .job-location');

            let pagePos = h1El ? (h1El.innerText || '').trim() : '';
            let pageComp = compEl ? (compEl.innerText || '').trim() : '';
            let pageLoc = locEl ? (locEl.innerText || '').trim() : '';

            if (pageComp && pagePos.endsWith(pageComp)) {
                pagePos = pagePos.slice(0, pagePos.length - pageComp.length).trim();
            }

            const allBtns = Array.from(document.querySelectorAll('button, a[role="button"], .save-button, [data-test*="save"], [data-test*="favorite"]'));
            const saveBtn = allBtns.find(b => {
                const t = (b.innerText || '').trim().toLowerCase();
                const a = (b.getAttribute('aria-label') || b.getAttribute('title') || '').toLowerCase();
                const dt = (b.getAttribute('data-test') || '').toLowerCase();
                if (t.includes('başvur') || t.includes('özgeçmiş')) return false;
                return (
                    t === 'kaydet' || t === 'kaydedildi' || t === 'ilanı kaydet' ||
                    a.includes('kaydet') || a.includes('favori') ||
                    dt.includes('save') || dt.includes('favorite')
                );
            });
            let saveState = 'Kaydet (Kaydedilmemiş)';
            if (saveBtn) {
                const bt = (saveBtn.innerText || '').trim().toLowerCase();
                const cls = (saveBtn.className || '').toLowerCase();
                if (bt.includes('kaydedildi') || cls.includes('saved') || cls.includes('active')) {
                    saveState = 'Kaydedildi';
                }
            }

            window.scrollBy({top: 240, behavior: 'smooth'});

            const descSelectors = [
                '.job-detail-content',
                '.aligment-container',
                '.job-detail',
                '[data-test="job-description"]',
                '.qualifications',
                'main'
            ];
            let fullDesc = '';
            for (const sel of descSelectors) {
                const el = document.querySelector(sel);
                if (el && (el.innerText || '').trim().length > 120) {
                    fullDesc = el.innerText.trim();
                    break;
                }
            }
            if (!fullDesc) {
                const bodyText = (document.body ? document.body.innerText : '').trim();
                const startMarkers = ['GENEL NİTELİKLER', 'Genel Nitelikler', 'ARADIĞIMIZ NİTELİKLER', 'İş Tanımı', 'İŞ TANIMI'];
                let startIdx = -1;
                for (const m of startMarkers) {
                    const i = bodyText.indexOf(m);
                    if (i !== -1) { startIdx = i; break; }
                }
                if (startIdx !== -1) {
                    const endIdx = bodyText.indexOf('Benzer İlanlar', startIdx);
                    fullDesc = endIdx !== -1 ? bodyText.slice(startIdx, endIdx) : bodyText.slice(startIdx, startIdx + 15000);
                } else {
                    fullDesc = bodyText.slice(0, 15000);
                }
            }

            fullDesc = fullDesc
                .replace(/\\n\\s*(Daha Fazla Göster|Daha Az Göster)\\s*(\\n|$)/gi, '\\n')
                .trim();

            return {
                position: fallbackMeta.position || pagePos || '',
                company: fallbackMeta.company || pageComp || '',
                location: fallbackMeta.location || pageLoc || '',
                work_mode: fallbackMeta.work_mode || '',
                saveState: saveState,
                description: fullDesc.slice(0, 15000)
            };
        }""", {
            "visitedCount": self.platform_visited_count,
            "maxJobs": self.jobs_per_platform,
            "pageNum": self.current_page_num,
            "cardIdx": target_idx + 1,
            "totalOnPage": total_on_page,
            "savedCount": len(self.saved_by_platform["kariyer"]),
            "queryStr": current_q,
            "fallbackMeta": target_card,
            "chainPos": chain_pos,
        })

        parsed_pos = _clean_text_field((detail_data or {}).get("position") or target_card.get("position") or "")
        parsed_comp = _clean_text_field((detail_data or {}).get("company") or target_card.get("company") or "")
        parsed_loc = _clean_text_field((detail_data or {}).get("location") or target_card.get("location") or "")
        parsed_mode = _clean_text_field((detail_data or {}).get("work_mode") or target_card.get("work_mode") or "")
        save_state = (detail_data or {}).get("saveState") or "Kaydet (Kaydedilmemiş)"
        desc_text = (detail_data or {}).get("description") or ""

        self.last_card_meta = {
            "platform": "kariyer",
            "position": parsed_pos,
            "company": parsed_comp,
            "location": parsed_loc,
            "work_mode": parsed_mode,
            "url": page.url,
        }

        fit_title = parsed_pos or "İlan"
        fit = _analyze_job_fit(fit_title, desc_text)
        if not expand_info.get("expanded") and fit["verdict"] == "UYGUN" and len(desc_text) < 800:
            fit["reject_reasons"].append("İlan açıklaması tam açılamadı ('Daha Fazla Göster' genişletilemedi)")
            fit["verdict"] = "BELİRSİZ"
        self.last_fit = {"platform": "kariyer", "url": page.url, "title": fit_title, "fit": fit, "saved": False}
        fit_summary = _format_fit_analysis(fit)

        short_label = (parsed_pos or "İlan")[:45]
        self.inspected_by_platform["kariyer"].append(f"#{self.platform_visited_count}: {short_label}")
        recent_history = ", ".join(self.inspected_by_platform["kariyer"][-8:])
        saved_str = ", ".join(self.saved_by_platform["kariyer"]) or "Henüz yok"
        next_num = self.total_visited_count + 1

        return ActionResult(
            extracted_content=(
                f"{page_transition_note}=== [ZİNCİR {chain_pos}: KARİYER.NET] İLAN #{self.platform_visited_count} / {self.jobs_per_platform} "
                f"(Sayfa {self.current_page_num}, Aday Kart {target_idx + 1}/{total_on_page} | Listeden Hızlı Elenen Alakasız İlan: {len(self.kariyer_skipped_on_list)}) ===\n"
                f"İLAN BİLGİSİ: Pozisyon: '{parsed_pos}' | Şirket: '{parsed_comp}' | Konum: '{parsed_loc}' | Çalışma Modeli: '{parsed_mode}'\n"
                f"İLERLEME (Kariyer.net): {self.platform_visited_count}/{self.jobs_per_platform} | Kaydedilenler ({len(self.saved_by_platform['kariyer'])}): {saved_str}\n"
                f"SON İNCELENENLER: {recent_history}\n"
                f"URL: {page.url}\n"
                f"[KAYIT DURUMU: {save_state}]\n"
                f"{fit_summary}\n---\n"
                f"{desc_text}\n\n"
                f"KARAR TALİMATI:\n"
                f"- Yukarıdaki ÖN ANALİZ sonucu 'UYGUN' ise ve ilan metni de CV ile gerçekten örtüşüyorsa (>= 75 puan, KAYIT DURUMU 'Kaydedildi' değilse) ŞİMDİ save_job(position='{parsed_pos}', company='{parsed_comp}', location='{parsed_loc}', work_mode='{parsed_mode}', ...) aksiyonunu çağır.\n"
                f"- Sonuç 'UYGUN DEĞİL' veya 'BELİRSİZ' ise ASLA save_job ÇAĞIRMA! Doğrudan read_job_by_order(job_order={next_num}) aksiyonunu çağır!"
            ),
            include_in_memory=False,
        )

    async def _save_kariyer_job_impl(self, page, clean_pos: str, clean_comp: str, full_location: str, score: int, reason: str) -> str:
        if page:
            await self._wait_for_kariyer_security_gate(page, f"İlan kaydetme ({clean_pos})")
        url = self.last_card_meta.get("url") or (page.url if page else "") or "https://www.kariyer.net/is-ilanlari"
        kariyer_status = "Kariyer.net Kaydet butonu kontrol edildi"
        if page:
            try:
                click_res = await page.evaluate("""async () => {
                    window.scrollTo({top: 0, behavior: 'smooth'});
                    await new Promise(r => setTimeout(r, 350));

                    const findSaveBtn = () => {
                        const candidates = Array.from(document.querySelectorAll(
                            'button, a[role="button"], .save-button, [data-test*="save"], [data-test*="favorite"], [class*="save-job"]'
                        ));
                        return candidates.find(b => {
                            const t = (b.innerText || '').trim().toLowerCase();
                            const a = (b.getAttribute('aria-label') || b.getAttribute('title') || '').toLowerCase();
                            const dt = (b.getAttribute('data-test') || '').toLowerCase();
                            const r = b.getBoundingClientRect();
                            if (t.includes('başvur') || t.includes('apply') || t.includes('özgeçmiş')) {
                                return false;
                            }
                            return r.width > 16 && r.height > 16 && (
                                t === 'kaydet' || t === 'kaydedildi' || t === 'ilanı kaydet' ||
                                a.includes('kaydet') || a.includes('favori') ||
                                dt.includes('save') || dt.includes('favorite')
                            );
                        });
                    };

                    const btn = findSaveBtn();
                    if (!btn) return {status: 'not_found'};
                    const tBefore = (btn.innerText || '').trim().toLowerCase();
                    const clsBefore = (btn.className || '').toLowerCase();
                    if (tBefore.includes('kaydedildi') || clsBefore.includes('saved') || clsBefore.includes('active')) {
                        return {status: 'already_saved'};
                    }
                    btn.style.outline = '3px solid #057642';
                    btn.click();
                    await new Promise(r => setTimeout(r, 1200));
                    return {status: 'clicked'};
                }""")
                st = (click_res or {}).get("status")
                if st == "already_saved":
                    kariyer_status = "İlan Kariyer.net'te zaten 'Kaydedildi' durumunda"
                elif st == "clicked":
                    kariyer_status = "Kariyer.net 'Kaydet' butonuna tıklandı"
                else:
                    kariyer_status = "İlan yerel Kariyer.net saved_jobs.md raporuna kaydedildi"
            except Exception as e:
                kariyer_status = f"Kariyer.net Kaydet tıklama uyarısı: {e}"

        _append_job_to_markdown("kariyer", clean_pos, clean_comp, full_location, f"{score}%", reason, url)
        self.saved_by_platform["kariyer"].append(f"{clean_pos} @ {clean_comp} ({score}%)")
        return kariyer_status

    # ==========================================================================
    # UNIFIED CHAIN ACTIONS
    # ==========================================================================
    def _register_custom_actions(self):
        """Register unified chain actions for LinkedIn <-> Kariyer.net"""

        @self.registry.action(
            "Aktif platformdaki (LinkedIn veya Kariyer.net) sıradaki iş ilanını açıp tam metnini okur. Aktif platformdaki hedef ilan sayısı tamamlandığında OTOMATİK OLARAK zincirdeki sıradaki platforma (ör. LinkedIn'den Kariyer.net'e veya Kariyer.net'ten LinkedIn'e) geçer. İlan incelemek için HER ZAMAN bu aksiyonu kullan!"
        )
        async def read_job_by_order(
            job_order: int = 1,
            browser: BrowserContext = None,
        ):
            page = await browser.get_current_page()
            if not page:
                return ActionResult(error="Aktif sayfa bulunamadı.")

            transition_banner = ""
            if self.platform_visited_count >= self.jobs_per_platform:
                prev_label = self.current_platform_label
                if self._switch_to_next_platform_state():
                    new_label = self.current_platform_label
                    transition_banner = (
                        f"[🔗 OTOMATİK ZİNCİR GEÇİŞİ: {prev_label} etabı ({self.jobs_per_platform} ilan) tamamlandı! "
                        f"Şimdi zincirin {self.current_platform_idx + 1}. etabı olan {new_label} başlatıldı!] "
                    )
                else:
                    return ActionResult(
                        extracted_content=self._build_final_summary(),
                        include_in_memory=False,
                    )

            if self.current_platform == "kariyer":
                return await self._read_kariyer_job(page, transition_prefix=transition_banner)
            else:
                return await self._read_linkedin_job(page, transition_prefix=transition_banner)

        @self.registry.action(
            "Şu an açık olan uygun iş ilanını (aktif platform LinkedIn ise LinkedIn'de, Kariyer.net ise Kariyer.net'te) 'Kaydet' yapar ve ilgili klasörün saved_jobs.md dosyasına kaydeder (ASLA başvuru yapmaz)."
        )
        async def save_job(
            position: str,
            company: str,
            location: str,
            work_mode: str,
            score: int,
            reason: str,
            browser: BrowserContext,
        ):
            clean_pos = _clean_text_field(position)
            if "@" in clean_pos:
                clean_pos = clean_pos.split("@")[0].strip()
            if len(re.findall(r'[A-Za-zÇĞİÖŞÜçğıöşü]', clean_pos)) < 3 and self.last_card_meta.get("position"):
                clean_pos = self.last_card_meta["position"]

            clean_comp = _clean_text_field(company)
            if len(re.findall(r'[A-Za-z0-9ÇĞİÖŞÜçğıöşü]', clean_comp)) < 2 and self.last_card_meta.get("company"):
                clean_comp = self.last_card_meta["company"]

            clean_loc = _clean_text_field(location)
            if clean_loc.lower().startswith("örkiye"):
                clean_loc = "T" + clean_loc[1:]
            if self.last_card_meta.get("location") and (len(clean_loc) < 3 or "örkiye" in clean_loc.lower()):
                clean_loc = self.last_card_meta["location"]

            clean_mode = _clean_text_field(work_mode) or self.last_card_meta.get("work_mode", "")
            if clean_mode and clean_mode.lower() not in clean_loc.lower():
                full_location = f"{clean_loc} ({clean_mode})" if clean_loc else clean_mode
            else:
                full_location = clean_loc or clean_mode

            page = await browser.get_current_page()
            active_plat = self.last_card_meta.get("platform") or self.current_platform
            next_order = self.total_visited_count + 1

            # Deterministic save guard: prevent LLM from saving mismatched or unexpanded/uncertain jobs
            if self.last_fit:
                fit = self.last_fit.get("fit") or {}
                verdict = fit.get("verdict")
                if self.last_fit.get("saved"):
                    return ActionResult(
                        extracted_content=f"Bu ilan zaten kaydedildi. Şimdi read_job_by_order(job_order={next_order}) çağır!",
                        include_in_memory=False,
                    )
                if verdict != "UYGUN":
                    reasons = "; ".join(fit.get("reject_reasons") or ["Yeterli ilan detayı/teknoloji eşleşmesi yok"])
                    logger.info(f"save_job blocked by fit guard ({verdict}): {clean_pos} @ {clean_comp} -> {reasons}")
                    return ActionResult(
                        extracted_content=(
                            f"KAYIT ENGELLENDİ ({verdict}): {clean_pos} @ {clean_comp} ilanı otomatik CV uyum kontrolünden geçemedi "
                            f"(Neden: {reasons}). Bu ilanı kaydetme, doğrudan read_job_by_order(job_order={next_order}) çağır!"
                        ),
                        include_in_memory=False,
                    )
                effective_score = min(int(score), int(fit.get("rule_score") or score))
                if effective_score < 75:
                    return ActionResult(
                        extracted_content=(
                            f"KAYIT ENGELLENDİ: Efektif uyum puanı ({effective_score}%, kural tavanı: {fit.get('rule_score')}) 75'in altında. "
                            f"Kaydetmeden doğrudan read_job_by_order(job_order={next_order}) çağır!"
                        ),
                        include_in_memory=False,
                    )
                score = effective_score
                self.last_fit["saved"] = True

            if active_plat == "kariyer":
                status_msg = await self._save_kariyer_job_impl(page, clean_pos, clean_comp, full_location, score, reason)
            else:
                status_msg = await self._save_linkedin_job_impl(page, clean_pos, clean_comp, full_location, score, reason)

            msg = (
                f"{status_msg} | Kaydedildi ({self.current_platform_label}): {clean_pos} @ {clean_comp} ({score}%). "
                f"Şimdi sıradaki ilan için read_job_by_order(job_order={next_order}) çağır!"
            )
            logger.info(msg)
            return ActionResult(extracted_content=msg, include_in_memory=False)

        @self.registry.action(
            "Zincirdeki bir sonraki platforma (ör. LinkedIn'den Kariyer.net'e veya Kariyer.net'ten LinkedIn'e) manuel geçiş yapar."
        )
        async def switch_to_next_platform(
            browser: BrowserContext,
        ):
            prev_label = self.current_platform_label
            if self._switch_to_next_platform_state():
                return ActionResult(
                    extracted_content=(
                        f"{prev_label} etabından {self.current_platform_label} etabına geçildi! "
                        f"Şimdi read_job_by_order(job_order={self.total_visited_count + 1}) çağır!"
                    ),
                    include_in_memory=False,
                )
            return ActionResult(
                extracted_content=self._build_final_summary(),
                include_in_memory=False,
            )

        @self.registry.action(
            "Aktif platformda (LinkedIn veya Kariyer.net) bir sonraki ilan sayfasına veya arama sorgusuna geçer."
        )
        async def next_jobs_page(
            browser: BrowserContext,
        ):
            page = await browser.get_current_page()
            if not page:
                return ActionResult(error="Aktif sayfa bulunamadı.")
            if self.current_platform == "kariyer":
                transition_msg = await self._kariyer_advance_page_or_query(page)
            else:
                transition_msg = await self._linkedin_advance_page_or_query(page)
            return ActionResult(
                extracted_content=f"{transition_msg}. Şimdi read_job_by_order(job_order={self.total_visited_count + 1}) çağır!",
                include_in_memory=False,
            )

        @self.registry.action(
            "Aktif platformda (LinkedIn veya Kariyer.net) verilen anahtar kelimeyle iş araması yapar."
        )
        async def search_jobs(
            keywords: str,
            browser: BrowserContext,
        ):
            page = await browser.get_current_page()
            if not page:
                return ActionResult(error="Aktif sayfa bulunamadı.")
            kw = keywords.strip() or "Full Stack Developer"
            self.current_page_num = 1
            if self.current_platform == "kariyer":
                await self._execute_kariyer_keyword_search(page, kw)
                cards = await self._collect_kariyer_cards(page)
                count = len(cards)
            else:
                if "türkiye" not in kw.lower() and "izmir" not in kw.lower():
                    kw = f"{kw} Türkiye"
                q = urllib.parse.quote(kw)
                await page.goto(f"https://www.linkedin.com/jobs/search-results/?keywords={q}", wait_until="domcontentloaded")
                await asyncio.sleep(3.5)
                cards = page.locator('div[role="button"][componentkey^="job-card-component-ref-"]')
                count = await cards.count()

            return ActionResult(
                extracted_content=(
                    f"{self.current_platform_label} üzerinde '{kw}' araması tamamlandı ({count} uygun aday ilan yüklendi). "
                    f"Şimdi read_job_by_order(job_order={self.total_visited_count + 1}) çağır!"
                ),
                include_in_memory=False,
            )

    @time_execution_sync('--act')
    async def act(
            self,
            action: ActionModel,
            browser_context: Optional[BrowserContext] = None,
            page_extraction_llm: Optional[BaseChatModel] = None,
            sensitive_data: Optional[Dict[str, str]] = None,
            available_file_paths: Optional[list[str]] = None,
            context: Context | None = None,
    ) -> ActionResult:
        """Execute an action"""
        try:
            for action_name, params in action.model_dump(exclude_unset=True).items():
                if params is not None:
                    if action_name.startswith("mcp"):
                        mcp_tool = self.registry.registry.actions.get(action_name).function
                        result = await mcp_tool.ainvoke(params)
                    else:
                        result = await self.registry.execute_action(
                            action_name,
                            params,
                            browser=browser_context,
                            page_extraction_llm=page_extraction_llm,
                            sensitive_data=sensitive_data,
                            available_file_paths=available_file_paths,
                            context=context,
                        )

                    if isinstance(result, str):
                        return ActionResult(extracted_content=result)
                    elif isinstance(result, ActionResult):
                        return result
                    elif result is None:
                        return ActionResult()
                    else:
                        raise ValueError(f'Invalid action result type: {type(result)} of {result}')
            return ActionResult()
        except Exception as e:
            raise e

    async def setup_mcp_client(self, mcp_server_config: Optional[Dict[str, Any]] = None):
        self.mcp_server_config = mcp_server_config
        if self.mcp_server_config:
            self.mcp_client = await setup_mcp_client_and_tools(self.mcp_server_config)
            self.register_mcp_tools()

    def register_mcp_tools(self):
        if self.mcp_client:
            for server_name in self.mcp_client.server_name_to_tools:
                for tool in self.mcp_client.server_name_to_tools[server_name]:
                    tool_name = f"mcp.{server_name}.{tool.name}"
                    self.registry.registry.actions[tool_name] = RegisteredAction(
                        name=tool_name,
                        description=tool.description,
                        function=tool,
                        param_model=create_tool_param_model(tool),
                    )

    async def close_mcp_client(self):
        if self.mcp_client:
            await self.mcp_client.__aexit__(None, None, None)
