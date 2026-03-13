import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.parse import urljoin
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import CONFIG

SEARCH_CFG = CONFIG.get("search", {})
RESUME_CFG = CONFIG.get("resume", {})
MATCHING_CFG = CONFIG.get("matching", {})
APPLY_CFG = CONFIG.get("apply", {})
LOG_CFG = CONFIG.get("logging", {})

DEBUG_HOST = SEARCH_CFG.get("debug_host", "127.0.0.1")
DEBUG_PORT = int(SEARCH_CFG.get("debug_port", 9222))
DEBUG_URL = f"http://{DEBUG_HOST}:{DEBUG_PORT}/json/version"
SEARCH_URLS = SEARCH_CFG.get("search_urls", [])
WAIT_TIMEOUT = int(SEARCH_CFG.get("wait_timeout", 12))
PAGE_LOAD_WAIT = float(SEARCH_CFG.get("page_load_wait", 5))
DETAIL_LOAD_WAIT = float(SEARCH_CFG.get("detail_load_wait", 4))
FLOW_RETRY_LIMIT = int(SEARCH_CFG.get("flow_retry_limit", 4))
CLICK_PAUSE = max(0.15, float(SEARCH_CFG.get("click_pause", 1.5)))
MAX_FLOW_STEPS = int(SEARCH_CFG.get("max_flow_steps", 20))
MAX_PAGES_PER_SEARCH = int(SEARCH_CFG.get("max_pages_per_search", 0))

SESSION_APPLY_CAP = int(APPLY_CFG.get("session_apply_cap", 25))
QUICK_APPLY_ONLY = bool(APPLY_CFG.get("quick_apply_only", True))
SKIP_EXTERNAL = bool(APPLY_CFG.get("skip_external", True))
SKIP_ALREADY_APPLIED = bool(APPLY_CFG.get("skip_already_applied", True))
AUTO_SUBMIT_ENABLED = bool(APPLY_CFG.get("auto_submit_enabled", True))
SKIP_ON_UNANSWERED_QUESTIONS = bool(APPLY_CFG.get("skip_on_unanswered_questions", True))
FORCE_RESUME_UPLOAD = bool(APPLY_CFG.get("force_resume_upload", False))
DIRECT_APPLY_URL_FALLBACK = bool(APPLY_CFG.get("direct_apply_url_fallback", True))
MAX_JOBS_PER_RUN = int(APPLY_CFG.get("max_jobs_per_run", 20))
WAIT_FOR_MANUAL_QUESTIONS = bool(APPLY_CFG.get("wait_for_manual_questions", True))
MANUAL_QUESTION_TIMEOUT = int(APPLY_CFG.get("manual_question_timeout_sec", 900))
MANUAL_QUESTION_SCAN_INTERVAL = float(APPLY_CFG.get("manual_question_scan_interval_sec", 2))
SCRIPT_EXE = APPLY_CFG.get("script_exe", "Script.exe")
SCRIPT_AU3 = APPLY_CFG.get("script_au3", "Script.au3")

SHOW_MATCH_DETAILS = bool(LOG_CFG.get("show_match_details", True))
SHOW_SKIP_REASONS = bool(LOG_CFG.get("show_skip_reasons", True))

RESUME_FILE = RESUME_CFG.get("resume_file", "")
COVER_LETTER_FILE = RESUME_CFG.get("cover_letter_file", "")
PROFILE_KEYWORDS = RESUME_CFG.get("profile_keywords", {})
MUST_HAVE_KEYWORDS = PROFILE_KEYWORDS.get("must_have", [])
PREFERRED_KEYWORDS = PROFILE_KEYWORDS.get("preferred", [])
EXCLUDE_KEYWORDS = RESUME_CFG.get("exclude_keywords", [])

MUST_HAVE_WEIGHT = int(MATCHING_CFG.get("must_have_weight", 12))
PREFERRED_WEIGHT = int(MATCHING_CFG.get("preferred_weight", 4))
EXCLUDE_PENALTY = int(MATCHING_CFG.get("exclude_penalty", 20))
MUST_HAVE_MISSING_PENALTY = int(MATCHING_CFG.get("must_have_missing_penalty", 10))
MIN_MATCH_SCORE = int(MATCHING_CFG.get("min_match_score", 20))
MATCHING_ENABLED = bool(MATCHING_CFG.get("enabled", False))
REQUIRE_RESUME_ON_STARTUP = bool(RESUME_CFG.get("require_on_startup", False))

LOG_DIR = os.path.join(os.getcwd(), "logs")
SCREENSHOT_DIR = os.path.join(LOG_DIR, "screenshots")
BEFORE_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "before")
AFTER_SCREENSHOT_DIR = os.path.join(SCREENSHOT_DIR, "after")
CSV_LOG_PATH = os.path.join(LOG_DIR, "applied_jobs.csv")
LAST_HR_TEXT = ""
LAST_HR_LINK = ""

BLOCKED_HR_IDENTIFIERS = [
    "agastya",
    "agastyakapoor",
    "agastyakapoorgk",
]
FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "proton.me",
    "protonmail.com",
}


def safe_input(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def normalize_path(path_value):
    if not path_value:
        return ""
    return os.path.abspath(os.path.expanduser(path_value))


def validate_config():
    if not isinstance(SEARCH_URLS, list) or not SEARCH_URLS:
        print("CONFIG_ERROR: search.search_urls must contain at least one URL")
        sys.exit(1)

    resume_path = normalize_path(RESUME_FILE)
    if not resume_path:
        if REQUIRE_RESUME_ON_STARTUP:
            print("CONFIG_ERROR: resume.resume_file is required")
            sys.exit(1)
        print("WARN: resume.resume_file missing; startup continue hoga")
    elif not os.path.exists(resume_path):
        if REQUIRE_RESUME_ON_STARTUP:
            print(f"CONFIG_ERROR: resume file not found -> {resume_path}")
            sys.exit(1)
        print(f"WARN: resume file not found -> {resume_path}")

    cover_path = normalize_path(COVER_LETTER_FILE)
    if cover_path and not os.path.exists(cover_path):
        print(f"WARN: cover letter file not found -> {cover_path}")


def get_debug_info(timeout=2):
    try:
        with urlopen(DEBUG_URL, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def find_chrome_binary():
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def start_debug_chrome(first_url):
    chrome_binary = find_chrome_binary()
    if not chrome_binary:
        print("Chrome binary nahi mila; normal WebDriver mode use karenge.")
        return False

    profile_dir = os.path.join(os.getcwd(), ".seekbot-chrome-profile")
    os.makedirs(profile_dir, exist_ok=True)

    args = [
        chrome_binary,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        first_url,
    ]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        data = get_debug_info(timeout=1)
        if data:
            print("Debug Chrome auto-start ho gaya.")
            print("Browser:", data.get("Browser"))
            return True
        time.sleep(0.5)

    print("Debug Chrome auto-start fail hua; normal WebDriver mode use karenge.")
    return False


def init_driver():
    chrome_options = Options()
    debug_data = get_debug_info(timeout=3)

    if debug_data:
        print("Debug Chrome running")
        print("Browser:", debug_data.get("Browser"))
        chrome_options.debugger_address = f"{DEBUG_HOST}:{DEBUG_PORT}"
        return webdriver.Chrome(options=chrome_options)

    print("Chrome debug mode running nahi hai; auto-start try kar rahe hain...")
    started = start_debug_chrome(SEARCH_URLS[0])
    if started and get_debug_info(timeout=2):
        chrome_options.debugger_address = f"{DEBUG_HOST}:{DEBUG_PORT}"
        return webdriver.Chrome(options=chrome_options)

    print("Fresh Chrome session start kiya (debug attach ke bina).")
    return webdriver.Chrome()


def normalize_text(value):
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def find_hits(haystack, keywords):
    hits = []
    for raw in keywords:
        key = normalize_text(raw)
        if key and key in haystack:
            hits.append(raw)
    return hits


def evaluate_match(title_text, detail_text):
    full_text = normalize_text(f"{title_text} {detail_text}")
    must_hits = find_hits(full_text, MUST_HAVE_KEYWORDS)
    preferred_hits = find_hits(full_text, PREFERRED_KEYWORDS)
    excluded_hits = find_hits(full_text, EXCLUDE_KEYWORDS)

    missing_must_have = [x for x in MUST_HAVE_KEYWORDS if x not in must_hits]

    score = 0
    score += len(must_hits) * MUST_HAVE_WEIGHT
    score += len(preferred_hits) * PREFERRED_WEIGHT
    score -= len(excluded_hits) * EXCLUDE_PENALTY
    score -= len(missing_must_have) * MUST_HAVE_MISSING_PENALTY

    return {
        "score": score,
        "eligible": score >= MIN_MATCH_SCORE,
        "matched_must_have": must_hits,
        "matched_preferred": preferred_hits,
        "missing_must_have": missing_must_have,
        "excluded_term_hit": excluded_hits,
    }


def safe_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.1)
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)
    time.sleep(CLICK_PAUSE)


def open_jobs_page(driver, url):
    driver.get(url)
    time.sleep(PAGE_LOAD_WAIT)
    print("Jobs page opened")
    print("Title:", driver.title)
    print("URL:", driver.current_url)


def extract_job_key_from_href(href):
    href = (href or "").strip()
    if not href:
        return ""
    if "/job/" in href:
        return href.split("?")[0]
    return href


def get_job_entries(driver):
    selectors = [
        "//a[@data-automation='jobTitle' and contains(@href, '/job/')]",
        "//article//a[contains(@href, '/job/')]",
    ]

    raw = []
    for xp in selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            href = (elem.get_attribute("href") or "").strip()
            if not href:
                continue
            title = (elem.text or "").strip() or "Untitled Job"
            key = extract_job_key_from_href(href)
            if not key:
                continue
            list_applied = False
            try:
                card = elem.find_element(By.XPATH, "./ancestor::article[1]")
                card_text = normalize_text(card.text)
                list_applied = (
                    " applied " in f" {card_text} "
                    or "application sent" in card_text
                    or "you ve applied" in card_text
                )
            except Exception:
                list_applied = False

            raw.append(
                {"key": key, "url": href, "title": title, "list_applied": list_applied}
            )
        if raw:
            break

    dedup = {}
    for item in raw:
        dedup[item["key"]] = item
    return list(dedup.values())


def is_external_apply(driver):
    checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), \"advertiser's site\")]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'take you to the advertiser')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'external site')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply on company site')]",
    ]
    for xp in checks:
        if driver.find_elements(By.XPATH, xp):
            return True
    return False


def is_already_applied(driver):
    checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'application sent')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'already applied')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), \"you've applied\")]",
    ]
    for xp in checks:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if elem.is_displayed():
                    return True
            except Exception:
                continue
    return False

def is_application_submitted(driver):
    checks = [
        "//*[@data-automation='application-confirmation']",
        "//*[@data-testid='application-confirmation']",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'application sent')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'application submitted')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'successfully applied')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), \"you've applied\")]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'application complete')]",
    ]
    for xp in checks:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if elem.is_displayed():
                    return True
            except Exception:
                continue
    return False

def is_on_apply_interface(driver):
    current = (driver.current_url or "").lower()
    if "/apply" in current:
        return True
    checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'choose documents')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review and submit')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'answer employer questions')]",
    ]
    for xp in checks:
        if driver.find_elements(By.XPATH, xp):
            return True
    return False


def build_apply_url(job_url):
    url = (job_url or "").strip()
    if not url:
        return ""

    base = url.split("?")[0]
    match = re.search(r"(https?://[^/]+/job/\d+)", base)
    if match:
        return f"{match.group(1)}/apply"

    if "/job/" in base and not base.endswith("/apply"):
        return f"{base.rstrip('/')}/apply"

    return ""


def wait_for_apply_interface(driver, timeout=6):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if is_on_apply_interface(driver):
            return True
        time.sleep(0.1)
    return False


def wait_for_apply_transition(driver, original_url, timeout=12):
    end_time = time.time() + timeout
    while time.time() < end_time:
        current = (driver.current_url or "").lower()
        if is_on_apply_interface(driver):
            return True
        if current != (original_url or "").lower() and "/apply" in current:
            return True
        time.sleep(0.1)
    return False


def switch_to_new_tab_if_any(driver):
    handles = driver.window_handles
    if len(handles) <= 1:
        return
    driver.switch_to.window(handles[-1])


def click_apply(driver, job_url):
    base_selectors = [
        "//*[@data-automation='job-detail-apply']",
        "//*[@data-testid='job-detail-apply']",
        "//main//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'quick apply')]",
        "//main//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'quick apply')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'quick apply')]",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'quick apply')]",
    ]
    if QUICK_APPLY_ONLY:
        possible = base_selectors
    else:
        possible = base_selectors + [
            "//main//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//main//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
        ]

    saw_candidate = False
    saw_quick_candidate = False
    origin_url = driver.current_url
    for xp in possible:
        elems = driver.find_elements(By.XPATH, xp)
        for btn in elems:
            try:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
            except Exception:
                continue

            attrs = " ".join(
                [
                    btn.text or "",
                    btn.get_attribute("aria-label") or "",
                    btn.get_attribute("title") or "",
                    btn.get_attribute("data-automation") or "",
                    btn.get_attribute("data-testid") or "",
                    btn.get_attribute("href") or "",
                ]
            )
            text_btn = normalize_text(attrs)
            is_quick_signal = "quick apply" in text_btn
            if QUICK_APPLY_ONLY and not is_quick_signal:
                continue

            saw_candidate = True
            if is_quick_signal:
                saw_quick_candidate = True
            btn_href = (btn.get_attribute("href") or "").strip()

            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                btn.click()
                print("APPLY_CLICK:normal")
                time.sleep(CLICK_PAUSE)
                switch_to_new_tab_if_any(driver)
                if wait_for_apply_transition(driver, origin_url, timeout=12):
                    return "opened"
            except Exception:
                pass

            try:
                driver.execute_script("arguments[0].click();", btn)
                print("APPLY_CLICK:js")
                time.sleep(CLICK_PAUSE)
                switch_to_new_tab_if_any(driver)
                if wait_for_apply_transition(driver, origin_url, timeout=12):
                    return "opened"
            except Exception:
                pass

            if btn_href and "/apply" in btn_href:
                try:
                    driver.get(btn_href)
                    print("APPLY_CLICK:href")
                    if wait_for_apply_transition(driver, origin_url, timeout=10):
                        print(f"APPLY_BUTTON_HREF:{btn_href}")
                        return "opened"
                except Exception:
                    pass

    allow_fallback = DIRECT_APPLY_URL_FALLBACK and (not QUICK_APPLY_ONLY or saw_quick_candidate)
    if allow_fallback:
        apply_url = build_apply_url(job_url)
        if apply_url:
            try:
                driver.get(apply_url)
                print("APPLY_CLICK:fallback_url")
                if wait_for_apply_transition(driver, origin_url, timeout=10):
                    print(f"APPLY_FALLBACK_URL:{apply_url}")
                    return "opened"
            except Exception:
                pass

    if QUICK_APPLY_ONLY and not saw_quick_candidate:
        non_quick_selectors = [
            "//main//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//main//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
        ]
        for xp in non_quick_selectors:
            elems = driver.find_elements(By.XPATH, xp)
            for elem in elems:
                try:
                    text = normalize_text(elem.text)
                    if elem.is_displayed() and "apply" in text and "quick apply" not in text:
                        return "not_quick_apply"
                except Exception:
                    continue

    if saw_candidate:
        return "visible_but_not_opened"
    return "not_found"

def click_first_match(driver, selectors):
    for xp in selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if not elem.is_displayed() or not elem.is_enabled():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                try:
                    elem.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", elem)
                time.sleep(CLICK_PAUSE)
                return True
            except Exception:
                continue
    return False


def get_job_text_snapshot(driver):
    title = ""
    for xp in ["//h1", "//*[@data-automation='job-detail-title']"]:
        elems = driver.find_elements(By.XPATH, xp)
        if elems:
            title = (elems[0].text or "").strip()
            if title:
                break

    blocks = []
    for xp in [
        "//*[@data-automation='jobAdDetails']",
        "//*[contains(@data-automation, 'job-detail')]",
        "//main",
    ]:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            text = (elem.text or "").strip()
            if text:
                blocks.append(text)
        if blocks:
            break

    return title, "\n".join(blocks).strip()


def select_resume_if_present(driver, target_name="Agastya Resume.pdf"):
    page_text = normalize_text(driver.page_source)
    if target_name.lower() in page_text:
        selectors = [
            f"//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{normalize_text(target_name)}')]",
            f"//option[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{normalize_text(target_name)}')]",
        ]
        if click_first_match(driver, selectors):
            print(f"RESUME_SELECT:{target_name}")
            return True
    print("RESUME_SELECT:keep_current")
    return False


def answer_known_employer_questions(driver):
    yes_selectors = [
        "//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
    ]
    keywords = ["driver", "driver's licence", "right to work", "work rights", "australia"]
    text = normalize_text(driver.page_source)
    matched = any(k in text for k in keywords)
    if not matched:
        return False

    for xp in yes_selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if not elem.is_displayed() or not elem.is_enabled():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                try:
                    elem.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", elem)
                print("EMPLOYER_Q:yes")
                time.sleep(0.2)
                return True
            except Exception:
                continue
    return False


def has_unanswered_required_questions(driver):
    # Strict blockers only: invalid required controls and visible error messages.
    strict_markers = [
        "//*[@aria-invalid='true' and (self::input or self::textarea or self::select)]",
        "//*[@aria-required='true' and (self::input or self::textarea or self::select) and normalize-space(@value)='']",
        "//input[@required and not(@disabled) and normalize-space(@value)='']",
        "//textarea[@required and not(@disabled) and normalize-space(.)='']",
        "//select[@required and not(@disabled) and (not(@value) or @value='')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'please make a selection')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'please answer')]",
    ]

    for xp in strict_markers:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if elem.is_displayed():
                    return True
            except Exception:
                continue
    return False


def find_autoit_binary():
    candidates = [
        shutil.which("AutoIt3"),
        shutil.which("AutoIt3.exe"),
        r"C:\Program Files (x86)\AutoIt3\AutoIt3.exe",
        r"C:\Program Files\AutoIt3\AutoIt3.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def run_upload_script(file_path):
    target = normalize_path(file_path)
    if not target or not os.path.exists(target):
        print(f"UPLOAD_FAIL:file_missing:{target}")
        return False

    script_exe_path = normalize_path(SCRIPT_EXE)
    script_au3_path = normalize_path(SCRIPT_AU3)

    if script_au3_path and os.path.exists(script_au3_path):
        autoit_bin = find_autoit_binary()
        if autoit_bin:
            try:
                completed = subprocess.run([autoit_bin, script_au3_path, target], timeout=20)
                return completed.returncode == 0
            except Exception as e:
                print(f"UPLOAD_FAIL:script_au3:{e}")

    if script_exe_path and os.path.exists(script_exe_path):
        try:
            completed = subprocess.run([script_exe_path, target], timeout=20)
            return completed.returncode == 0
        except Exception as e:
            print(f"UPLOAD_FAIL:script_exe:{e}")

    print("UPLOAD_FAIL:no_executable_upload_runner")
    return False


def click_upload_trigger(driver, label):
    needle = normalize_text(label)
    selectors = [
        f"//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
        f"//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
        f"//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
    ]
    return click_first_match(driver, selectors)


def handle_resume_upload(driver):
    if not FORCE_RESUME_UPLOAD:
        print("UPLOAD:skip_force_resume_upload=False")
        return True

    resume_path = normalize_path(RESUME_FILE)
    cover_path = normalize_path(COVER_LETTER_FILE)

    resume_triggered = click_upload_trigger(driver, "upload a resume") or click_upload_trigger(driver, "resume")
    cover_triggered = click_upload_trigger(driver, "cover letter")

    if not resume_triggered and not cover_triggered:
        print("UPLOAD:skipped:not_requested")
        return True

    if resume_triggered and not run_upload_script(resume_path):
        return False

    if cover_triggered and cover_path and not run_upload_script(cover_path):
        return False

    print("UPLOAD:forced:ok")
    return True


def ensure_log_paths():
    os.makedirs(BEFORE_SCREENSHOT_DIR, exist_ok=True)
    os.makedirs(AFTER_SCREENSHOT_DIR, exist_ok=True)


def safe_filename(value):
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "")
    return clean.strip("_") or "job"


def capture_job_screenshot(driver, job_key, status, phase="after"):
    ensure_log_paths()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{stamp}_{safe_filename(job_key)}_{safe_filename(status)}.png"
    target_dir = BEFORE_SCREENSHOT_DIR if phase == "before" else AFTER_SCREENSHOT_DIR
    out_path = os.path.join(target_dir, fname)
    try:
        driver.save_screenshot(out_path)
        return out_path
    except Exception:
        return ""


def extract_company_and_position(driver, fallback_title):
    position = (fallback_title or "").strip()
    company = ""

    title_selectors = [
        "//h1",
        "//*[@data-automation='job-detail-title']",
        "//*[@data-testid='job-title']",
    ]
    for xp in title_selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            t = (elem.text or "").strip()
            if t:
                position = t
                break
        if position:
            break

    company_selectors = [
        "//*[@data-automation='advertiser-name']",
        "//*[@data-testid='advertiser-name']",
        "//a[contains(@href, '/companies/') and normalize-space(.)!='']",
        "//span[contains(@data-automation, 'advertiser') and normalize-space(.)!='']",
    ]
    for xp in company_selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            c = (elem.text or "").strip()
            if c:
                company = c
                break
        if company:
            break

    if not company:
        text_blob = (driver.page_source or "")[:2000]
        m = re.search(r"by\s+([A-Za-z0-9 &.,'-]{2,60})", text_blob)
        if m:
            company = m.group(1).strip()

    return company or "Unknown", position or "Unknown"


def _normalize_spaces(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _has_blocked_identifier(value):
    lowered = (value or "").lower()
    return any(token in lowered for token in BLOCKED_HR_IDENTIFIERS)


def build_hr_context_text(driver, title_text, detail_text):
    parts = []
    for chunk in [title_text or "", detail_text or ""]:
        if chunk and chunk not in parts:
            parts.append(chunk)

    selectors = [
        "//*[@data-automation='jobAdDetails']",
        "//*[@data-automation='advertiser-name']/ancestor::*[1]",
        "//main//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'recruit')]",
        "//main//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'hiring manager')]",
        "//main//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'contact')]",
        "//main//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'talent acquisition')]",
    ]
    for xp in selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if not elem.is_displayed():
                    continue
                txt = _normalize_spaces(elem.text)
                if txt and txt not in parts:
                    parts.append(txt)
            except Exception:
                continue

    return "\n".join(parts)


def extract_hr_profile_link(driver):
    links = []
    for xp in ["//main//a[@href]", "//a[contains(@href, '/companies/')]"]:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                href = (elem.get_attribute("href") or "").strip()
                text = _normalize_spaces(elem.text).lower()
                if not href:
                    continue
                links.append((urljoin(driver.current_url, href), text))
            except Exception:
                continue

    for href, text in links:
        h = href.lower()
        if any(k in text for k in ["recruit", "hiring", "talent", "contact"]):
            return href
        if any(k in h for k in ["linkedin.com", "/recruit", "/contact"]):
            return href

    for href, _text in links:
        if "/companies/" in href.lower():
            return href

    return ""


def extract_hr_details(text_blob):
    text = text_blob or ""
    hr_name = ""
    hr_email = ""
    hr_contact = ""

    windows = []
    for token in ["recruiter", "hiring manager", "talent acquisition", "contact"]:
        idx = text.lower().find(token)
        while idx != -1:
            start = max(0, idx - 120)
            end = min(len(text), idx + 320)
            windows.append(text[start:end])
            idx = text.lower().find(token, idx + 1)
    if not windows:
        windows = [text]

    emails = []
    for chunk in windows:
        emails.extend(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", chunk))
    for email in emails:
        e = email.strip()
        domain = e.split("@")[-1].lower() if "@" in e else ""
        if _has_blocked_identifier(e):
            continue
        if domain in FREE_EMAIL_DOMAINS:
            continue
        hr_email = e
        break

    phones = []
    for chunk in windows:
        phones.extend(re.findall(r"(?:\+?\d[\d\s()\-]{7,}\d)", chunk))
    for phone in phones:
        p = _normalize_spaces(phone)
        if _has_blocked_identifier(p):
            continue
        digits = re.sub(r"\D", "", p)
        if len(digits) < 8:
            continue
        hr_contact = p
        break

    name_patterns = [
        r"(?:recruiter|hiring manager|contact|talent acquisition)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})",
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*(?:\(|-)\s*(?:recruiter|hiring manager|talent acquisition|contact)",
    ]
    for chunk in windows:
        for pat in name_patterns:
            m = re.search(pat, chunk, flags=re.IGNORECASE)
            if m:
                candidate = _normalize_spaces(m.group(1))
                if _has_blocked_identifier(candidate):
                    continue
                hr_name = candidate
                break
        if hr_name:
            break

    if _has_blocked_identifier(hr_name):
        hr_name = ""
    if _has_blocked_identifier(hr_email):
        hr_email = ""
    if _has_blocked_identifier(hr_contact):
        hr_contact = ""

    return hr_name, hr_email, hr_contact


def append_apply_log(
    company_name,
    position,
    job_link,
    status,
    screenshot_path="",
    before_screenshot_path="",
    hr_name="",
    hr_email="",
    hr_contact="",
    hr_profile_link="",
):
    if status != "submitted":
        return

    ensure_log_paths()
    header = [
        "date",
        "company_name",
        "position",
        "job_link",
        "status",
        "hr_name",
        "hr_email",
        "hr_contact",
        "hr_profile_link",
    ]

    rewrite_header = False
    if os.path.exists(CSV_LOG_PATH):
        try:
            with open(CSV_LOG_PATH, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if not rows or rows[0] != header:
                rewrite_header = True
        except Exception:
            rewrite_header = True
    else:
        rewrite_header = True

    if rewrite_header:
        with open(CSV_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)

    if not any([hr_name, hr_email, hr_contact]):
        hr_name, hr_email, hr_contact = extract_hr_details(LAST_HR_TEXT)
    if not hr_profile_link:
        hr_profile_link = LAST_HR_LINK

    with open(CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%d-%m-%Y"),
            company_name,
            position,
            job_link,
            "submitted",
            hr_name,
            hr_email,
            hr_contact,
            hr_profile_link,
        ])


def is_employer_questions_step(driver):
    current = (driver.current_url or "").lower()
    if "role-requirements" in current or "employer-questions" in current:
        return True
    checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'answer employer questions')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'before you can continue with the application')]",
    ]
    for xp in checks:
        if driver.find_elements(By.XPATH, xp):
            return True
    return False


def wait_for_manual_required_answers(driver):
    if not WAIT_FOR_MANUAL_QUESTIONS:
        return "blocked_questions"

    interval = max(0.5, MANUAL_QUESTION_SCAN_INTERVAL)
    print("MANUAL_WAIT:start mode=infinite")
    last_ping = time.time()
    while True:
        if is_application_submitted(driver):
            return "submitted"
        if not has_unanswered_required_questions(driver):
            print("MANUAL_WAIT:resolved")
            return "resolved"

        now = time.time()
        if now - last_ping >= 30:
            print("MANUAL_WAIT:still_waiting")
            last_ping = now
        time.sleep(interval)

def run_quick_apply_flow(driver):
    step_selectors = [
        (
            "SUBMIT_APPLICATION",
            [
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//button[@data-testid='submit-application-button']",
                "//button[@data-automation='submit-application-button']",
            ],
        ),
        (
            "CONTINUE",
            [
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
                "//button[@data-testid='continue-button']",
                "//button[@data-automation='continue-button']",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
            ],
        ),
        (
            "NEXT",
            [
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
            ],
        ),
        (
            "REVIEW",
            [
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review')]",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review')]",
            ],
        ),
        (
            "SUBMIT",
            [
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
                "//button[@data-testid='submit-button']",
                "//button[@data-automation='submit-button']",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
            ],
        ),
        (
            "YES",
            [
                "//button[normalize-space(.)='Yes']",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
            ],
        ),
    ]

    retries = 0
    while retries < MAX_FLOW_STEPS:
        if is_external_apply(driver):
            return "external"

        if is_application_submitted(driver):
            return "submitted"

        # Hard lock on employer questions when required fields are still pending.
        # This prevents "continue" click spam while user fills answers manually.
        if is_employer_questions_step(driver) and has_unanswered_required_questions(driver):
            manual_state = wait_for_manual_required_answers(driver)
            if manual_state == "submitted":
                return "submitted"
            if manual_state == "resolved":
                retries = 0
                continue

        progressed = False
        for step_name, selectors in step_selectors:
            if click_first_match(driver, selectors):
                print(f"FLOW_STEP:{step_name}")
                progressed = True
                if is_application_submitted(driver):
                    return "submitted"
                break

        if progressed:
            retries = 0
            time.sleep(CLICK_PAUSE)
            if is_application_submitted(driver):
                return "submitted"
            continue

        print("FLOW_WAIT:no_action")
        if SKIP_ON_UNANSWERED_QUESTIONS and has_unanswered_required_questions(driver):
            manual_state = wait_for_manual_required_answers(driver)
            if manual_state == "submitted":
                return "submitted"
            if manual_state == "resolved":
                retries = 0
                continue
            return "blocked_questions"

        retries += 1
        time.sleep(0.5)

    if is_application_submitted(driver):
        return "submitted"
    if SKIP_ON_UNANSWERED_QUESTIONS and has_unanswered_required_questions(driver):
        return "blocked_questions"
    return "blocked"

def log_match_result(job_key, title, match_result):
    if not SHOW_MATCH_DETAILS:
        return
    print(
        "MATCH:"
        f"key={job_key} "
        f"score={match_result['score']} "
        f"eligible={match_result['eligible']}"
    )
    print(f"MATCH_TITLE:{title}")
    print(f"MATCH_MUST:{match_result['matched_must_have']}")
    print(f"MATCH_PREF:{match_result['matched_preferred']}")
    print(f"MATCH_MISSING:{match_result['missing_must_have']}")
    print(f"MATCH_EXCLUDED:{match_result['excluded_term_hit']}")


def process_job_url(driver, job_entry, idx, stats):
    global LAST_HR_TEXT, LAST_HR_LINK
    job_url = job_entry["url"]
    job_key = job_entry["key"]
    job_title = job_entry["title"]

    print(f"OPEN:{idx}:{job_title}")
    try:
        driver.get(job_url)
        time.sleep(DETAIL_LOAD_WAIT)
    except Exception as e:
        print(f"FAILED:{job_key}:open_job:{e}")
        stats["failed"] += 1
        append_apply_log("Unknown", job_title, job_url, "failed_open_job", "", "")
        return job_key

    company_name, position = extract_company_and_position(driver, job_title)

    if SKIP_EXTERNAL and is_external_apply(driver):
        if SHOW_SKIP_REASONS:
            print(f"SKIP_EXTERNAL:{job_key}")
        stats["skipped_external"] += 1
        append_apply_log(company_name, position, job_url, "skipped_external", "", "")
        return job_key

    if SKIP_ALREADY_APPLIED and is_already_applied(driver):
        if SHOW_SKIP_REASONS:
            print(f"SKIP_APPLIED:{job_key}")
        stats["skipped_applied"] += 1
        append_apply_log(company_name, position, job_url, "skipped_applied", "", "")
        return job_key

    title_text, detail_text = get_job_text_snapshot(driver)
    LAST_HR_TEXT = build_hr_context_text(driver, title_text, detail_text)
    LAST_HR_LINK = extract_hr_profile_link(driver)
    match_result = evaluate_match(title_text, detail_text)
    log_match_result(job_key, title_text, match_result)

    if MATCHING_ENABLED and not match_result["eligible"]:
        if SHOW_SKIP_REASONS:
            print(
                "SKIP_LOW_MATCH:"
                f"score={match_result['score']} "
                f"min={MIN_MATCH_SCORE} "
                f"missing={match_result['missing_must_have']} "
                f"excluded={match_result['excluded_term_hit']}"
            )
        stats["skipped_low_match"] += 1
        append_apply_log(company_name, position, job_url, "skipped_low_match", "", "")
        return job_key

    if not MATCHING_ENABLED and SHOW_MATCH_DETAILS:
        print("MATCH_BYPASS:matching.enabled=False")

    apply_state = click_apply(driver, job_url)
    if apply_state in ("not_found", "not_quick_apply"):
        print(f"SKIP_NO_QUICK_APPLY:{job_key}")
        stats["skipped_no_quick_apply"] += 1
        append_apply_log(company_name, position, job_url, "skipped_no_quick_apply", "", "")
        return job_key

    if apply_state == "visible_but_not_opened":
        print(f"FAILED:{job_key}:quick_apply_transition")
        stats["failed"] += 1
        append_apply_log(company_name, position, job_url, "failed_quick_apply_transition", "", "")
        return job_key

    if not is_on_apply_interface(driver):
        print(f"FAILED:{job_key}:quick_apply_interface_not_opened")
        stats["failed"] += 1
        append_apply_log(company_name, position, job_url, "failed_quick_apply_interface", "", "")
        return job_key

    before_shot = capture_job_screenshot(driver, job_key, "before_apply", phase="before")

    select_resume_if_present(driver, "Agastya Resume.pdf")
    answer_known_employer_questions(driver)

    if not handle_resume_upload(driver):
        print(f"FAILED:{job_key}:resume_upload")
        stats["failed"] += 1
        append_apply_log(company_name, position, job_url, "failed_resume_upload", "", "")
        return job_key

    if not AUTO_SUBMIT_ENABLED:
        print("AUTO_SUBMIT_DISABLED")
        append_apply_log(company_name, position, job_url, "auto_submit_disabled", "", "")
        return job_key

    result = run_quick_apply_flow(driver)
    if result == "submitted":
        print(f"SUBMITTED:{job_key}")
        stats["applied"] += 1
        confirm_deadline = time.time() + 1.0
        while time.time() < confirm_deadline:
            if is_application_submitted(driver):
                break
            time.sleep(0.2)
        shot = capture_job_screenshot(driver, job_key, "submitted", phase="after")
        hr_name, hr_email, hr_contact = extract_hr_details(LAST_HR_TEXT)
        append_apply_log(
            company_name,
            position,
            job_url,
            "submitted",
            shot,
            before_shot,
            hr_name,
            hr_email,
            hr_contact,
            LAST_HR_LINK,
        )
    elif result == "external":
        print(f"SKIP_EXTERNAL:{job_key}")
        stats["skipped_external"] += 1
        append_apply_log(company_name, position, job_url, "skipped_external", "", "")
    elif result == "blocked_questions":
        print(f"FAILED:{job_key}:blocked_questions")
        stats["failed"] += 1
        append_apply_log(company_name, position, job_url, "failed_blocked_questions", "", "")
    else:
        print(f"FAILED:{job_key}:blocked_or_incomplete")
        stats["failed"] += 1
        append_apply_log(company_name, position, job_url, "failed_blocked_or_incomplete", "", "")

    return job_key


def go_to_next_results_page(driver):
    selectors = [
        "//a[@aria-label='Next']",
        "//button[@aria-label='Next']",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
    ]
    for xp in selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            try:
                if not elem.is_displayed() or not elem.is_enabled():
                    continue
                safe_click(driver, elem)
                time.sleep(PAGE_LOAD_WAIT)
                print("NEXT_PAGE")
                return True
            except Exception:
                continue
    return False


def apply_cap_reached(stats):
    return SESSION_APPLY_CAP > 0 and stats["applied"] >= SESSION_APPLY_CAP


def run_continuous(driver):
    stats = {
        "pages": 0,
        "scanned": 0,
        "applied": 0,
        "skipped_external": 0,
        "skipped_applied": 0,
        "skipped_no_quick_apply": 0,
        "skipped_low_match": 0,
        "failed": 0,
    }

    processed_global = set()

    for search_url in SEARCH_URLS:
        if apply_cap_reached(stats):
            print("STOP:apply_cap_reached")
            break

        per_url_start = dict(stats)
        scanned_this_search = 0
        dedup_skipped_this_search = 0

        print(f"SEARCH_START:{search_url}")
        open_jobs_page(driver, search_url)
        pages_in_this_search = 0

        while True:
            if apply_cap_reached(stats):
                print("STOP:apply_cap_reached")
                break

            if MAX_JOBS_PER_RUN > 0 and scanned_this_search >= MAX_JOBS_PER_RUN:
                print("STOP:max_jobs_per_url_reached")
                break

            stats["pages"] += 1
            pages_in_this_search += 1
            results_page_url = driver.current_url

            entries = get_job_entries(driver)
            print(f"PAGE:{stats['pages']}:jobs={len(entries)}")
            if not entries:
                break

            page_processed = 0
            for idx, entry in enumerate(entries, start=1):
                if apply_cap_reached(stats):
                    print("STOP:apply_cap_reached")
                    break

                if MAX_JOBS_PER_RUN > 0 and scanned_this_search >= MAX_JOBS_PER_RUN:
                    print("STOP:max_jobs_per_url_reached")
                    break

                key = entry["key"]
                if not key:
                    continue

                if key in processed_global:
                    dedup_skipped_this_search += 1
                    print(f"SKIP_DUPLICATE:{key}")
                    continue

                if SKIP_ALREADY_APPLIED and entry.get("list_applied"):
                    print(f"SKIP_APPLIED:{key}:list_badge")
                    stats["scanned"] += 1
                    scanned_this_search += 1
                    stats["skipped_applied"] += 1
                    append_apply_log("Unknown", entry.get("title", "Unknown"), entry.get("url", ""), "skipped_applied", "", "")
                    processed_global.add(key)
                    page_processed += 1
                    continue

                stats["scanned"] += 1
                scanned_this_search += 1
                result_key = process_job_url(driver, entry, idx, stats)
                processed_global.add(result_key or key)
                page_processed += 1

                driver.get(results_page_url)
                time.sleep(PAGE_LOAD_WAIT)

            if apply_cap_reached(stats):
                break

            if MAX_JOBS_PER_RUN > 0 and scanned_this_search >= MAX_JOBS_PER_RUN:
                break

            if page_processed == 0:
                break

            if MAX_PAGES_PER_SEARCH > 0 and pages_in_this_search >= MAX_PAGES_PER_SEARCH:
                print("STOP:max_pages_per_search_reached")
                break

            if not go_to_next_results_page(driver):
                break

        per_url_end = dict(stats)
        print(
            "SEARCH_DONE:"
            f"url={search_url} "
            f"scanned={per_url_end['scanned'] - per_url_start['scanned']} "
            f"applied={per_url_end['applied'] - per_url_start['applied']} "
            f"skip_applied={per_url_end['skipped_applied'] - per_url_start['skipped_applied']} "
            f"skip_no_quick_apply={per_url_end['skipped_no_quick_apply'] - per_url_start['skipped_no_quick_apply']} "
            f"failed={per_url_end['failed'] - per_url_start['failed']} "
            f"dedup_skipped={dedup_skipped_this_search}"
        )

    print(
        "DONE:"
        f"pages={stats['pages']} "
        f"scanned={stats['scanned']} "
        f"applied={stats['applied']} "
        f"skip_external={stats['skipped_external']} "
        f"skip_applied={stats['skipped_applied']} "
        f"skip_no_quick_apply={stats['skipped_no_quick_apply']} "
        f"skip_low_match={stats['skipped_low_match']} "
        f"failed={stats['failed']}"
    )

def main():
    validate_config()
    driver = init_driver()

    try:
        if QUICK_APPLY_ONLY:
            print("QUICK_ONLY_MODE:on")
        print("Connected successfully")
        print("Current title:", driver.title)
        print("Current URL:", driver.current_url)

        safe_input("Agar login already ho chuka hai to Enter dabao... ")
        run_continuous(driver)
        safe_input("Script finished. Enter dabao...")
    except Exception as e:
        print("ERROR:", e)
        safe_input("Enter dabao...")


if __name__ == "__main__":
    main()

























