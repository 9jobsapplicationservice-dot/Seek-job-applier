import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.common.exceptions import InvalidSessionIdException, SessionNotCreatedException, TimeoutException, WebDriverException

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
STARTUP_URL = SEARCH_CFG.get("startup_url", "https://www.seek.com.au/")
PERSISTENT_DEBUG_PROFILE_DIR = os.path.join(tempfile.gettempdir(), "seekbot-chrome-profile-persistent")
WAIT_TIMEOUT = int(SEARCH_CFG.get("wait_timeout", 12))
PAGE_LOAD_WAIT = float(SEARCH_CFG.get("page_load_wait", 5))
DETAIL_LOAD_WAIT = float(SEARCH_CFG.get("detail_load_wait", 4))
FLOW_RETRY_LIMIT = int(SEARCH_CFG.get("flow_retry_limit", 4))
CLICK_PAUSE = max(0.15, float(SEARCH_CFG.get("click_pause", 1.5)))
SECURITY_VERIFICATION_TIMEOUT = float(SEARCH_CFG.get("security_verification_timeout_sec", 25))
SECURITY_VERIFICATION_POLL = max(0.5, float(SEARCH_CFG.get("security_verification_poll_sec", 1.5)))
POST_VERIFICATION_SETTLE_WAIT = max(0.5, float(SEARCH_CFG.get("post_verification_settle_wait_sec", 2.0)))
MAX_FLOW_STEPS = int(SEARCH_CFG.get("max_flow_steps", 20))
MAX_PAGES_PER_SEARCH = int(SEARCH_CFG.get("max_pages_per_search", 0))
SALARY_TOLERANCE = int(SEARCH_CFG.get("salary_tolerance", 1000))
RESULTS_PAGE_READY_TIMEOUT = float(SEARCH_CFG.get("results_page_ready_timeout_sec", 20))
DRIVER_COMMAND_TIMEOUT = max(15.0, float(SEARCH_CFG.get("driver_command_timeout_sec", 30)))
DRIVER_PAGELOAD_TIMEOUT = max(15.0, float(SEARCH_CFG.get("driver_pageload_timeout_sec", 30)))

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
MANUAL_FIELD_FILL_WAIT = float(APPLY_CFG.get("manual_field_fill_wait_sec", 5))
MANUAL_FIELD_SETTLE_WAIT = float(APPLY_CFG.get("manual_field_settle_wait_sec", 3))
MANUAL_RESOLUTION_CONFIRM_WAIT = float(APPLY_CFG.get("manual_resolution_confirm_wait_sec", 1))
PROMPT_BEFORE_RUN = bool(APPLY_CFG.get("prompt_before_run", False))
PROMPT_AFTER_RUN = bool(APPLY_CFG.get("prompt_after_run", False))
PROMPT_ON_ERROR = bool(APPLY_CFG.get("prompt_on_error", False))
SCRIPT_EXE = APPLY_CFG.get("script_exe", "Script.exe")
SCRIPT_AU3 = APPLY_CFG.get("script_au3", "Script.au3")

SHOW_MATCH_DETAILS = bool(LOG_CFG.get("show_match_details", True))
SHOW_SKIP_REASONS = bool(LOG_CFG.get("show_skip_reasons", True))
ENABLE_EVALUATION_CSV = bool(LOG_CFG.get("enable_evaluation_csv", False))

RESUME_FILE = RESUME_CFG.get("resume_file", "")
COVER_LETTER_FILE = RESUME_CFG.get("cover_letter_file", "")
PROFILE_KEYWORDS = RESUME_CFG.get("profile_keywords", {})
MUST_HAVE_KEYWORDS = PROFILE_KEYWORDS.get("must_have", [])
PREFERRED_KEYWORDS = PROFILE_KEYWORDS.get("preferred", [])
EXCLUDE_KEYWORDS = RESUME_CFG.get("exclude_keywords", [])

JOB_FILTERS_CFG = RESUME_CFG.get("job_filters", {})
JOB_FILTER_REQUIRED_KEYWORDS = JOB_FILTERS_CFG.get("keywords", [])
JOB_FILTER_EXCLUDE_KEYWORDS = JOB_FILTERS_CFG.get("exclude_keywords", [])
JOB_FILTER_RELATED_ROLES = JOB_FILTERS_CFG.get("related_roles", [])
SEARCH_INTENT_KEYWORDS = []

MUST_HAVE_WEIGHT = int(MATCHING_CFG.get("must_have_weight", 12))
PREFERRED_WEIGHT = int(MATCHING_CFG.get("preferred_weight", 4))
EXCLUDE_PENALTY = int(MATCHING_CFG.get("exclude_penalty", 20))
MUST_HAVE_MISSING_PENALTY = int(MATCHING_CFG.get("must_have_missing_penalty", 10))
MIN_MATCH_SCORE = int(MATCHING_CFG.get("min_match_score", 20))
MATCHING_ENABLED = bool(MATCHING_CFG.get("enabled", False))
REQUIRE_RESUME_ON_STARTUP = bool(RESUME_CFG.get("require_on_startup", False))
JOB_FIT_WEIGHTS = MATCHING_CFG.get(
    "job_fit_weights",
    {
        "search_intent": 25,
        "role_relevance": 20,
        "skills_relevance": 20,
        "experience": 20,
        "salary": 10,
        "location_work_type": 5,
    },
)
MIN_JOB_MATCH_SCORE = int(MATCHING_CFG.get("min_job_match_score", 70))
BORDERLINE_JOB_MATCH_SCORE = int(MATCHING_CFG.get("borderline_job_match_score", 60))
ALLOW_UNKNOWN_SALARY = bool(MATCHING_CFG.get("allow_unknown_salary", True))
ALLOW_RELATED_ROLES = bool(MATCHING_CFG.get("allow_related_roles", True))
ENFORCE_EXPECTED_SALARY_CEILING = bool(MATCHING_CFG.get("enforce_expected_salary_ceiling", False))
STRICT_TITLE_MATCH = bool(MATCHING_CFG.get("strict_title_match", True))
TITLE_MATCH_HARD_GATE = bool(MATCHING_CFG.get("title_match_hard_gate", True))
REQUIRE_TITLE_MATCH_BEFORE_APPLY = bool(MATCHING_CFG.get("require_title_match_before_apply", True))
REVALIDATE_TITLE_BEFORE_SUBMIT = bool(MATCHING_CFG.get("revalidate_title_before_submit", True))
CLASSIFICATION_IS_SEARCH_ONLY = bool(MATCHING_CFG.get("classification_is_search_only", True))
ALLOW_LOOSE_TITLE_MATCH = bool(MATCHING_CFG.get("allow_loose_title_match", False))
SKIP_TITLE_MISMATCH = bool(MATCHING_CFG.get("skip_title_mismatch", True))
EXPERIENCE_STRICT = bool(MATCHING_CFG.get("experience_strict", True))

LOG_DIR = os.path.join(os.getcwd(), "logs")
SCREENSHOT_DIR = os.path.join(LOG_DIR, "screenshots")
CSV_LOG_PATH = os.path.join(LOG_DIR, "applied_jobs.csv")
EVALUATION_CSV_LOG_PATH = os.path.join(LOG_DIR, "job_evaluation_log.csv")
LAST_HR_TEXT = ""
LAST_HR_LINK = ""
TODAY_SUBMITTED_JOB_KEYS = set()
ACTIVE_APPLY_STATE = {"job_key": "", "job_url": "", "apply_url": "", "locked": False}
ACTIVE_JOB_CONTEXT = {"company_name": "", "position": ""}
LAST_JOB_DECISION = {}

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

ROLE_LEVEL_TOKENS = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "mid", "intermediate"
}
ROLE_STOP_TOKENS = {
    "jobs", "job", "in", "all", "nsw", "vic", "qld", "wa", "sa", "act", "nt", "australia",
    "sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "engineering",
    "full", "time", "part", "casual", "contract", "temp"
}
TITLE_STOP_WORDS = {
    "and", "or", "the", "a", "an", "of", "for", "in", "to", "with",
    "on", "at", "by", "from", "as"
}
TITLE_LEVEL_TOKENS = ROLE_LEVEL_TOKENS.union({"experienced"})
TITLE_TOKEN_VARIANTS = {
    "laborer": "labourer",
    "labor": "labour",
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


def parse_version_tuple(value):
    text = str(value or "").strip()
    if not text:
        return tuple()
    parts = []
    for item in re.findall(r"\d+", text):
        try:
            parts.append(int(item))
        except ValueError:
            continue
    return tuple(parts)


def extract_browser_version(debug_data=None):
    info = debug_data if isinstance(debug_data, dict) else {}
    browser_text = str(info.get("Browser") or "").strip()
    match = re.search(r"(\d+(?:\.\d+)+)", browser_text)
    return match.group(1) if match else ""


def find_local_chromedriver(preferred_version=""):
    roots = [
        os.path.join(os.path.expanduser("~"), ".cache", "selenium", "chromedriver", "win64"),
        os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver", "win64"),
    ]
    candidates = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for current_root, _dirs, files in os.walk(root):
            if "chromedriver.exe" in files:
                driver_path = os.path.join(current_root, "chromedriver.exe")
                version_hint = os.path.basename(os.path.dirname(driver_path))
                candidates.append((parse_version_tuple(version_hint), driver_path))
    if not candidates:
        return ""

    preferred_tuple = parse_version_tuple(preferred_version)
    if preferred_tuple:
        same_major = [
            item for item in candidates
            if item[0] and item[0][0] == preferred_tuple[0]
        ]
        if same_major:
            same_major.sort(key=lambda item: item[0], reverse=True)
            return same_major[0][1]

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def start_debug_chrome(first_url):
    chrome_binary = find_chrome_binary()
    if not chrome_binary:
        print("Chrome binary nahi mila; normal WebDriver mode use karenge.")
        return False

    profile_dir = PERSISTENT_DEBUG_PROFILE_DIR
    os.makedirs(profile_dir, exist_ok=True)

    args = [
        chrome_binary,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=Crashpad",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--test-type",
        first_url,
    ]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(120):
        data = get_debug_info(timeout=1)
        if data:
            print("Debug Chrome auto-start ho gaya.")
            print("Browser:", data.get("Browser"))
            return True
        time.sleep(0.5)

    print("Debug Chrome auto-start fail hua; normal WebDriver mode use karenge.")
    return False


def configure_driver_runtime(driver):
    if not driver:
        return driver
    try:
        driver.set_page_load_timeout(DRIVER_PAGELOAD_TIMEOUT)
    except Exception:
        pass
    try:
        driver.set_script_timeout(DRIVER_COMMAND_TIMEOUT)
    except Exception:
        pass
    try:
        if getattr(driver, "command_executor", None) and hasattr(driver.command_executor, "set_timeout"):
            driver.command_executor.set_timeout(DRIVER_COMMAND_TIMEOUT)
    except Exception:
        pass
    return driver


def get_driver_title_safe(driver):
    if not driver:
        return ""
    try:
        return driver.title
    except TimeoutException:
        return ""
    except Exception as exc:
        if is_session_recoverable_error(exc):
            return ""
        raise


def get_driver_current_url_safe(driver):
    if not driver:
        return ""
    try:
        return driver.current_url
    except TimeoutException:
        return ""
    except Exception as exc:
        if is_session_recoverable_error(exc):
            return ""
        raise


def build_debug_driver():
    chrome_options = Options()
    chrome_options.debugger_address = f"{DEBUG_HOST}:{DEBUG_PORT}"
    debug_data = get_debug_info(timeout=2)
    chromedriver_path = find_local_chromedriver(extract_browser_version(debug_data))
    if chromedriver_path and os.path.exists(chromedriver_path):
        return configure_driver_runtime(webdriver.Chrome(service=ChromeService(executable_path=chromedriver_path), options=chrome_options))
    return configure_driver_runtime(webdriver.Chrome(options=chrome_options))


def build_standard_driver(start_url=""):
    chrome_options = Options()
    chrome_binary = find_chrome_binary()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--disable-session-crashed-bubble")
    chrome_options.add_argument("--disable-features=Crashpad")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--remote-debugging-port=0")
    chrome_options.add_argument("--test-type")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--start-maximized")
    chromedriver_path = find_local_chromedriver()
    try:
        if chromedriver_path and os.path.exists(chromedriver_path):
            driver = configure_driver_runtime(webdriver.Chrome(service=ChromeService(executable_path=chromedriver_path), options=chrome_options))
        else:
            driver = configure_driver_runtime(webdriver.Chrome(options=chrome_options))
    except SessionNotCreatedException:
        retry_options = Options()
        if chrome_binary:
            retry_options.binary_location = chrome_binary
        retry_profile_dir = tempfile.mkdtemp(prefix="seekbot-webdriver-profile-")
        retry_options.add_argument(f"--user-data-dir={retry_profile_dir}")
        retry_options.add_argument("--no-first-run")
        retry_options.add_argument("--no-default-browser-check")
        retry_options.add_argument("--disable-session-crashed-bubble")
        retry_options.add_argument("--disable-features=Crashpad")
        retry_options.add_argument("--disable-background-networking")
        retry_options.add_argument("--disable-popup-blocking")
        retry_options.add_argument("--disable-gpu")
        retry_options.add_argument("--disable-dev-shm-usage")
        retry_options.add_argument("--disable-software-rasterizer")
        retry_options.add_argument("--remote-debugging-port=0")
        retry_options.add_argument("--test-type")
        retry_options.add_argument("--disable-extensions")
        retry_options.add_argument("--start-maximized")
        if chromedriver_path and os.path.exists(chromedriver_path):
            driver = configure_driver_runtime(webdriver.Chrome(service=ChromeService(executable_path=chromedriver_path), options=retry_options))
        else:
            driver = configure_driver_runtime(webdriver.Chrome(options=retry_options))
    if start_url:
        driver.get(start_url)
    return driver


class SessionReconnectRequired(RuntimeError):
    pass


def is_session_recoverable_error(exc):
    if isinstance(exc, InvalidSessionIdException):
        return True
    message = normalize_text(str(exc))
    session_markers = [
        "invalid session id",
        "session deleted",
        "disconnected",
        "unable to receive message from renderer",
        "target window already closed",
        "web view not found",
        "failed to establish a new connection",
        "actively refused it",
        "max retries exceeded",
        "forcibly closed by the remote host",
        "connectionreseterror",
        "winerror 10054",
        "httppconnectionpool",
        "httpconnectionpool",
        "newconnectionerror",
        "connection refused",
        "localhost",
    ]
    if isinstance(exc, WebDriverException):
        return any(marker in message for marker in session_markers)
    return any(marker in message for marker in session_markers)


def raise_session_reconnect(exc, context):
    if is_session_recoverable_error(exc):
        raise SessionReconnectRequired(context) from exc
    raise exc


def try_quit_driver(driver):
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def verify_driver_session(driver):
    if not driver:
        return False
    try:
        _ = get_driver_current_url_safe(driver)
        return True
    except Exception as exc:
        if is_session_recoverable_error(exc):
            return False
        return False


def clear_active_apply_state():
    ACTIVE_APPLY_STATE.update({"job_key": "", "job_url": "", "apply_url": "", "locked": False})
    ACTIVE_JOB_CONTEXT.update({"company_name": "", "position": ""})


def set_active_job_context(company_name="", position=""):
    ACTIVE_JOB_CONTEXT["company_name"] = (company_name or "").strip()
    ACTIVE_JOB_CONTEXT["position"] = (position or "").strip()


def build_cover_letter_text(template_text, company_name="", position=""):
    text = str(template_text or "")
    company = (company_name or "").strip()
    role = (position or "").strip()

    if role and company:
        text = re.sub(r"\bposition\s+at\s+Company\b", f"{role} at {company}", text, flags=re.IGNORECASE)
        text = re.sub(r"\bthe\s+position\s+at\s+Company\b", f"the {role} at {company}", text, flags=re.IGNORECASE)
    if company:
        text = re.sub(r"\bCompany\b", company, text, flags=re.IGNORECASE)
    if role:
        text = re.sub(r"\bPosition\b", role, text, flags=re.IGNORECASE)
    return text


def rewrite_cover_letter_for_current_job(existing_text, company_name="", position=""):
    text = build_cover_letter_text(existing_text, company_name, position).strip()
    company = (company_name or "").strip()
    role = (position or "").strip()

    if not text or not company or not role:
        return text

    intro_patterns = [
        r"(i am writing to express my interest in\s+)(?:the\s+)?(.+?)(?:\s+position)?\s+at\s+(.+?)([,\.\n])",
        r"(i am excited to apply for\s+)(?:the\s+)?(.+?)(?:\s+position|\s+role)?\s+at\s+(.+?)([,\.\n])",
        r"(i would like to apply for\s+)(?:the\s+)?(.+?)(?:\s+position|\s+role)?\s+at\s+(.+?)([,\.\n])",
    ]
    replacement = f"\\1the {role} at {company}\\4"

    for pattern in intro_patterns:
        updated, count = re.subn(pattern, replacement, text, count=1, flags=re.IGNORECASE | re.DOTALL)
        if count:
            return updated.strip()

    if company not in text or role.lower() not in text.lower():
        return (
            f"Hi,\n\n"
            f"I am writing to express my interest in the {role} at {company}.\n\n"
            f"{text}"
        ).strip()
    return text


def lock_active_apply_state(job_key="", job_url="", apply_url=""):
    ACTIVE_APPLY_STATE["job_key"] = job_key or ACTIVE_APPLY_STATE.get("job_key", "")
    ACTIVE_APPLY_STATE["job_url"] = job_url or ACTIVE_APPLY_STATE.get("job_url", "")
    ACTIVE_APPLY_STATE["apply_url"] = apply_url or ACTIVE_APPLY_STATE.get("apply_url", "")
    ACTIVE_APPLY_STATE["locked"] = True
    print("APPLY_OPEN:locked")


def refresh_active_apply_state(driver, job_key="", job_url=""):
    if not driver:
        return False
    try:
        current = (get_driver_current_url_safe(driver) or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "refresh_active_apply_state")
    if current and has_open_seek_apply_page(driver):
        ACTIVE_APPLY_STATE["job_key"] = job_key or ACTIVE_APPLY_STATE.get("job_key", "")
        ACTIVE_APPLY_STATE["job_url"] = job_url or ACTIVE_APPLY_STATE.get("job_url", "")
        ACTIVE_APPLY_STATE["apply_url"] = current
        ACTIVE_APPLY_STATE["locked"] = True
        return True
    return False


def detect_and_lock_seek_apply_page(driver, job_key="", job_url="", switch=True):
    if not driver:
        return False

    try:
        if refresh_active_apply_state(driver, job_key=job_key, job_url=job_url):
            return True
    except SessionReconnectRequired:
        raise
    except Exception:
        pass

    if not switch:
        return False

    try:
        handles = driver.window_handles
        current_handle = driver.current_window_handle
    except Exception as exc:
        raise_session_reconnect(exc, "detect_and_lock_seek_apply_page_handles")

    for handle in reversed(handles):
        try:
            driver.switch_to.window(handle)
            if refresh_active_apply_state(driver, job_key=job_key, job_url=job_url):
                return True
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("detect_and_lock_seek_apply_page_state") from exc
            continue

    try:
        driver.switch_to.window(current_handle)
    except Exception:
        pass
    return False


def reattach_debug_driver(driver=None, job_url="", context="session"):
    # Do not quit the attached driver here; with debuggerAddress Chrome can close too.
    debug_data = get_debug_info(timeout=3)
    if not debug_data:
        restart_url = job_url or (SEARCH_URLS[0] if SEARCH_URLS else "")
        print(f"SESSION_RECOVER:restart_debug:{context}")
        if not restart_url or not start_debug_chrome(restart_url):
            print(f"FAILED:session_reconnect:{context}:debug_unavailable")
            return None
        debug_data = get_debug_info(timeout=3)
        if not debug_data:
            print(f"FAILED:session_reconnect:{context}:debug_unavailable")
            return None
    try:
        driver = build_debug_driver()
        _ = driver.current_url
        print("SESSION_RECOVER:reattach")
        print("Browser:", debug_data.get("Browser"))
        resume_url = job_url or (ACTIVE_APPLY_STATE.get("apply_url") if ACTIVE_APPLY_STATE.get("locked") else "")
        if resume_url:
            driver.get(resume_url)
            time.sleep(DETAIL_LOAD_WAIT)
            detect_and_lock_seek_apply_page(driver, job_key=ACTIVE_APPLY_STATE.get("job_key", ""), job_url=ACTIVE_APPLY_STATE.get("job_url", ""))
        return driver
    except Exception as exc:
        print(f"FAILED:session_reconnect:{context}:{exc}")
        return None


def init_driver():
    debug_data = get_debug_info(timeout=3)

    if debug_data:
        print("Debug Chrome running")
        print("Browser:", debug_data.get("Browser"))
        try:
            driver = build_debug_driver()
            if verify_driver_session(driver):
                return driver
            print("WARN: debug driver attach unhealthy; fresh session try karenge")
        except Exception as exc:
            print(f"WARN: debug driver attach failed: {exc}")

    print("Chrome debug mode running nahi hai; auto-start try kar rahe hain...")
    started = start_debug_chrome(STARTUP_URL or (SEARCH_URLS[0] if SEARCH_URLS else "https://www.seek.com.au/"))
    if started and get_debug_info(timeout=2):
        try:
            driver = build_debug_driver()
            if verify_driver_session(driver):
                return driver
            print("WARN: auto-start debug attach unhealthy; fresh session use hoga")
        except Exception as exc:
            print(f"WARN: auto-start debug attach failed: {exc}")

    print("Fresh Chrome session start kiya (debug attach ke bina).")
    return build_standard_driver(STARTUP_URL or "https://www.seek.com.au/")


def normalize_text(value):
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title_text(value):
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[\/|+]+", " ", text)
    text = re.sub(r"[()\[\]{}]", " ", text)
    text = re.sub(r"[-_,.:;]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = []
    for token in re.split(r"\s+", text):
        cleaned = TITLE_TOKEN_VARIANTS.get(token, token)
        if cleaned:
            tokens.append(cleaned)
    return " ".join(tokens).strip()


def tokenize_title_for_matching(value, drop_levels=False, drop_stop_words=False):
    tokens = []
    for token in normalize_title_text(value).split():
        if drop_levels and token in TITLE_LEVEL_TOKENS:
            continue
        if drop_stop_words and token in TITLE_STOP_WORDS:
            continue
        if len(token) <= 1:
            continue
        tokens.append(token)
    return tokens


def choose_best_target_role(role_text, desired_roles):
    desired_roles = collect_string_list(desired_roles)
    if not desired_roles:
        return ""
    role_tokens = set(tokenize_title_for_matching(role_text, drop_levels=True, drop_stop_words=True))
    best_role = desired_roles[0]
    best_score = -1.0
    for desired_role in desired_roles:
        desired_tokens = set(tokenize_title_for_matching(desired_role, drop_levels=True, drop_stop_words=True))
        if not desired_tokens:
            continue
        overlap = len(role_tokens.intersection(desired_tokens))
        score = overlap / max(1, len(desired_tokens))
        if score > best_score:
            best_score = score
            best_role = desired_role
    return best_role


def title_role_match_details(actual_title, role_title, allow_loose=False):
    actual_tokens = tokenize_title_for_matching(actual_title, drop_levels=True, drop_stop_words=True)
    role_tokens = tokenize_title_for_matching(role_title, drop_levels=True, drop_stop_words=True)
    actual_set = set(actual_tokens)
    role_set = set(role_tokens)
    overlap = actual_set.intersection(role_set)
    normalized_actual = normalize_title_text(actual_title)
    normalized_role = normalize_title_text(role_title)
    exact_phrase = bool(normalized_actual and normalized_role and normalized_role in normalized_actual)
    strict_match = bool(role_set) and role_set.issubset(actual_set)
    loose_match = False
    if allow_loose and not strict_match and len(role_set) >= 2:
        missing = role_set.difference(actual_set)
        loose_match = len(missing) <= 1 and len(overlap) / max(1, len(role_set)) >= 0.75
    return {
        "normalized_actual": normalized_actual,
        "normalized_role": normalized_role,
        "actual_tokens": actual_tokens,
        "role_tokens": role_tokens,
        "overlap_tokens": sorted(overlap),
        "strict_match": strict_match,
        "exact_phrase": exact_phrase,
        "loose_match": loose_match,
        "matched": strict_match or loose_match,
    }


def evaluate_target_title(actual_title, required_keywords=None, related_roles=None, filters=None):
    filter_config = filters if isinstance(filters, dict) else JOB_FILTERS_CFG
    desired_roles = collect_string_list(required_keywords if required_keywords is not None else filter_config.get("keywords", JOB_FILTER_REQUIRED_KEYWORDS))
    configured_related_roles = collect_string_list(
        related_roles if related_roles is not None else filter_config.get("related_roles", JOB_FILTER_RELATED_ROLES)
    )
    normalized_title = normalize_title_text(actual_title)
    actual_tokens = tokenize_title_for_matching(actual_title, drop_levels=True, drop_stop_words=True)
    result = {
        "enabled": STRICT_TITLE_MATCH and bool(desired_roles or configured_related_roles),
        "job_title": (actual_title or "").strip(),
        "normalized_job_title": normalized_title,
        "job_title_tokens": actual_tokens,
        "target_role": choose_best_target_role(actual_title, desired_roles),
        "matched_role": "",
        "matched_role_type": "",
        "title_match": True,
        "decision": "CONTINUE",
        "reason": "TITLE_MATCH_NOT_CONFIGURED",
        "checked_roles": desired_roles + configured_related_roles,
        "desired_roles": desired_roles,
        "related_roles": configured_related_roles,
        "allow_related_roles": ALLOW_RELATED_ROLES,
        "allow_loose_title_match": ALLOW_LOOSE_TITLE_MATCH,
        "classification_is_search_only": CLASSIFICATION_IS_SEARCH_ONLY,
        "match_details": {},
    }
    if not result["enabled"]:
        return result

    for role in desired_roles:
        details = title_role_match_details(actual_title, role, allow_loose=ALLOW_LOOSE_TITLE_MATCH)
        if details["matched"]:
            result.update({
                "target_role": role,
                "matched_role": role,
                "matched_role_type": "desired",
                "title_match": True,
                "decision": "CONTINUE",
                "reason": "TARGET_TITLE_MATCH",
                "match_details": details,
            })
            return result

    if ALLOW_RELATED_ROLES:
        for role in configured_related_roles:
            details = title_role_match_details(actual_title, role, allow_loose=ALLOW_LOOSE_TITLE_MATCH)
            if details["matched"]:
                result.update({
                    "target_role": choose_best_target_role(role, desired_roles),
                    "matched_role": role,
                    "matched_role_type": "related",
                    "title_match": True,
                    "decision": "CONTINUE",
                    "reason": "RELATED_ROLE_MATCH",
                    "match_details": details,
                })
                return result

    result.update({
        "title_match": False,
        "decision": "SKIP",
        "reason": "SKIP_TITLE_MISMATCH",
    })
    return result


def log_title_match_result(title_result):
    if not title_result.get("enabled"):
        return
    print(f"JOB TITLE: {title_result.get('job_title', '')}")
    print(f"TARGET ROLE: {title_result.get('target_role', '')}")
    print(f"MATCHED ROLE: {title_result.get('matched_role', '')}")
    print(f"TITLE MATCH: {'TRUE' if title_result.get('title_match') else 'FALSE'}")
    print(f"DECISION: {title_result.get('decision', '')}")
    print(f"REASON: {title_result.get('reason', '')}")


def normalize_role_tokens(value):
    tokens = []
    for token in normalize_text(value).split():
        if token in ROLE_LEVEL_TOKENS or token in ROLE_STOP_TOKENS:
            continue
        if len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def tokenize_for_matching(value, drop_levels=False):
    tokens = []
    for token in normalize_text(value).split():
        if drop_levels and token in ROLE_LEVEL_TOKENS:
            continue
        if token in ROLE_STOP_TOKENS:
            continue
        if len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def build_search_intent_keywords(search_urls):
    keywords = []
    for raw_url in search_urls or []:
        try:
            parsed = urlparse(raw_url)
        except Exception:
            continue
        path_parts = [part for part in parsed.path.split("/") if part]
        for part in path_parts:
            text = unquote(part).replace("-", " ").replace("+", " ")
            text = re.sub(r"\bin\b", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            if "job" in text.lower():
                keywords.append(text)
        query = parse_qs(parsed.query)
        for value in query.get("keywords", []):
            cleaned = normalize_text(unquote(value))
            if cleaned:
                keywords.append(cleaned)
    deduped = []
    seen = set()
    for item in keywords:
        key = normalize_text(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def parse_search_url_context(search_url):
    context = {
        "url": search_url,
        "search_phrases": [],
        "classifications": [],
        "subclassifications": [],
        "locations": [],
        "job_types": [],
        "salary_values": [],
    }
    try:
        parsed = urlparse(search_url or "")
    except Exception:
        return context

    path_parts = [part for part in parsed.path.split("/") if part]
    for part in path_parts:
        cleaned = normalize_text(unquote(part).replace("-", " ").replace("+", " "))
        if not cleaned:
            continue
        lower = cleaned.lower()
        if "jobs" in lower or "job" in lower:
            context["search_phrases"].append(cleaned)
        if lower.startswith("all "):
            context["locations"].append(cleaned)

    query = parse_qs(parsed.query)
    for key in ["keywords", "what", "where"]:
        for value in query.get(key, []):
            cleaned = normalize_text(unquote(value))
            if not cleaned:
                continue
            if key == "where":
                context["locations"].append(cleaned)
            else:
                context["search_phrases"].append(cleaned)

    for value in query.get("classification", []):
        cleaned = normalize_text(unquote(value))
        if cleaned:
            context["classifications"].append(cleaned)
    for value in query.get("subclassification", []):
        cleaned = normalize_text(unquote(value))
        if cleaned:
            context["subclassifications"].append(cleaned)
    for value in query.get("salaryrange", []):
        cleaned = normalize_text(unquote(value))
        if cleaned:
            context["salary_values"].append(cleaned)
    for value in query.get("salarytype", []):
        cleaned = normalize_text(unquote(value))
        if cleaned:
            context["salary_values"].append(cleaned)

    return context


SEARCH_INTENT_KEYWORDS = build_search_intent_keywords(SEARCH_URLS)


def collect_string_list(values):
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raw_values = [values]
    cleaned = []
    for value in raw_values:
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return cleaned


def infer_job_type_signals(text):
    full_text = normalize_text(text)
    signals = []
    mappings = {
        "full time": ["full time", "permanent full time"],
        "part time": ["part time"],
        "contract": ["contract", "fixed term", "fixed-term"],
        "casual": ["casual"],
        "temporary": ["temporary", "temp"],
        "permanent": ["permanent"],
    }
    for canonical, phrases in mappings.items():
        if any(phrase in full_text for phrase in phrases):
            signals.append(canonical)
    return signals


def find_hits(haystack, keywords):
    hits = []
    for raw in keywords:
        key = normalize_text(raw)
        if key and key in haystack:
            hits.append(raw)
    return hits


def find_lenient_hits(haystack, keywords):
    hits = []
    for raw in keywords:
        words = [w for w in normalize_text(raw).split() if len(w) > 2]
        if words and any(w in haystack for w in words):
            hits.append(raw)
    return hits


def role_overlap_score(title_text, detail_text="", role_keywords=None):
    combined_tokens = set(normalize_role_tokens(f"{title_text} {detail_text}"))
    role_keywords = collect_string_list(role_keywords)
    best_score = 0.0
    best_match = ""
    for raw in role_keywords:
        target_tokens = set(normalize_role_tokens(raw))
        if not target_tokens:
            continue
        overlap = combined_tokens.intersection(target_tokens)
        if not overlap:
            continue
        score = len(overlap) / max(1, len(target_tokens))
        if score > best_score:
            best_score = score
            best_match = raw
    return {"score": best_score, "best_match": best_match}


def get_candidate_role_keywords(required_keywords=None):
    base = collect_string_list(required_keywords if required_keywords is not None else JOB_FILTER_REQUIRED_KEYWORDS)
    related = collect_string_list(JOB_FILTER_RELATED_ROLES)
    search_terms = collect_string_list(SEARCH_INTENT_KEYWORDS)
    keywords = []
    seen = set()
    for item in base + related + search_terms:
        key = normalize_text(item)
        if key and key not in seen:
            seen.add(key)
            keywords.append(item)
    return keywords


def get_first_numeric_value(values, default=None):
    for value in collect_string_list(values):
        digits = re.sub(r"[^\d]", "", str(value))
        if digits:
            try:
                return int(digits)
            except ValueError:
                continue
    return default


def extract_salary_numbers(text):
    matches = re.findall(r"\$?\s*(\d[\d,]{2,})", text or "")
    numbers = []
    for raw in matches:
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            continue
        try:
            numbers.append(int(digits))
        except ValueError:
            continue
    return numbers


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
}


def replace_number_words(text):
    output = str(text or "")
    for word, value in NUMBER_WORDS.items():
        output = re.sub(rf"\b{word}\b", str(value), output, flags=re.IGNORECASE)
    return output


def extract_experience_requirements(title_text, detail_text):
    full_text = re.sub(r"\s+", " ", replace_number_words(f"{title_text} {detail_text}").lower()).strip()
    result = {
        "mentioned": False,
        "minimum": None,
        "maximum": None,
        "raw": "",
        "seniority_signal": "",
    }
    seniority_text = normalize_text(title_text)
    for token in ["principal", "staff", "head of", "director", "manager", "lead", "senior"]:
        if token in seniority_text:
            result["seniority_signal"] = token
            break

    patterns = [
        r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:\+)?\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
        r"(?:minimum of |minimum |at least )(\d+)\s*(?:\+|plus)?\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
        r"(\d+)\s*(?:\+|plus)?\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
        r"experience\s+(?:of\s+)?(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:years?|yrs?)",
        r"experience\s+(?:of\s+)?(?:minimum of |minimum |at least )?(\d+)\s*(?:\+|plus)?\s*(?:years?|yrs?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text)
        if not match:
            continue
        groups = [int(item) for item in match.groups() if item is not None]
        if not groups:
            continue
        result["mentioned"] = True
        result["minimum"] = groups[0]
        if len(groups) > 1:
            result["maximum"] = groups[1]
        elif "at least" not in match.group(0) and "minimum" not in match.group(0) and "+" not in match.group(0) and "plus" not in match.group(0):
            result["maximum"] = groups[0]
        result["raw"] = match.group(0)
        return result
    return result


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


def evaluate_configured_job_filters(title_text, detail_text, required_keywords=None, exclude_keywords=None, filters=None):
    full_text = normalize_text(f"{title_text} {detail_text}")
    filter_config = filters if isinstance(filters, dict) else JOB_FILTERS_CFG
    if required_keywords is None:
        required_keywords = JOB_FILTER_REQUIRED_KEYWORDS
    else:
        required_keywords = collect_string_list(required_keywords)

    if exclude_keywords is None:
        exclude_keywords = JOB_FILTER_EXCLUDE_KEYWORDS
    else:
        exclude_keywords = collect_string_list(exclude_keywords)

    title_match_result = evaluate_target_title(
        title_text,
        required_keywords=required_keywords,
        related_roles=filter_config.get("related_roles", JOB_FILTER_RELATED_ROLES),
        filters=filter_config,
    )
    role_keywords = get_candidate_role_keywords(required_keywords)
    role_overlap = role_overlap_score(title_text, detail_text, role_keywords)
    excluded_hits = find_hits(full_text, exclude_keywords)

    required_hits = [title_match_result["matched_role"]] if title_match_result.get("title_match") and title_match_result.get("matched_role") else []
    missing_required = [] if title_match_result.get("title_match") else list(required_keywords)
    rejection_reasons = []

    enabled = bool(required_keywords or exclude_keywords)
    has_required_match = True if not required_keywords else bool(required_hits)
    if STRICT_TITLE_MATCH and title_match_result.get("enabled"):
        has_required_match = bool(title_match_result.get("title_match"))
        if not has_required_match and SKIP_TITLE_MISMATCH:
            rejection_reasons.append("title mismatch")
    elif not has_required_match and role_overlap["score"] >= 0.5:
        has_required_match = True
        if role_overlap["best_match"]:
            required_hits.append(role_overlap["best_match"])
    if not has_required_match and "title mismatch" not in rejection_reasons:
        rejection_reasons.append("required keywords not matched")
    if excluded_hits:
        rejection_reasons.append("excluded keyword matched")

    configured_job_types = [normalize_text(item) for item in collect_string_list(filter_config.get("job_type", []))]
    actual_job_types = infer_job_type_signals(full_text)
    job_type_hits = [item for item in configured_job_types if item in actual_job_types]
    strict_job_type = bool(filter_config.get("strict_job_type", False))
    if configured_job_types and actual_job_types and not job_type_hits and strict_job_type:
        rejection_reasons.append("job type not matched")

    configured_locations = collect_string_list(filter_config.get("location", []))
    location_hits = find_lenient_hits(full_text, configured_locations)
    if configured_locations and not location_hits:
        rejection_reasons.append("location not matched")

    salary_numbers = extract_salary_numbers(f"{title_text} {detail_text}")
    configured_experience = get_first_numeric_value(filter_config.get("experience", []))
    if configured_experience:
        experience_info = extract_experience_requirements(title_text, detail_text)
        if experience_info.get("mentioned") and experience_info.get("minimum") is not None:
            required_experience = int(experience_info["minimum"])
            if required_experience > configured_experience:
                if experience_info.get("maximum") is not None and experience_info["maximum"] != required_experience:
                    rejection_reasons.append(
                        f"experience range {required_experience}-{int(experience_info['maximum'])} exceeds user exp {configured_experience}"
                    )
                else:
                    rejection_reasons.append(
                        f"required experience {required_experience} exceeds user exp {configured_experience}"
                    )

    minimum_salary = get_first_numeric_value(filter_config.get("current_salary", []))
    expected_salary = get_first_numeric_value(filter_config.get("expected_salary", []))
    enforce_expected_salary_ceiling = bool(
        filter_config.get("enforce_expected_salary_ceiling", ENFORCE_EXPECTED_SALARY_CEILING)
    )
    if minimum_salary and minimum_salary < 1000:
        minimum_salary *= 1000
    if expected_salary and expected_salary < 1000:
        expected_salary *= 1000

    annual_salaries = [n for n in salary_numbers if 40000 <= n <= 300000]
    if annual_salaries:
        job_min_salary = min(annual_salaries)
        job_max_salary = max(annual_salaries)
        if minimum_salary is not None and job_max_salary < (minimum_salary - SALARY_TOLERANCE):
            rejection_reasons.append(f"job salary max {job_max_salary} is below min salary {minimum_salary}")
        if enforce_expected_salary_ceiling and expected_salary is not None and job_min_salary > (expected_salary + SALARY_TOLERANCE):
            rejection_reasons.append(f"job salary min {job_min_salary} is above expected max salary {expected_salary}")

    eligible = not rejection_reasons

    return {
        "enabled": enabled,
        "eligible": eligible,
        "matched_required": required_hits,
        "missing_required": missing_required,
        "excluded_hits": excluded_hits,
        "matched_job_type": job_type_hits,
        "matched_location": location_hits,
        "salary_numbers": salary_numbers,
        "role_overlap_score": role_overlap["score"],
        "related_role_match": title_match_result.get("matched_role") if title_match_result.get("matched_role_type") == "related" else role_overlap["best_match"],
        "title_match": title_match_result.get("title_match"),
        "target_role": title_match_result.get("target_role", ""),
        "matched_role": title_match_result.get("matched_role", ""),
        "title_match_reason": title_match_result.get("reason", ""),
        "title_match_result": title_match_result,
        "rejection_reasons": rejection_reasons,
    }


def build_candidate_profile(filters=None):
    filter_config = filters if isinstance(filters, dict) else JOB_FILTERS_CFG
    return {
        "target_roles": collect_string_list(filter_config.get("keywords", JOB_FILTER_REQUIRED_KEYWORDS)),
        "related_roles": collect_string_list(filter_config.get("related_roles", JOB_FILTER_RELATED_ROLES)),
        "skills": collect_string_list(MUST_HAVE_KEYWORDS) + collect_string_list(PREFERRED_KEYWORDS),
        "years_experience": get_first_numeric_value(filter_config.get("experience", [])),
        "current_salary": get_first_numeric_value(filter_config.get("current_salary", [])),
        "target_salary": get_first_numeric_value(filter_config.get("expected_salary", [])),
        "minimum_salary": get_first_numeric_value(filter_config.get("current_salary", [])),
        "maximum_salary": get_first_numeric_value(filter_config.get("expected_salary", [])),
        "location_preferences": collect_string_list(filter_config.get("location", [])),
        "employment_types": collect_string_list(filter_config.get("job_type", [])),
        "work_authorization": collect_string_list(filter_config.get("visa_type", [])),
        "resume_path": RESUME_FILE,
    }


def infer_profile_seniority(target_roles, years_experience):
    role_text = normalize_text(" ".join(collect_string_list(target_roles)))
    for token in ["director", "head", "principal", "lead", "manager", "senior", "junior", "graduate"]:
        if token in role_text:
            return token
    if years_experience is None:
        return ""
    if years_experience <= 1:
        return "entry"
    if years_experience <= 3:
        return "junior"
    if years_experience <= 6:
        return "mid"
    return "senior"


def build_client_context(search_urls=None, filters=None, profile_keywords=None, client_id=""):
    filter_config = filters if isinstance(filters, dict) else JOB_FILTERS_CFG
    profile = build_candidate_profile(filter_config)
    keyword_config = profile_keywords if isinstance(profile_keywords, dict) else PROFILE_KEYWORDS
    parsed_searches = [parse_search_url_context(url) for url in (search_urls or SEARCH_URLS or [])]
    search_queries = []
    classifications = []
    locations = collect_string_list(filter_config.get("location", []))
    for item in parsed_searches:
        search_queries.extend(item.get("search_phrases", []))
        classifications.extend(item.get("classifications", []))
        classifications.extend(item.get("subclassifications", []))
        locations.extend(item.get("locations", []))

    target_roles = collect_string_list(filter_config.get("keywords", JOB_FILTER_REQUIRED_KEYWORDS))
    historical_roles = collect_string_list(filter_config.get("related_roles", JOB_FILTER_RELATED_ROLES))
    skills = collect_string_list(keyword_config.get("must_have", [])) + collect_string_list(keyword_config.get("preferred", []))
    role_families = []
    seen = set()
    for source in target_roles + historical_roles + search_queries:
        tokens = tokenize_for_matching(source, drop_levels=True)
        if not tokens:
            continue
        family = " ".join(tokens[:3])
        if family and family not in seen:
            seen.add(family)
            role_families.append(family)

    normalized_queries = []
    query_seen = set()
    for value in search_queries + target_roles:
        normalized = normalize_text(value)
        if normalized and normalized not in query_seen:
            query_seen.add(normalized)
            normalized_queries.append(normalized)

    return {
        "client_id": client_id or "|".join(normalized_queries[:3]),
        "search_queries": normalized_queries,
        "search_url_contexts": parsed_searches,
        "target_roles": target_roles,
        "historical_roles": historical_roles,
        "role_families": role_families,
        "skills": skills,
        "industries": classifications,
        "years_experience": profile.get("years_experience"),
        "seniority": infer_profile_seniority(target_roles + historical_roles, profile.get("years_experience")),
        "salary_target": profile.get("target_salary"),
        "salary_minimum": profile.get("minimum_salary"),
        "locations": collect_string_list(locations),
        "job_types": collect_string_list(filter_config.get("job_type", [])),
        "work_rights": collect_string_list(filter_config.get("visa_type", [])),
        "qualifications": collect_string_list(filter_config.get("qualification", [])),
        "licenses": collect_string_list(filter_config.get("licenses", [])),
        "profile": profile,
    }


def compute_token_overlap_ratio(job_tokens, reference_tokens):
    reference = set(tokenize_for_matching(" ".join(collect_string_list(reference_tokens)), drop_levels=True))
    if not reference:
        return 0.0
    overlap = set(job_tokens).intersection(reference)
    return len(overlap) / max(1, len(reference))


def infer_job_role_relationship(client_context, title_text, detail_text):
    job_title_tokens = tokenize_for_matching(title_text, drop_levels=True)
    job_detail_tokens = tokenize_for_matching(detail_text, drop_levels=True)
    combined_tokens = job_title_tokens + job_detail_tokens

    search_ratio = compute_token_overlap_ratio(combined_tokens, client_context.get("search_queries", []))
    role_ratio = compute_token_overlap_ratio(
        combined_tokens,
        client_context.get("target_roles", []) + client_context.get("historical_roles", []) + client_context.get("role_families", []),
    )
    skill_ratio = compute_token_overlap_ratio(combined_tokens, client_context.get("skills", []))
    industry_ratio = compute_token_overlap_ratio(combined_tokens, client_context.get("industries", []))
    responsibility_ratio = max(role_ratio, skill_ratio, industry_ratio)
    overall = max(search_ratio, role_ratio, (0.6 * skill_ratio) + (0.4 * industry_ratio))

    if overall >= 0.7:
        relationship = "DIRECT"
    elif overall >= 0.45:
        relationship = "RELATED"
    elif overall >= 0.25:
        relationship = "TRANSFERABLE"
    else:
        relationship = "UNRELATED"

    same_domain = relationship != "UNRELATED"
    confidence = clamp_score(overall * 100)
    best_search = ""
    best_score = 0.0
    for query in client_context.get("search_queries", []):
        ratio = compute_token_overlap_ratio(combined_tokens, [query])
        if ratio > best_score:
            best_score = ratio
            best_search = query

    return {
        "role_family": client_context.get("role_families", [""])[0] if client_context.get("role_families") else "",
        "same_professional_domain": same_domain,
        "relationship": relationship,
        "reason": f"search={search_ratio:.2f} role={role_ratio:.2f} skill={skill_ratio:.2f} industry={industry_ratio:.2f}",
        "confidence": confidence,
        "search_ratio": search_ratio,
        "role_ratio": role_ratio,
        "skill_ratio": skill_ratio,
        "industry_ratio": industry_ratio,
        "responsibility_ratio": responsibility_ratio,
        "best_matching_search": best_search,
        "best_matching_search_score": best_score,
    }


def clamp_score(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, int(round(value))))


def score_component(weight, ratio):
    return clamp_score(weight * max(0.0, min(1.0, float(ratio))), 0, int(weight))


def normalize_reason_code(reason):
    reason_text = normalize_text(reason).lower()
    if "required experience" in reason_text or "experience range" in reason_text:
        return "SKIP_EXPERIENCE_TOO_HIGH"
    if "title mismatch" in reason_text:
        return "SKIP_TITLE_MISMATCH"
    if "required keywords not matched" in reason_text:
        return "SKIP_ROLE_IRRELEVANT"
    if "excluded keyword matched" in reason_text:
        return "SKIP_EXCLUDED_KEYWORD"
    if "job type not matched" in reason_text:
        return "SKIP_JOB_TYPE"
    if "location not matched" in reason_text:
        return "SKIP_LOCATION"
    if "salary" in reason_text:
        return "SKIP_SALARY_MISMATCH"
    return "SKIP_FILTER_RULE"


def build_job_decision(
    job_key,
    job_url,
    company_name,
    title_text,
    detail_text,
    filter_result,
    match_result,
    *,
    list_quick_apply=False,
    already_applied=False,
    duplicate=False,
    external_apply=False,
    client_context=None,
):
    client_context = client_context or build_client_context()
    candidate_profile = client_context.get("profile") or build_candidate_profile()
    experience_info = extract_experience_requirements(title_text, detail_text)
    annual_salaries = [n for n in filter_result.get("salary_numbers", []) if 40000 <= n <= 300000]
    salary_min = min(annual_salaries) if annual_salaries else None
    salary_max = max(annual_salaries) if annual_salaries else None

    hard_fail_reasons = []
    title_match_result = filter_result.get("title_match_result") or evaluate_target_title(title_text, filters=JOB_FILTERS_CFG)
    if duplicate:
        hard_fail_reasons.append("SKIP_DUPLICATE")
    if already_applied:
        hard_fail_reasons.append("SKIP_ALREADY_APPLIED")
    if external_apply:
        hard_fail_reasons.append("SKIP_EXTERNAL_APPLY")
    if TITLE_MATCH_HARD_GATE and title_match_result.get("enabled") and not title_match_result.get("title_match"):
        hard_fail_reasons.append("SKIP_TITLE_MISMATCH")
    for reason in filter_result.get("rejection_reasons", []):
        hard_fail_reasons.append(normalize_reason_code(reason))

    relationship = infer_job_role_relationship(client_context, title_text, detail_text)
    search_overlap = role_overlap_score(
        title_text,
        detail_text,
        client_context.get("search_queries", []) or SEARCH_INTENT_KEYWORDS or JOB_FILTER_REQUIRED_KEYWORDS,
    )
    role_overlap = role_overlap_score(
        title_text,
        detail_text,
        client_context.get("target_roles", []) + client_context.get("historical_roles", []) + client_context.get("role_families", []),
    )
    search_ratio = max(search_overlap.get("score", 0.0), relationship.get("search_ratio", 0.0))
    if filter_result.get("matched_required"):
        search_ratio = max(search_ratio, 0.85)
    role_ratio = max(role_overlap.get("score", 0.0), relationship.get("role_ratio", 0.0), relationship.get("responsibility_ratio", 0.0))
    if ALLOW_RELATED_ROLES and relationship.get("relationship") in ("DIRECT", "RELATED") and role_ratio < 0.7:
        role_ratio = max(role_ratio, 0.7)

    has_skill_config = bool(MUST_HAVE_KEYWORDS or PREFERRED_KEYWORDS)
    if has_skill_config:
        matched_score = len(match_result.get("matched_must_have", [])) + (0.5 * len(match_result.get("matched_preferred", [])))
        possible_score = len(MUST_HAVE_KEYWORDS) + (0.5 * len(PREFERRED_KEYWORDS))
        skill_ratio = min(1.0, matched_score / max(1.0, possible_score))
    else:
        skill_ratio = max(relationship.get("skill_ratio", 0.0), 0.7 if relationship.get("relationship") in ("DIRECT", "RELATED") else 0.0)

    candidate_exp = candidate_profile.get("years_experience")
    if any(code == "SKIP_EXPERIENCE_TOO_HIGH" for code in hard_fail_reasons):
        experience_ratio = 0.0
        experience_match = False
    elif experience_info.get("mentioned"):
        required_min = experience_info.get("minimum")
        experience_match = required_min is None or candidate_exp is None or candidate_exp >= required_min
        experience_ratio = 1.0 if experience_match else 0.0
    else:
        experience_match = True
        experience_ratio = 0.65 if EXPERIENCE_STRICT else 0.75

    if salary_min is None and salary_max is None:
        salary_match = "UNKNOWN"
        salary_ratio = 0.6 if ALLOW_UNKNOWN_SALARY else 0.0
    elif any(code == "SKIP_SALARY_MISMATCH" for code in hard_fail_reasons):
        salary_match = "FAIL"
        salary_ratio = 0.0
    else:
        salary_match = "PASS"
        salary_ratio = 1.0

    location_configured = bool(candidate_profile.get("location_preferences"))
    job_type_configured = bool(candidate_profile.get("employment_types"))
    location_ok = (not location_configured) or bool(filter_result.get("matched_location"))
    job_type_ok = (not job_type_configured) or bool(filter_result.get("matched_job_type"))
    if location_ok and job_type_ok:
        location_ratio = 1.0
    elif location_ok or job_type_ok:
        location_ratio = 0.5
    else:
        location_ratio = 0.0

    breakdown = {
        "search_intent": score_component(JOB_FIT_WEIGHTS.get("search_intent", 25), search_ratio),
        "role_relevance": score_component(JOB_FIT_WEIGHTS.get("role_relevance", 20), max(role_ratio, relationship.get("responsibility_ratio", 0.0))),
        "skills_relevance": score_component(JOB_FIT_WEIGHTS.get("skills_relevance", 20), skill_ratio),
        "experience": score_component(JOB_FIT_WEIGHTS.get("experience", 20), experience_ratio),
        "salary": score_component(JOB_FIT_WEIGHTS.get("salary", 10), salary_ratio),
        "location_work_type": score_component(JOB_FIT_WEIGHTS.get("location_work_type", 5), location_ratio),
    }
    total_score = clamp_score(sum(breakdown.values()))

    hard_fail = bool(hard_fail_reasons)
    related_role_strong = role_ratio >= 0.7 or relationship.get("relationship") in ("DIRECT", "RELATED")
    if not relationship.get("same_professional_domain") and "SKIP_ROLE_IRRELEVANT" not in hard_fail_reasons:
        hard_fail_reasons.append("SKIP_LOW_RELEVANCE")
        hard_fail = True
    if hard_fail:
        fit_decision = "INELIGIBLE"
        final_action = hard_fail_reasons[0]
        decision_reason = hard_fail_reasons[0]
    elif total_score >= MIN_JOB_MATCH_SCORE:
        fit_decision = "STRONG_MATCH" if total_score >= 85 else "ELIGIBLE"
        decision_reason = "FIT_SCORE_PASS"
    elif total_score >= BORDERLINE_JOB_MATCH_SCORE and related_role_strong and experience_match:
        fit_decision = "BORDERLINE"
        decision_reason = "FIT_BORDERLINE_RELATED_ROLE"
    else:
        fit_decision = "INELIGIBLE"
        final_action = "SKIP_LOW_RELEVANCE"
        decision_reason = "SKIP_LOW_RELEVANCE"

    application_method_status = "QUICK_APPLY_HINT" if list_quick_apply else "UNKNOWN"
    if not hard_fail and fit_decision in ("STRONG_MATCH", "ELIGIBLE", "BORDERLINE"):
        final_action = "APPLY"
    elif not hard_fail and fit_decision == "INELIGIBLE":
        final_action = "SKIP_LOW_RELEVANCE"
    elif hard_fail and 'final_action' not in locals():
        final_action = hard_fail_reasons[0]

    return {
        "job_id": job_key,
        "job_title": title_text,
        "company": company_name,
        "job_url": job_url,
        "client_search_intent": client_context.get("search_queries", []),
        "detected_role_family": relationship.get("role_family", ""),
        "best_matching_search": relationship.get("best_matching_search", ""),
        "search_intent_match": breakdown["search_intent"],
        "responsibility_match": breakdown["role_relevance"],
        "role_match": breakdown["role_relevance"],
        "skill_match": breakdown["skills_relevance"],
        "experience_score": breakdown["experience"],
        "salary_score": breakdown["salary"],
        "location_job_type_score": breakdown["location_work_type"],
        "experience_match": experience_match,
        "experience_required_min": experience_info.get("minimum"),
        "experience_required_max": experience_info.get("maximum"),
        "candidate_experience": candidate_exp,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_match": salary_match,
        "salary_status": salary_match,
        "location_match": location_ok,
        "job_type_match": job_type_ok,
        "target_role": title_match_result.get("target_role", ""),
        "matched_role": title_match_result.get("matched_role", ""),
        "title_match": title_match_result.get("title_match", True),
        "title_match_reason": title_match_result.get("reason", ""),
        "fit_decision": fit_decision,
        "quick_apply_available": bool(list_quick_apply),
        "application_method_status": application_method_status,
        "hard_fail": hard_fail,
        "hard_fail_reason": "|".join(hard_fail_reasons),
        "hard_fail_reasons": hard_fail_reasons,
        "duplicate": duplicate,
        "total_score": total_score,
        "decision": "APPLY" if final_action == "APPLY" else "SKIP",
        "final_action": final_action,
        "decision_reason": decision_reason,
        "breakdown": breakdown,
        "role_overlap_score": filter_result.get("role_overlap_score", 0),
        "related_role_match": filter_result.get("related_role_match", ""),
        "search_overlap_score": search_overlap.get("score", 0),
        "relationship": relationship.get("relationship", ""),
        "relationship_reason": relationship.get("reason", ""),
        "confidence": relationship.get("confidence", 0),
        "client_context": client_context,
        "candidate_profile": candidate_profile,
    }


def log_job_decision(job_key, decision):
    print(
        "DECISION:"
        f"key={job_key} "
        f"fit={decision.get('fit_decision')} "
        f"final_action={decision.get('final_action')} "
        f"score={decision['total_score']} "
        f"reason={decision['decision_reason']}"
    )
    print(
        "DECISION_BREAKDOWN:"
        f"search={decision['breakdown']['search_intent']} "
        f"role={decision['breakdown']['role_relevance']} "
        f"skills={decision['breakdown']['skills_relevance']} "
        f"exp={decision['breakdown']['experience']} "
        f"salary={decision['breakdown']['salary']} "
        f"location={decision['breakdown']['location_work_type']}"
    )
    print(
        "DECISION_META:"
        f"relationship={decision.get('relationship', '')} "
        f"quick_apply={decision.get('quick_apply_available')} "
        f"application_method={decision.get('application_method_status', '')} "
        f"confidence={decision.get('confidence', 0)}"
    )
    print(
        "DECISION_TITLE:"
        f"target_role={decision.get('target_role', '')} "
        f"matched_role={decision.get('matched_role', '')} "
        f"title_match={decision.get('title_match', True)} "
        f"title_reason={decision.get('title_match_reason', '')}"
    )
    if decision.get("hard_fail"):
        print(f"HARD_FAIL:{decision.get('hard_fail_reason', '')}")


def log_filter_result(job_key, title, filter_result):
    if not filter_result.get("enabled"):
        return
    print(f"CONFIG_FILTER:key={job_key} eligible={filter_result['eligible']}")
    print(f"FILTER_TITLE:{title}")
    print(f"FILTER_MATCHED:{filter_result['matched_required']}")
    print(f"FILTER_TITLE_MATCH:{filter_result.get('title_match')}")
    print(f"FILTER_TARGET_ROLE:{filter_result.get('target_role', '')}")
    print(f"FILTER_MATCHED_ROLE:{filter_result.get('matched_role', '')}")
    print(f"FILTER_MISSING:{filter_result['missing_required']}")
    print(f"FILTER_EXCLUDED:{filter_result['excluded_hits']}")
    print(f"FILTER_LOCATION:{filter_result.get('matched_location', [])}")
    print(f"FILTER_JOB_TYPE:{filter_result.get('matched_job_type', [])}")
    print(f"FILTER_REASONS:{filter_result.get('rejection_reasons', [])}")


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
    wait_for_security_verification(driver)
    wait_for_results_page_ready(driver)
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
        "//*[@data-testid='job-card-title']//a[contains(@href, '/job/')]",
        "//a[contains(@data-testid, 'job-card-title') and contains(@href, '/job/')]",
        "//a[contains(@aria-label, 'Job') and contains(@href, '/job/')]",
        "//article//a[contains(@href, '/job/')]",
        "//a[contains(@href, '/job/') and not(contains(@href, '/apply'))]",
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
            list_quick_apply = False
            try:
                card = elem.find_element(By.XPATH, "./ancestor::article[1]")
                card_text = normalize_text(card.text)
                list_applied = (
                    " applied " in f" {card_text} "
                    or "application sent" in card_text
                    or "you ve applied" in card_text
                )
                list_quick_apply = "quick apply" in card_text or "apply with seek" in card_text
            except Exception:
                list_applied = False
                list_quick_apply = False

            raw.append(
                {"key": key, "url": href, "title": title, "list_applied": list_applied, "list_quick_apply": list_quick_apply}
            )
        if raw:
            break

    dedup = {}
    for item in raw:
        dedup[item["key"]] = item
    if dedup:
        entries = list(dedup.values())
        entries.sort(key=lambda item: (not item.get("list_quick_apply", False), item.get("title", "")))
        return entries

    try:
        current_url = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "get_job_entries_current")

    if classify_apply_target(current_url, current_url) == "seek_job":
        title = "Untitled Job"
        title_selectors = [
            "//*[@data-automation='job-detail-title']",
            "//h1",
        ]
        for xp in title_selectors:
            try:
                elems = driver.find_elements(By.XPATH, xp)
            except Exception as exc:
                raise_session_reconnect(exc, "get_job_entries_title_find")
            for elem in elems:
                text = (elem.text or "").strip()
                if text:
                    title = text
                    break
            if title != "Untitled Job":
                break
        key = extract_job_key_from_href(current_url)
        if key:
            return [{
                "key": key,
                "url": current_url,
                "title": title,
                "list_applied": False,
                "list_quick_apply": False,
            }]
    return []


def is_results_page_ready(driver):
    try:
        title = normalize_text(driver.title)
    except Exception as exc:
        raise_session_reconnect(exc, "is_results_page_ready_title")
    if "just a moment" in title or "performing security verification" in title:
        return False
    try:
        if driver.find_elements(By.XPATH, "//a[@data-automation='jobTitle' and contains(@href, '/job/')]"):
            return True
        if driver.find_elements(By.XPATH, "//article//a[contains(@href, '/job/')]"):
            return True
    except Exception as exc:
        raise_session_reconnect(exc, "is_results_page_ready_find")
    return False


def wait_for_results_page_ready(driver, timeout=None):
    wait_timeout = RESULTS_PAGE_READY_TIMEOUT if timeout is None else max(0, float(timeout))
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        wait_for_security_verification(driver, timeout=5)
        if is_results_page_ready(driver):
            return True
        time.sleep(0.5)
    return is_results_page_ready(driver)


def is_external_apply(driver):
    try:
        current = (driver.current_url or "").strip()
        if current and classify_apply_target(current, current) == "external_handoff":
            return True
    except Exception as exc:
        raise_session_reconnect(exc, "is_external_apply_current")
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


def confirm_application_submission(driver, timeout=10):
    deadline = time.time() + max(1, float(timeout))
    while time.time() < deadline:
        if is_application_submitted(driver) or is_already_applied(driver):
            return True
        time.sleep(0.25)
    return is_application_submitted(driver) or is_already_applied(driver)


def verify_submission_artifacts(job_url, before_screenshot_path="", after_screenshot_path=""):
    issues = []
    before_ok = bool(before_screenshot_path and os.path.exists(before_screenshot_path) and os.path.getsize(before_screenshot_path) > 0)
    after_ok = bool(after_screenshot_path and os.path.exists(after_screenshot_path) and os.path.getsize(after_screenshot_path) > 0)
    row_count = count_applied_rows_for_job(job_url)
    csv_ok = row_count == 1

    if not before_ok:
        issues.append("missing_before_screenshot")
    if not after_ok:
        issues.append("missing_after_screenshot")
    if row_count == 0:
        issues.append("missing_applied_csv_row")
    elif row_count > 1:
        issues.append("duplicate_applied_csv_row")

    return {
        "ok": not issues,
        "before_ok": before_ok,
        "after_ok": after_ok,
        "csv_ok": csv_ok,
        "row_count": row_count,
        "issues": issues,
    }


def is_security_verification_page(driver):
    try:
        current = (driver.current_url or "").strip().lower()
        title = (driver.title or "").strip().lower()
    except Exception as exc:
        raise_session_reconnect(exc, "is_security_verification_page")

    if not is_seek_domain(current):
        return False

    page_text = ""
    try:
        page_text = normalize_text(driver.find_element(By.TAG_NAME, "body").text)
    except Exception:
        page_text = ""

    combined = f"{title} {page_text} {current}"
    markers = [
        "performing security verification",
        "verification successful waiting for",
        "security service to protect against malicious bots",
        "verify you are a human",
        "checking if the site connection is secure",
    ]
    return any(marker in combined for marker in markers)


def wait_for_security_verification(driver, timeout=None):
    wait_timeout = SECURITY_VERIFICATION_TIMEOUT if timeout is None else max(0, float(timeout))
    deadline = time.time() + wait_timeout
    verification_seen = False

    while True:
        if not is_security_verification_page(driver):
            if verification_seen:
                print("SECURITY_CHECK:cleared")
                time.sleep(POST_VERIFICATION_SETTLE_WAIT)
            return verification_seen

        if not verification_seen:
            print("SECURITY_CHECK:waiting")
            verification_seen = True

        if wait_timeout <= 0 or time.time() >= deadline:
            print("SECURITY_CHECK:timeout")
            return verification_seen

        time.sleep(SECURITY_VERIFICATION_POLL)


def prepare_for_manual_login(driver):
    if not driver:
        return
    target_url = STARTUP_URL or "https://www.seek.com.au/"
    try:
        current = (driver.current_url or "").strip()
    except Exception:
        current = ""
    if current.rstrip("/") == target_url.rstrip("/"):
        return
    driver.get(target_url)
    wait_for_security_verification(driver)
    time.sleep(PAGE_LOAD_WAIT)

def is_on_apply_interface(driver):
    current = (driver.current_url or "").lower()
    if is_seek_domain(current) and "/apply" in current:
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


def is_review_submit_page(driver):
    try:
        current = (driver.current_url or "").lower()
        if "/apply/review" in current:
            return True
    except Exception as exc:
        raise_session_reconnect(exc, "is_review_submit_page_url")

    submit_checks = [
        "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//button[@type='submit'][.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//button[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
        "//*[@data-testid='submit-application-button']",
        "//*[@data-automation='submit-application-button']",
        "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
        "//button[@data-testid='submit-button']",
        "//button[@data-automation='submit-button']",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
        "//button[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]]",
        "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit your application')]",
    ]
    continue_checks = [
        "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
        "//button[@data-testid='continue-button']",
        "//button[@data-automation='continue-button']",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
        "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
    ]
    review_checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review and submit')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit your application')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review application')]",
    ]

    def has_visible(selectors, context):
        for xp in selectors:
            try:
                elems = driver.find_elements(By.XPATH, xp)
            except Exception as exc:
                raise_session_reconnect(exc, context)
            for elem in elems:
                try:
                    if elem.is_displayed():
                        return True
                except Exception as exc:
                    if is_session_recoverable_error(exc):
                        raise SessionReconnectRequired(f"{context}_state") from exc
                    continue
        return False

    if has_visible(continue_checks, "is_review_submit_page_continue"):
        return False

    has_submit = has_visible(submit_checks, "is_review_submit_page_submit")
    if not has_submit:
        return False
    return has_visible(review_checks, "is_review_submit_page_review") or has_submit


def get_submit_application_selectors():
    return [
        "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//button[@type='submit'][.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//button[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
        "//*[@data-testid='submit-application-button']",
        "//*[@data-automation='submit-application-button']",
        "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
        "//*[self::button or self::a][contains(@class, 'Button') and .//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
    ]


def hard_submit_application(driver):
    selectors = get_submit_application_selectors()
    if not any_visible_selector(driver, selectors):
        return False
    if click_first_match(driver, selectors):
        return True

    try:
        submitted = driver.execute_script(
            r"""
            const selectors = [
              'button[type="submit"]',
              '[data-testid="submit-application-button"]',
              '[data-automation="submit-application-button"]',
              'button'
            ];
            const norm = value => (value || '').toLowerCase().replace(/\s+/g, ' ').trim();
            for (const selector of selectors) {
              for (const el of document.querySelectorAll(selector)) {
                const text = norm((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || ''));
                if (!text.includes('submit application')) continue;
                el.scrollIntoView({block: 'center', inline: 'nearest'});
                try { el.click(); } catch (e) {}
                try { ['mousedown','mouseup','click'].forEach(name => el.dispatchEvent(new MouseEvent(name, {bubbles:true,cancelable:true,view:window}))); } catch (e) {}
                const form = el.form || el.closest('form');
                if (form) {
                  try { if (form.requestSubmit) { form.requestSubmit(el); } else { form.submit(); } } catch (e) {}
                }
                return true;
              }
            }
            return false;
            """
        )
        return bool(submitted)
    except Exception as exc:
        raise_session_reconnect(exc, "hard_submit_application")


def is_seek_domain(url):
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return (
        host == "seek.com.au"
        or host.endswith(".seek.com.au")
        or host == "seek.com"
        or host.endswith(".seek.com")
    )


def classify_apply_target(target_url, attrs_text=""):
    url = (target_url or "").strip()
    attrs = normalize_text(attrs_text)
    if any(marker in attrs for marker in ["advertiser s site", "apply on company site", "external site"]):
        return "external_handoff"
    if not url:
        if "quick apply" in attrs:
            return "seek_in_site"
        if "apply with seek" in attrs:
            return "seek_in_site"
        return "unknown"
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if host and not is_seek_domain(url):
        return "external_handoff"
    if is_seek_domain(url) and "/job/" in path and "/apply" in path:
        return "seek_in_site"
    if is_seek_domain(url) and "/job/" in path:
        return "seek_job"
    return "external_handoff" if host else "unknown"


def build_apply_url(job_url):
    url = (job_url or "").strip()
    if not url or not is_seek_domain(url):
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
        try:
            if is_on_apply_interface(driver):
                return True
        except Exception as exc:
            raise_session_reconnect(exc, "wait_for_apply_interface")
        time.sleep(0.1)
    return False


def has_open_seek_apply_page(driver):
    try:
        current = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "has_open_seek_apply_page")

    if classify_apply_target(current, current) == "seek_in_site":
        return True
    return is_on_apply_interface(driver)


def wait_for_apply_transition(driver, original_url, timeout=12):
    end_time = time.time() + timeout
    baseline_url = (original_url or "").lower()
    while time.time() < end_time:
        try:
            current = (driver.current_url or "").lower()
            if has_open_seek_apply_page(driver):
                return True
            if current != baseline_url and "/apply" in current and is_seek_domain(current):
                return True
        except Exception as exc:
            raise_session_reconnect(exc, "wait_for_apply_transition")
        time.sleep(0.1)
    return False


def classify_current_location(driver):
    try:
        current = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "classify_current_location")
    if not current:
        return "unknown"
    return classify_apply_target(current, current)


def ensure_job_detail_page(driver, job_url):
    expected_job_url = (job_url or "").strip()
    if not expected_job_url or is_disallowed_seek_page(expected_job_url):
        return False
    expected_job_key = extract_job_key_from_href(expected_job_url)

    try:
        current = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "ensure_job_detail_page_current")

    if is_disallowed_seek_page(current):
        try:
            driver.get(expected_job_url)
            guard_current_page_against_disallowed(driver, expected_job_url)
            time.sleep(DETAIL_LOAD_WAIT)
        except Exception as exc:
            raise_session_reconnect(exc, "ensure_job_detail_page_redirect")
        try:
            current = (driver.current_url or "").strip()
        except Exception as exc:
            raise_session_reconnect(exc, "ensure_job_detail_page_redirect_verify")

    current_job_key = extract_job_key_from_href(current)
    if classify_apply_target(current, current) == "seek_job" and (
        not expected_job_key or current_job_key == expected_job_key
    ):
        return True

    try:
        driver.get(expected_job_url)
        guard_current_page_against_disallowed(driver, expected_job_url)
        time.sleep(DETAIL_LOAD_WAIT)
    except Exception as exc:
        raise_session_reconnect(exc, "ensure_job_detail_page_get")

    try:
        current = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "ensure_job_detail_page_verify")
    current_job_key = extract_job_key_from_href(current)
    return classify_apply_target(current, current) == "seek_job" and (
        not expected_job_key or current_job_key == expected_job_key
    )


def get_current_job_identity(driver):
    try:
        current_url = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "get_current_job_identity_url")
    current_key = extract_job_key_from_href(current_url)
    title = ""
    selectors = [
        "//*[@data-automation='job-detail-title']",
        "//*[@data-testid='job-title']",
        "//h1",
    ]
    for xp in selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "get_current_job_identity_title")
        for elem in elems:
            try:
                if not elem.is_displayed():
                    continue
                text = normalize_text(elem.text)
                if text:
                    title = text
                    break
            except Exception:
                continue
        if title:
            break
    return {"url": current_url, "job_key": current_key, "title": title}


def wait_for_job_detail_ready(driver, expected_job_url="", expected_title="", timeout=None):
    deadline = time.time() + (max(2.0, float(timeout)) if timeout is not None else max(4.0, WAIT_TIMEOUT))
    expected_key = extract_job_key_from_href(expected_job_url)
    expected_title_norm = normalize_text(expected_title)
    last_identity = {"url": "", "job_key": "", "title": ""}
    while time.time() < deadline:
        wait_for_security_verification(driver, timeout=2)
        identity = get_current_job_identity(driver)
        last_identity = identity
        key_ok = not expected_key or identity.get("job_key") == expected_key
        title_ok = not expected_title_norm or identity.get("title") == expected_title_norm or expected_title_norm in identity.get("title", "")
        body_ready = False
        for xp in ["//*[@data-automation='jobAdDetails']", "//main", "//h1"]:
            try:
                elems = driver.find_elements(By.XPATH, xp)
            except Exception as exc:
                raise_session_reconnect(exc, "wait_for_job_detail_ready_find")
            visible = False
            for elem in elems:
                try:
                    if elem.is_displayed():
                        visible = True
                        break
                except Exception:
                    continue
            if visible:
                body_ready = True
                break
        if key_ok and title_ok and body_ready:
            return {"ready": True, "identity": identity}
        time.sleep(0.25)
    return {"ready": False, "identity": last_identity}


def is_disallowed_seek_page(url):
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    path = (parsed.path or "").lower()
    if not is_seek_domain(url):
        return False
    return (
        "career-advice" in path
        or "career-guide" in path
        or "/salary" in path
        or "/resume-templates" in path
        or "/cover-letter-template" in path
    )


def is_allowed_seek_page(url):
    text = (url or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("chrome://"):
        return True
    if not is_seek_domain(text) or is_disallowed_seek_page(text):
        return False
    try:
        path = (urlparse(text).path or "").lower()
    except Exception:
        return False
    return (
        "/job/" in path
        or "/jobs" in path
        or "-jobs-" in path
        or "-job-" in path
    )


def close_disallowed_seek_tabs(driver, preferred_handle="", fallback_url=""):
    closed = 0
    try:
        handles = list(driver.window_handles)
    except Exception as exc:
        raise_session_reconnect(exc, "close_disallowed_seek_tabs_handles")

    keep_handle = preferred_handle or ""
    chosen_keep = ""
    current_before = ""
    try:
        current_before = driver.current_window_handle
    except Exception:
        current_before = ""

    for handle in list(handles):
        try:
            driver.switch_to.window(handle)
            for _ in range(20):
                current_url = (driver.current_url or "").strip()
                if current_url and current_url != "about:blank":
                    break
                time.sleep(0.1)
            if is_disallowed_seek_page(current_url):
                if len(handles) == 1 and fallback_url:
                    driver.get(fallback_url)
                    try:
                        current_url = (driver.current_url or "").strip()
                    except Exception:
                        current_url = fallback_url
                    if not is_disallowed_seek_page(current_url):
                        if not chosen_keep:
                            chosen_keep = handle
                        continue
                driver.close()
                closed += 1
                continue
            if not chosen_keep:
                if keep_handle and handle == keep_handle:
                    chosen_keep = handle
                elif is_allowed_seek_page(current_url):
                    chosen_keep = handle
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("close_disallowed_seek_tabs_state") from exc
            continue

    try:
        remaining = list(driver.window_handles)
    except Exception as exc:
        raise_session_reconnect(exc, "close_disallowed_seek_tabs_remaining")

    target_handle = ""
    if keep_handle and keep_handle in remaining:
        target_handle = keep_handle
    elif chosen_keep and chosen_keep in remaining:
        target_handle = chosen_keep
    elif remaining:
        target_handle = remaining[0]

    if target_handle:
        try:
            driver.switch_to.window(target_handle)
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("close_disallowed_seek_tabs_switch_back") from exc
    elif fallback_url:
        try:
            driver.get(fallback_url)
        except Exception as exc:
            raise_session_reconnect(exc, "close_disallowed_seek_tabs_fallback_get")

    return closed


def guard_current_page_against_disallowed(driver, fallback_url):
    if not fallback_url:
        return False
    try:
        current_url = (driver.current_url or "").strip()
    except Exception as exc:
        raise_session_reconnect(exc, "guard_current_page_against_disallowed_current")
    if not is_disallowed_seek_page(current_url):
        return False
    try:
        driver.get(fallback_url)
        time.sleep(DETAIL_LOAD_WAIT)
    except Exception as exc:
        raise_session_reconnect(exc, "guard_current_page_against_disallowed_get")
    return True


def scroll_job_description_into_view(driver):
    selectors = [
        "//*[@data-automation='jobAdDetails']",
        "//*[contains(@data-automation, 'job-detail')]",
        "//main",
    ]
    for xp in selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "scroll_job_description_into_view_find")
        for elem in elems:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'start'});", elem)
                time.sleep(0.1)
                return True
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("scroll_job_description_into_view_state") from exc
                continue
    return False


def find_seek_window_handle(driver):
    try:
        handles = driver.window_handles
        current_handle = driver.current_window_handle
    except Exception as exc:
        raise_session_reconnect(exc, "find_seek_window_handle")
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            current_url = (driver.current_url or "").strip()
            if is_allowed_seek_page(current_url):
                return handle
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("find_seek_window_handle_state") from exc
            continue
    try:
        driver.switch_to.window(current_handle)
    except Exception:
        pass
    return ""


def close_external_target_and_return(driver, original_handle=None):
    host = ""
    try:
        current = (driver.current_url or "").strip()
        host = (urlparse(current).netloc or "").lower()
    except Exception:
        current = ""
    try:
        handles = driver.window_handles
    except Exception as exc:
        raise_session_reconnect(exc, "close_external_target_handles")

    if original_handle and len(handles) > 1:
        try:
            current_handle = driver.current_window_handle
            if current_handle != original_handle:
                driver.close()
                remaining_handles = driver.window_handles
                if original_handle in remaining_handles:
                    driver.switch_to.window(original_handle)
                else:
                    seek_handle = find_seek_window_handle(driver)
                    if seek_handle:
                        driver.switch_to.window(seek_handle)
                return True, host
        except Exception as exc:
            if is_session_recoverable_error(exc):
                return False, host
            raise_session_reconnect(exc, "close_external_target_close_tab")

    try:
        if driver.execute_script("return window.history.length"):
            driver.back()
            time.sleep(CLICK_PAUSE)
            return False, host
    except Exception as exc:
        if is_session_recoverable_error(exc):
            return False, host
    return False, host


def switch_to_new_tab_if_any(driver, existing_handles=None, original_handle=None):
    try:
        handles = driver.window_handles
        if len(handles) <= 1:
            return
        if existing_handles:
            for handle in handles:
                if handle not in existing_handles:
                    driver.switch_to.window(handle)
                    for _ in range(30):
                        current_url = (driver.current_url or "").strip()
                        if current_url and current_url != "about:blank":
                            break
                        time.sleep(0.1)
                    if is_disallowed_seek_page(current_url):
                        driver.close()
                        remaining = driver.window_handles
                        if original_handle and original_handle in remaining:
                            driver.switch_to.window(original_handle)
                        elif remaining:
                            driver.switch_to.window(remaining[0])
                        if original_handle and original_handle in remaining:
                            try:
                                guard_current_page_against_disallowed(driver, fallback_url=driver.current_url)
                            except Exception:
                                pass
                        return
                    return
    except Exception as exc:
        raise_session_reconnect(exc, "switch_to_new_tab")


def get_element_text_blob(elem):
    try:
        return normalize_text(" ".join([
            elem.text or "",
            elem.get_attribute("aria-label") or "",
            elem.get_attribute("title") or "",
            elem.get_attribute("name") or "",
            elem.get_attribute("data-automation") or "",
            elem.get_attribute("data-testid") or "",
        ]))
    except Exception as exc:
        raise_session_reconnect(exc, "get_element_text_blob")


def collect_visible_cta_candidates(driver):
    selectors = [
        "//*[@data-automation='job-detail-apply']",
        "//*[@data-testid='job-detail-apply']",
        "//main//*[self::button or self::a or @role='button']",
        "//*[self::button or self::a or @role='button']",
        "//input[@type='button' or @type='submit']",
    ]
    candidates = []
    seen = set()
    for xp in selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "collect_visible_cta_candidates_find")
        for elem in elems:
            try:
                if not elem.is_displayed() or not elem.is_enabled():
                    continue
                elem_id = getattr(elem, "id", None) or id(elem)
                if elem_id in seen:
                    continue
                seen.add(elem_id)
                candidates.append(
                    {
                        "element": elem,
                        "href": (elem.get_attribute("href") or "").strip(),
                        "text": get_element_text_blob(elem),
                        "role": (elem.get_attribute("role") or "").strip().lower(),
                        "data_automation": (elem.get_attribute("data-automation") or "").strip().lower(),
                        "data_testid": (elem.get_attribute("data-testid") or "").strip().lower(),
                        "title": (elem.get_attribute("title") or "").strip(),
                        "aria_label": (elem.get_attribute("aria-label") or "").strip(),
                    }
                )
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("collect_visible_cta_candidates_state") from exc
                continue
    return candidates


def append_quick_apply_debug(job_url, expected_title, detection_result):
    ensure_log_paths()
    debug_path = os.path.join(LOG_DIR, "quick_apply_debug.log")
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "job_url": job_url,
        "expected_title": expected_title,
        "current_url": detection_result.get("page_url", ""),
        "reason": detection_result.get("reason", ""),
        "visible_ctas": detection_result.get("visible_ctas", []),
        "identity": detection_result.get("identity", {}),
    }
    with open(debug_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return debug_path


def detect_quick_apply(driver, job_context=None, timeout=4):
    job_context = job_context or {}
    expected_job_url = job_context.get("job_url", "")
    expected_title = job_context.get("title", "")
    strategies = []
    final_visible_ctas = []
    final_identity = {}
    bug_inconsistency = False
    found_apply_but_not_quick = False
    selected = None

    for attempt in range(1, 5):
        if attempt > 1:
            if attempt == 2:
                time.sleep(0.5)
            elif attempt == 3:
                try:
                    driver.execute_script("window.scrollTo(0, 0);")
                except Exception:
                    pass
                time.sleep(0.4)
            else:
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.25);")
                except Exception:
                    pass
                time.sleep(0.4)

        readiness = wait_for_job_detail_ready(driver, expected_job_url=expected_job_url, expected_title=expected_title, timeout=2.5)
        final_identity = readiness.get("identity", {})
        candidates = collect_visible_cta_candidates(driver)
        visible_ctas = []
        best_score = -1
        best_item = None
        has_quick_text = False
        for item in candidates:
            text_blob = item["text"]
            href = item["href"]
            visible_ctas.append(text_blob or href or item["data_automation"] or item["data_testid"])
            is_quick_text = "quick apply" in text_blob or "apply with seek" in text_blob
            if is_quick_text:
                has_quick_text = True
            is_stable_seek = item["data_automation"] == "job-detail-apply" or item["data_testid"] == "job-detail-apply"
            is_seek_apply_href = bool(href and classify_apply_target(href, text_blob) == "seek_in_site")
            is_apply_semantic = (
                "apply" in text_blob
                or "apply" in (item["aria_label"] or "").lower()
                or "apply" in (item["title"] or "").lower()
            )
            if is_apply_semantic and not is_quick_text:
                found_apply_but_not_quick = True

            score = 0
            method = ""
            if is_quick_text:
                score = 100
                method = "visible_text"
            elif any(
                phrase in (item["aria_label"] or "").lower() or phrase in (item["title"] or "").lower()
                for phrase in ("quick apply", "apply with seek")
            ):
                score = 90
                method = "aria_attribute"
            elif is_stable_seek and (is_seek_apply_href or is_apply_semantic):
                score = 85
                method = "stable_seek_attribute"
            elif not QUICK_APPLY_ONLY and is_seek_apply_href:
                score = 70
                method = "internal_apply_cta"

            if score > best_score:
                best_score = score
                best_item = {
                    "available": score > 0,
                    "method": method or "none",
                    "selector": item["data_automation"] or item["data_testid"] or item["role"],
                    "button_text": text_blob,
                    "confidence": min(1.0, score / 100.0) if score > 0 else 0.0,
                    "page_url": final_identity.get("url", ""),
                    "reason": "" if score > 0 else "No usable Quick Apply control found after all checks",
                    "element": item["element"],
                    "href": href,
                }
        final_visible_ctas = visible_ctas
        strategies.append({"attempt": attempt, "found": bool(best_item and best_item.get("available"))})
        print(f"QUICK_APPLY_CHECK:attempt={attempt} found={bool(best_item and best_item.get('available'))}")
        if best_item and best_item.get("available"):
            selected = best_item
            break
        if has_quick_text:
            bug_inconsistency = True

    if selected:
        selected["visible_ctas"] = final_visible_ctas
        selected["identity"] = final_identity
        selected["strategies"] = strategies
        return selected

    result = {
        "available": False,
        "method": "exhaustive_detection",
        "selector": None,
        "button_text": None,
        "confidence": 1.0,
        "page_url": final_identity.get("url", ""),
        "reason": "Apply CTA exists but no usable Quick Apply control was found on the current SEEK job detail page" if found_apply_but_not_quick else "No usable Quick Apply control found after all checks",
        "visible_ctas": final_visible_ctas,
        "identity": final_identity,
        "strategies": strategies,
        "bug_inconsistency": bug_inconsistency,
        "has_apply_but_not_quick": found_apply_but_not_quick,
    }
    if bug_inconsistency:
        print("BUG: QUICK APPLY TEXT EXISTS BUT DETECTOR RETURNED FALSE")
    return result


def click_apply(driver, job_url, is_quick_apply=False, expected_title=""):
    try:
        origin_url = driver.current_url
        origin_handle = driver.current_window_handle
    except Exception as exc:
        raise_session_reconnect(exc, "click_apply_origin")

    detection = detect_quick_apply(driver, {"job_url": job_url, "title": expected_title}, timeout=max(3, WAIT_TIMEOUT / 2))
    if not detection.get("available"):
        debug_path = append_quick_apply_debug(job_url, expected_title, detection)
        print(f"QUICK_APPLY_DEBUG:{debug_path}")
        if detection.get("has_apply_but_not_quick"):
            return "not_quick_apply"
        return "not_found"

    print(
        "QUICK_APPLY_FOUND:"
        f"method={detection.get('method')} "
        f"text={detection.get('button_text')} "
        f"url={detection.get('page_url')}"
    )

    for attempt in range(1, 4):
        btn = detection.get("element")
        if btn is None:
            detection = detect_quick_apply(driver, {"job_url": job_url, "title": expected_title}, timeout=2)
            btn = detection.get("element")
            if btn is None:
                break
        try:
            origin_handles = driver.window_handles
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
            try:
                btn.click()
                print(f"APPLY_CLICK:normal:attempt={attempt}")
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
                print(f"APPLY_CLICK:js:attempt={attempt}")
            time.sleep(CLICK_PAUSE)
            switch_to_new_tab_if_any(driver, existing_handles=origin_handles, original_handle=origin_handle)
            if detect_and_lock_seek_apply_page(driver, job_url=job_url):
                print("APPLY_OPEN:detected_interface")
                return "opened"
            current_kind = classify_current_location(driver)
            if current_kind == "external_handoff" or is_external_apply(driver):
                closed_tab, host = close_external_target_and_return(driver, origin_handle)
                print(f"SKIP_EXTERNAL_HOST:{host or 'unknown'}")
                return "external_target_closed_tab" if closed_tab else "external_target"
            if wait_for_apply_transition(driver, origin_url, timeout=12) and detect_and_lock_seek_apply_page(driver, job_url=job_url):
                print("APPLY_OPEN:detected_interface")
                return "opened"
        except Exception as exc:
            if detect_and_lock_seek_apply_page(driver, job_url=job_url):
                print("APPLY_OPEN:detected_interface")
                return "opened"
            if is_session_recoverable_error(exc):
                detection = detect_quick_apply(driver, {"job_url": job_url, "title": expected_title}, timeout=2)
                continue
        detection = detect_quick_apply(driver, {"job_url": job_url, "title": expected_title}, timeout=2)

    if detect_and_lock_seek_apply_page(driver, job_url=job_url):
        print("APPLY_OPEN:detected_interface")
        return "opened"
    return "visible_but_not_opened"


def click_first_match(driver, selectors):
    candidates = []
    seen_ids = set()
    for xp in selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "click_first_match_find")
        for elem in elems:
            try:
                candidate = elem
                try:
                    resolved = driver.execute_script(
                        'const el=arguments[0]; return el && el.closest ? el.closest("button, a, [role=\"button\"], input[type=\"submit\"], input[type=\"button\"]") : el;',
                        elem,
                    )
                    if resolved is not None:
                        candidate = resolved
                except Exception:
                    candidate = elem

                if not candidate.is_displayed() or not candidate.is_enabled():
                    continue
                elem_id = getattr(candidate, "id", None) or id(candidate)
                if elem_id in seen_ids:
                    continue
                seen_ids.add(elem_id)
                tag = (candidate.tag_name or "").lower()
                elem_type = (candidate.get_attribute("type") or "").lower()
                text_blob = normalize_text(" ".join([
                    candidate.text or "",
                    candidate.get_attribute("aria-label") or "",
                    candidate.get_attribute("title") or "",
                    candidate.get_attribute("data-testid") or "",
                    candidate.get_attribute("data-automation") or "",
                ]))
                y = 0
                x = 0
                width = 0
                height = 0
                try:
                    location = candidate.location_once_scrolled_into_view or {}
                    size = candidate.size or {}
                    y = int(location.get("y", 0))
                    x = int(location.get("x", 0))
                    width = int(size.get("width", 0))
                    height = int(size.get("height", 0))
                except Exception:
                    pass
                priority = 0
                if tag == "button":
                    priority += 25
                if elem_type == "submit":
                    priority += 35
                if tag == "a":
                    priority -= 10
                if width >= 80 and height >= 28:
                    priority += 10
                if text_blob == "submit application":
                    priority += 60
                elif "submit application" in text_blob:
                    priority += 45
                elif text_blob == "submit":
                    priority += 35
                elif "submit" in text_blob:
                    priority += 25
                elif text_blob == "continue":
                    priority += 30
                elif "continue" in text_blob:
                    priority += 20
                elif "next" in text_blob:
                    priority += 15
                candidates.append((priority, y, width * height, candidate, text_blob))
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("click_first_match_collect") from exc
                continue

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    for _priority, _y, _area, elem, text_blob in candidates:
        try:
            block = "end" if any(token in text_blob for token in ("continue", "submit", "next", "review")) else "center"
            driver.execute_script("arguments[0].scrollIntoView({block: arguments[1], inline: 'nearest'});", elem, block)
            try:
                elem.click()
            except Exception:
                try:
                    ActionChains(driver).move_to_element(elem).pause(0.1).click(elem).perform()
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", elem)
                    except Exception:
                        try:
                            driver.execute_script(
                                "const el=arguments[0]; ['mousedown','mouseup','click'].forEach(name => el.dispatchEvent(new MouseEvent(name, {bubbles:true,cancelable:true,view:window})));",
                                elem,
                            )
                        except Exception:
                            try:
                                elem.send_keys(Keys.ENTER)
                            except Exception:
                                submitted = driver.execute_script(
                                    "const el=arguments[0]; const form=el.form || el.closest('form'); if(form){ if(form.requestSubmit){ form.requestSubmit(el); } else { form.submit(); } return true; } return false;",
                                    elem,
                                )
                                if not submitted:
                                    raise
            time.sleep(CLICK_PAUSE)
            return True
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("click_first_match_click") from exc
            continue
    return False


def get_job_text_snapshot(driver):
    scroll_job_description_into_view(driver)
    expand_selectors = [
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more')]",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'read more')]",
        "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'read more')]",
    ]
    clicked_expand = set()
    for xp in expand_selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "get_job_text_snapshot_expand_find")
        for elem in elems:
            try:
                label = normalize_text(elem.text)
                if not elem.is_displayed() or label in clicked_expand:
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                try:
                    elem.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", elem)
                clicked_expand.add(label)
                time.sleep(0.1)
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("get_job_text_snapshot_expand_state") from exc
                continue

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
    normalized_target = normalize_text(target_name)
    page_text = normalize_text(driver.page_source)
    if normalized_target and normalized_target in page_text:
        candidates = []
        selectors = [
            f"//*[self::label or self::button or self::a or self::div or self::span][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{normalized_target}')]",
            f"//option[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{normalized_target}')]",
        ]
        for xp in selectors:
            try:
                elems = driver.find_elements(By.XPATH, xp)
            except Exception as exc:
                raise_session_reconnect(exc, "select_resume_if_present_find")
            for elem in elems:
                try:
                    if not elem.is_displayed() or not elem.is_enabled():
                        continue
                    text_blob = normalize_text(" ".join([
                        elem.text or "",
                        elem.get_attribute("aria-label") or "",
                        elem.get_attribute("title") or "",
                    ]))
                    if normalized_target not in text_blob:
                        continue
                    y = 0
                    try:
                        y = int((elem.location or {}).get("y", 0))
                    except Exception:
                        y = 0
                    candidates.append((y, text_blob != normalized_target, elem))
                except Exception as exc:
                    if is_session_recoverable_error(exc):
                        raise SessionReconnectRequired("select_resume_if_present_state") from exc
                    continue
        candidates.sort(key=lambda item: (item[1], item[0]))
        for _y, _partial, elem in candidates:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});", elem)
                try:
                    elem.click()
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(elem).pause(0.05).click(elem).perform()
                    except Exception:
                        driver.execute_script("arguments[0].click();", elem)
                print(f"RESUME_SELECT:{target_name}")
                time.sleep(min(CLICK_PAUSE, 0.3))
                return True
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("select_resume_if_present_click") from exc
                continue
    print("RESUME_SELECT:keep_current")
    return False


def get_field_context_text(driver, elem):
    try:
        text = driver.execute_script(
            """
            const el = arguments[0];
            const container = el.closest('fieldset, section, form, div');
            return (container ? container.innerText : (el.innerText || el.textContent || '')) || '';
            """,
            elem,
        )
    except Exception as exc:
        raise_session_reconnect(exc, "get_field_context_text")
    return normalize_text(text)


def set_input_value(driver, elem, value):
    fill_value = str(value or "")
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", elem)
    except Exception:
        pass

    script = """
    const el = arguments[0];
    const value = arguments[1];
    el.focus();
    el.value = '';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    """
    try:
        driver.execute_script(script, elem, fill_value)
    except Exception as exc:
        if is_session_recoverable_error(exc):
            raise SessionReconnectRequired("set_input_value_script") from exc
        try:
            elem.clear()
        except Exception:
            pass
        try:
            elem.send_keys(fill_value)
        except Exception as inner_exc:
            if is_session_recoverable_error(inner_exc):
                raise SessionReconnectRequired("set_input_value_send_keys") from inner_exc
            return False

    try:
        updated_value = (elem.get_attribute("value") or elem.text or "").strip()
    except Exception as exc:
        if is_session_recoverable_error(exc):
            raise SessionReconnectRequired("set_input_value_verify") from exc
        updated_value = ""
    return normalize_text(updated_value) == normalize_text(fill_value)


def refresh_active_job_context_from_page(driver):
    if not hasattr(driver, "find_elements"):
        return ACTIVE_JOB_CONTEXT.get("company_name", ""), ACTIVE_JOB_CONTEXT.get("position", "")

    fallback_company = ACTIVE_JOB_CONTEXT.get("company_name", "")
    fallback_position = ACTIVE_JOB_CONTEXT.get("position", "")
    company_name, position = extract_company_and_position(driver, fallback_position)

    if normalize_text(company_name) == "unknown":
        company_name = fallback_company
    if normalize_text(position) == "unknown":
        position = fallback_position

    set_active_job_context(company_name, position)
    return company_name, position


def is_cover_letter_context(context):
    context_text = normalize_text(context)
    hints = ["cover letter", "message to employer", "message for the employer", "why are you interested"]
    return any(hint in context_text for hint in hints)


def get_configured_answer_for_context(context, answers=None):
    context_text = normalize_text(context)
    configured_answers = dict(JOB_FILTERS_CFG)
    if isinstance(answers, dict):
        configured_answers.update(answers)

    mapping = [
        ("expected_salary", ["expected salary", "salary expectation", "salary are you expecting"]),
        ("current_salary", ["current salary", "what are you currently earning", "what is your salary"]),
        ("visa_type", ["visa", "work rights", "right to work", "work in australia"]),
        ("experience", ["years experience", "how many years", "experience do you have"]),
        ("location", ["location", "suburb", "where are you based", "where do you live"]),
        ("job_type", ["job type", "employment type", "full time", "part time", "casual"]),
        ("classification", ["classification", "specialisation", "specialization", "category"]),
        ("certification", ["certification", "licence", "license", "qualification"]),
        ("keywords", ["key skills", "keywords", "skills", "expertise"]),
        ("cover_letter", ["cover letter", "message to employer", "message for the employer", "why are you interested"]),
    ]

    for field_name, hints in mapping:
        if not any(hint in context_text for hint in hints):
            continue
        value = configured_answers.get(field_name)
        if value is None or value == "":
            return "", []
        if isinstance(value, (list, tuple, set)):
            tokens = [str(item).strip() for item in value if str(item).strip()]
        else:
            tokens = [str(value).strip()]
        if field_name == "cover_letter":
            company_name = ACTIVE_JOB_CONTEXT.get("company_name", "")
            position = ACTIVE_JOB_CONTEXT.get("position", "")
            tokens = [build_cover_letter_text(item, company_name, position) for item in tokens]
        if tokens:
            return field_name, tokens
    return "", []


def select_first_matching_option(select_elem, match_tokens):
    try:
        sel = Select(select_elem)
        options = sel.options
    except Exception:
        return False

    normalized_tokens = [normalize_text(token) for token in match_tokens if normalize_text(token)]
    digit_tokens = [re.sub(r"[^\d]", "", token) for token in match_tokens]
    digit_tokens = [token for token in digit_tokens if token]

    for option in options:
        text = normalize_text(option.text)
        value = normalize_text(option.get_attribute("value") or "")
        digit_text = re.sub(r"[^\d]", "", option.text or "")
        digit_value = re.sub(r"[^\d]", "", option.get_attribute("value") or "")
        if not text or text in ("select", "select one", "please select"):
            continue
        text_match = any(token in text or token in value for token in normalized_tokens)
        digit_match = any(token in digit_text or token in digit_value for token in digit_tokens)
        if text_match or digit_match:
            try:
                sel.select_by_visible_text(option.text)
                return True
            except Exception:
                try:
                    sel.select_by_value(option.get_attribute("value") or "")
                    return True
                except Exception:
                    continue
    return False


def answer_common_select_questions(driver):
    changed = False
    try:
        selects = driver.find_elements(By.XPATH, "//select[not(@disabled)]")
    except Exception as exc:
        raise_session_reconnect(exc, "answer_common_select_questions_find")

    for select_elem in selects:
        try:
            if not select_elem.is_displayed() or not select_elem.is_enabled():
                continue
            current_value = normalize_text(select_elem.get_attribute("value") or "")
            selected_text = ""
            try:
                selected_text = normalize_text(Select(select_elem).first_selected_option.text)
            except Exception:
                selected_text = ""
            if current_value and selected_text not in ("", "select", "select one", "please select"):
                continue

            context = get_field_context_text(driver, select_elem)
            if not context:
                continue

            field_name, answer_tokens = get_configured_answer_for_context(context)
            if answer_tokens and select_first_matching_option(select_elem, answer_tokens):
                print(f"EMPLOYER_Q:{field_name}={answer_tokens[0]}")
                changed = True
                continue

            if any(token in context for token in ["right to work", "work rights", "work in australia", "visa"]):
                if select_first_matching_option(select_elem, ["temporary visa", "student visa", "restrictions on work hours"]):
                    print("EMPLOYER_Q:work_rights=temp_visa")
                    changed = True
                    continue

            if any(token in context for token in ["years experience", "how many years", "experience do you have"]):
                if select_first_matching_option(select_elem, ["0", "0-1", "less than 1", "under 1", "1 year", "1-2"]):
                    print("EMPLOYER_Q:experience=conservative")
                    changed = True
                    continue
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("answer_common_select_questions_state") from exc
            continue
    return changed


def answer_common_input_questions(driver):
    changed = False
    xpath = "//input[not(@disabled) and not(@type='hidden') and not(@type='file') and not(@type='checkbox') and not(@type='radio')] | //textarea[not(@disabled)]"
    try:
        fields = driver.find_elements(By.XPATH, xpath)
    except Exception as exc:
        raise_session_reconnect(exc, "answer_common_input_questions_find")

    for elem in fields:
        try:
            if not elem.is_displayed() or not elem.is_enabled():
                continue

            current_value = (elem.get_attribute("value") or elem.text or "").strip()
            context = get_field_context_text(driver, elem)
            field_name, answer_tokens = get_configured_answer_for_context(context)
            if field_name == "cover_letter" or (not field_name and current_value and is_cover_letter_context(context)):
                company_name = ACTIVE_JOB_CONTEXT.get("company_name", "")
                position = ACTIVE_JOB_CONTEXT.get("position", "")
                source_text = "\n".join(answer_tokens).strip() if answer_tokens else current_value
                fill_value = rewrite_cover_letter_for_current_job(source_text, company_name, position)
                if not fill_value:
                    continue
                if normalize_text(current_value) == normalize_text(fill_value):
                    continue
                field_name = "cover_letter"
            else:
                if current_value or not answer_tokens:
                    continue
                if field_name == "keywords":
                    fill_value = ", ".join(answer_tokens)
                else:
                    fill_value = answer_tokens[0]

            if not fill_value:
                continue

            if set_input_value(driver, elem, fill_value):
                print(f"EMPLOYER_Q:{field_name}={fill_value}")
                time.sleep(0.1)
                changed = True
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("answer_common_input_questions_state") from exc
            continue
    return changed


def answer_common_radio_questions(driver):
    changed = False
    radio_script = """
    const groups = [];
    const seen = new Set();
    const radios = Array.from(document.querySelectorAll('input[type="radio"]:not([disabled])'));
    for (const radio of radios) {
      const key = radio.name || radio.id;
      if (!key || seen.has(key)) continue;
      const group = radios.filter(item => (item.name || item.id) === key);
      if (!group.length || group.some(item => item.checked)) continue;
      const first = group[0];
      const visible = !!(first.offsetWidth || first.offsetHeight || first.getClientRects().length);
      if (!visible) continue;
      const container = first.closest('fieldset, section, form, div') || first.parentElement;
      if (!container) continue;
      seen.add(key);
      groups.push(container);
    }
    return groups;
    """
    try:
        groups = driver.execute_script(radio_script)
    except Exception as exc:
        raise_session_reconnect(exc, "answer_common_radio_questions_find")

    for container in groups:
        try:
            context = get_field_context_text(driver, container)
            field_name, answer_tokens = get_configured_answer_for_context(context)
            if not answer_tokens:
                continue

            clicked = driver.execute_script(
                """
                const container = arguments[0];
                const answers = arguments[1].map(item => String(item).toLowerCase().trim()).filter(Boolean);
                const nodes = Array.from(container.querySelectorAll('label, button, [role="radio"], [role="option"]'));
                for (const node of nodes) {\n                  const text = (node.innerText || node.textContent || '').toLowerCase().trim();
                  if (!text) continue;
                  if (answers.some(answer => text === answer || text.includes(answer))) {\n                    node.scrollIntoView({ block: 'center' });
                    node.click();
                    return true;
                  }
                }
                return false;
                """,
                container,
                answer_tokens,
            )
            if clicked:
                print(f"EMPLOYER_Q:{field_name}={answer_tokens[0]}")
                time.sleep(0.1)
                changed = True
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("answer_common_radio_questions_state") from exc
            continue
    return changed


def click_visible_label_choice(driver, label_text):
    xpath = f"//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label_text}')]"
    try:
        elems = driver.find_elements(By.XPATH, xpath)
    except Exception as exc:
        raise_session_reconnect(exc, "click_visible_label_choice_find")

    for elem in elems:
        try:
            if not elem.is_displayed():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
            try:
                elem.click()
            except Exception:
                driver.execute_script("arguments[0].click();", elem)
            time.sleep(0.2)
            return True
        except Exception as exc:
            if is_session_recoverable_error(exc):
                raise SessionReconnectRequired("click_visible_label_choice_state") from exc
            continue
    return False


def answer_known_employer_questions(driver):
    text = normalize_text(driver.page_source)
    changed = False

    changed = answer_common_select_questions(driver) or changed
    changed = answer_common_input_questions(driver) or changed
    changed = answer_common_radio_questions(driver) or changed

    if "rsa" in text or "responsible service of alcohol" in text:
        if click_visible_label_choice(driver, "no"):
            print("EMPLOYER_Q:rsa=no")
            changed = True

    yes_selectors = [
        "//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]",
    ]
    keywords = ["driver", "driver's licence", "right to work", "work rights", "australia"]
    matched = any(k in text for k in keywords)
    if not matched:
        return changed

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
    return changed


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

    radio_group_script = """
    const groups = new Map();
    const radios = Array.from(document.querySelectorAll('input[type="radio"]:not([disabled])'));
    for (const radio of radios) {
      const name = radio.name || radio.id;
      if (!name) continue;
      const required = radio.required || radio.getAttribute('aria-required') === 'true';
      if (!required) continue;
      const visible = !!(radio.offsetWidth || radio.offsetHeight || radio.getClientRects().length);
      if (!visible) continue;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(radio);
    }
    for (const items of groups.values()) {
      if (!items.some(r => r.checked)) return true;
    }
    return false;
    """
    try:
        if driver.execute_script(radio_group_script):
            return True
    except Exception as exc:
        raise_session_reconnect(exc, "has_unanswered_required_questions_radios")
    return False



def prepare_active_application(driver):
    refresh_active_job_context_from_page(driver)
    select_resume_if_present(driver, os.path.basename(RESUME_FILE or ""))
    answer_known_employer_questions(driver)
    return handle_resume_upload(driver)


def get_apply_page_signature(driver, phase=None):
    try:
        current = (driver.current_url or "").lower().strip()
        action = get_primary_action_name(driver, phase or get_current_flow_phase(driver))
        main_text = ""
        for xp in ["//main", "//*[@data-automation='application-form']", "//*[@data-testid='application-form']"]:
            elems = driver.find_elements(By.XPATH, xp)
            for elem in elems:
                try:
                    if not elem.is_displayed():
                        continue
                    main_text = normalize_text(elem.text)[:800]
                    if main_text:
                        break
                except Exception as exc:
                    if is_session_recoverable_error(exc):
                        raise SessionReconnectRequired("get_apply_page_signature_state") from exc
                    continue
            if main_text:
                break
        return "|".join([
            current,
            phase or "",
            action,
            "questions" if is_employer_questions_step(driver) else "no_questions",
            main_text,
        ])
    except Exception as exc:
        raise_session_reconnect(exc, "get_apply_page_signature")


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
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    if not ENABLE_EVALUATION_CSV and os.path.exists(EVALUATION_CSV_LOG_PATH):
        try:
            os.remove(EVALUATION_CSV_LOG_PATH)
        except OSError:
            pass


def append_evaluation_log(job_url, company_name, position, decision, reason, filter_result=None, match_result=None, decision_data=None):
    if not ENABLE_EVALUATION_CSV:
        return False
    ensure_log_paths()
    preferred_header = [
        "timestamp",
        "job_link",
        "company_name",
        "position",
        "decision",
        "reason",
        "filter_eligible",
        "filter_reasons",
        "matched_required",
        "target_role",
        "matched_role",
        "title_match",
        "title_match_reason",
        "related_role_match",
        "role_overlap_score",
        "salary_numbers",
        "match_score",
        "match_eligible",
        "decision_score",
        "decision_reason",
        "hard_fail_reason",
        "score_breakdown",
        "decision_json",
    ]
    if not os.path.exists(EVALUATION_CSV_LOG_PATH):
        with open(EVALUATION_CSV_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(preferred_header)
    header = preferred_header
    try:
        with open(EVALUATION_CSV_LOG_PATH, "r", newline="", encoding="utf-8") as f:
            first_row = next(csv.reader(f), [])
            if first_row:
                header = first_row
    except Exception:
        header = preferred_header

    row_map = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "job_link": job_url,
        "company_name": company_name,
        "position": position,
        "decision": decision,
        "reason": reason,
        "filter_eligible": "" if not filter_result else filter_result.get("eligible"),
        "filter_reasons": "" if not filter_result else "|".join(filter_result.get("rejection_reasons", [])),
        "matched_required": "" if not filter_result else "|".join(filter_result.get("matched_required", [])),
        "target_role": "" if not filter_result else filter_result.get("target_role", ""),
        "matched_role": "" if not filter_result else filter_result.get("matched_role", ""),
        "title_match": "" if not filter_result else filter_result.get("title_match"),
        "title_match_reason": "" if not filter_result else filter_result.get("title_match_reason", ""),
        "related_role_match": "" if not filter_result else filter_result.get("related_role_match", ""),
        "role_overlap_score": "" if not filter_result else filter_result.get("role_overlap_score", 0),
        "salary_numbers": "" if not filter_result else "|".join(str(x) for x in filter_result.get("salary_numbers", [])),
        "match_score": "" if not match_result else match_result.get("score"),
        "match_eligible": "" if not match_result else match_result.get("eligible"),
        "decision_score": "" if not decision_data else decision_data.get("total_score"),
        "decision_reason": "" if not decision_data else decision_data.get("decision_reason", ""),
        "hard_fail_reason": "" if not decision_data else decision_data.get("hard_fail_reason", ""),
        "score_breakdown": "" if not decision_data else json.dumps(decision_data.get("breakdown", {}), sort_keys=True),
        "decision_json": "" if not decision_data else json.dumps(decision_data, sort_keys=True, default=str),
    }
    with open(EVALUATION_CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([row_map.get(column, "") for column in header])
    return True


def safe_filename(value):
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "")
    return clean.strip("_") or "job"


def get_screenshot_phase_dir(phase="after", current_time=None):
    stamp_source = current_time or datetime.now()
    dated_dir = os.path.join(SCREENSHOT_DIR, stamp_source.strftime("%Y-%m-%d"))
    if phase == "pending_before":
        phase_dir = os.path.join(dated_dir, "_pending_before")
    else:
        phase_dir = os.path.join(dated_dir, "before" if phase == "before" else "after")
    os.makedirs(phase_dir, exist_ok=True)
    return phase_dir


def get_next_screenshot_path(phase="after", current_time=None):
    target_dir = get_screenshot_phase_dir(phase=phase, current_time=current_time)
    highest = 0
    for name in os.listdir(target_dir):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".png":
            continue
        if stem.isdigit():
            highest = max(highest, int(stem))
    return os.path.join(target_dir, f"{highest + 1}.png")


def capture_job_screenshot(driver, job_key, status, phase="after"):
    ensure_log_paths()
    out_path = get_next_screenshot_path(phase=phase)
    try:
        driver.save_screenshot(out_path)
        return out_path
    except Exception:
        return ""


def capture_job_screenshot_to_path(driver, out_path):
    if not driver or not out_path:
        return ""
    ensure_log_paths()
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        driver.save_screenshot(out_path)
        return out_path
    except Exception:
        return ""


def finalize_submission_screenshots(driver, before_pending_path, job_key):
    ensure_log_paths()
    after_path = get_next_screenshot_path(phase="after")
    file_name = os.path.basename(after_path)
    before_path = os.path.join(get_screenshot_phase_dir(phase="before"), file_name)
    saved_after_path = capture_job_screenshot_to_path(driver, after_path)
    saved_before_path = ""
    if before_pending_path and os.path.exists(before_pending_path):
        try:
            os.replace(before_pending_path, before_path)
            saved_before_path = before_path
        except OSError:
            saved_before_path = ""
    return saved_before_path, saved_after_path


def is_job_start_screenshot_ready(driver):
    if not driver:
        return False
    title_selectors = [
        "//h1[normalize-space(.)!='']",
        "//*[@data-automation='job-detail-title'][normalize-space(.)!='']",
        "//*[@data-testid='job-title'][normalize-space(.)!='']",
    ]
    try:
        current = (driver.current_url or "").strip().lower()
    except Exception:
        current = ""
    if "/apply" in current:
        return False
    return any_visible_selector(driver, title_selectors)


def wait_for_job_start_screenshot_ready(driver, timeout=6):
    end_time = time.time() + max(1, float(timeout))
    while time.time() < end_time:
        try:
            try:
                driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass
            if is_job_start_screenshot_ready(driver):
                return True
        except Exception as exc:
            raise_session_reconnect(exc, "wait_for_job_start_screenshot_ready")
        time.sleep(0.1)
    return is_job_start_screenshot_ready(driver)


def capture_job_start_screenshot(driver, job_key):
    if not driver:
        return ""
    wait_for_job_start_screenshot_ready(driver, timeout=max(2, DETAIL_LOAD_WAIT * 6))
    try:
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass
    time.sleep(0.2)
    return capture_job_screenshot(driver, job_key, "before_apply", phase="pending_before")


def remove_screenshot_file(path):
    target = (path or "").strip()
    if not target:
        return False
    try:
        if os.path.exists(target):
            os.remove(target)
            return True
    except OSError:
        return False
    return False


def extract_company_and_position(driver, fallback_title):
    fallback_position = (fallback_title or "").strip()
    position = ""
    company = ""

    title_selectors = [
        "//*[@data-automation='job-detail-title']",
        "//*[@data-testid='job-title']",
        "//h1[normalize-space(.)!='']",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'applying for')]/following::*[self::h1 or self::h2 or self::div or self::span][normalize-space(.)!=''][1]",
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
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'applying for')]/following::*[normalize-space(.)!=''][2]",
    ]
    for xp in company_selectors:
        elems = driver.find_elements(By.XPATH, xp)
        for elem in elems:
            c = (elem.text or "").strip()
            if c and normalize_text(c) != normalize_text(position):
                company = c
                break
        if company:
            break

    if not company:
        text_blob = (driver.page_source or "")[:4000]
        m = re.search(r"by\s+([A-Za-z0-9 &.,'-]{2,60})", text_blob)
        if m:
            company = m.group(1).strip()

    if not position:
        position = fallback_position

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


def load_today_submitted_job_keys():
    today = datetime.now().strftime("%d-%m-%Y")
    submitted = set()
    if not os.path.exists(CSV_LOG_PATH):
        return submitted
    try:
        with open(CSV_LOG_PATH, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                if (row.get("status") or "").strip().lower() != "submitted":
                    continue
                if (row.get("date") or "").strip() != today:
                    continue
                job_link = (row.get("job_link") or "").strip()
                key = extract_job_key_from_href(job_link)
                if key:
                    submitted.add(key)
    except Exception:
        return submitted
    return submitted


def should_skip_previously_submitted_job(job_key, submitted_keys=None):
    if not SKIP_ALREADY_APPLIED:
        return False
    lookup = submitted_keys if submitted_keys is not None else TODAY_SUBMITTED_JOB_KEYS
    return bool(job_key and job_key in lookup)


def get_applied_log_rows():
    if not os.path.exists(CSV_LOG_PATH):
        return []
    try:
        with open(CSV_LOG_PATH, "r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def count_applied_rows_for_job(job_link):
    target = extract_job_key_from_href(job_link)
    count = 0
    for row in get_applied_log_rows():
        row_link = extract_job_key_from_href((row.get("job_link") or "").strip())
        if row_link and row_link == target and (row.get("status") or "").strip().lower() == "submitted":
            count += 1
    return count


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
        return False

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

    if count_applied_rows_for_job(job_link) > 0:
        return False

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
    return True


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


def wait_for_manual_required_answers(driver, force_fill_window=False):
    if not WAIT_FOR_MANUAL_QUESTIONS:
        return "blocked_questions"

    interval = max(0.5, MANUAL_QUESTION_SCAN_INTERVAL)
    timeout_deadline = time.time() + MANUAL_QUESTION_TIMEOUT if MANUAL_QUESTION_TIMEOUT > 0 else None
    print("MANUAL_WAIT:start")
    last_ping = time.time()
    gave_manual_fill_window = False
    while True:
        try:
            if classify_current_location(driver) == "external_handoff" or is_external_apply(driver):
                print("MANUAL_WAIT:external")
                return "external"
        except Exception:
            pass
        if is_application_submitted(driver):
            return "submitted"
        unresolved = has_unanswered_required_questions(driver)
        if force_fill_window and not gave_manual_fill_window and MANUAL_FIELD_FILL_WAIT > 0:
            gave_manual_fill_window = True
            print(f"MANUAL_WAIT:fill_window:{MANUAL_FIELD_FILL_WAIT}")
            time.sleep(MANUAL_FIELD_FILL_WAIT)
            continue
        if not unresolved:
            print("MANUAL_WAIT:resolved")
            if MANUAL_FIELD_SETTLE_WAIT > 0:
                print(f"MANUAL_WAIT:settle:{MANUAL_FIELD_SETTLE_WAIT}")
                time.sleep(MANUAL_FIELD_SETTLE_WAIT)
            if MANUAL_RESOLUTION_CONFIRM_WAIT > 0:
                print(f"MANUAL_WAIT:confirm:{MANUAL_RESOLUTION_CONFIRM_WAIT}")
                time.sleep(MANUAL_RESOLUTION_CONFIRM_WAIT)
            if has_unanswered_required_questions(driver):
                print("MANUAL_WAIT:reopened")
                continue
            return "resolved"
        if not gave_manual_fill_window and MANUAL_FIELD_FILL_WAIT > 0:
            gave_manual_fill_window = True
            print(f"MANUAL_WAIT:fill_window:{MANUAL_FIELD_FILL_WAIT}")
            time.sleep(MANUAL_FIELD_FILL_WAIT)
            continue
        if timeout_deadline is not None and time.time() >= timeout_deadline:
            print("MANUAL_WAIT:timeout")
            return "blocked_questions"

        now = time.time()
        if now - last_ping >= 30:
            print("MANUAL_WAIT:still_waiting")
            last_ping = now
        time.sleep(interval)

def get_quick_apply_step_selectors():
    pre_review_steps = [
        (
            "CONTINUE",
            [
                "//button[@type='submit'][.//text()[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]]",
                "//*[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
                "//button[@data-testid='continue-button']",
                "//button[@data-automation='continue-button']",
                "//button[contains(@aria-label, 'Continue')]",
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
    ]
    review_submit_steps = [
        (
            "SUBMIT_APPLICATION",
            [
                "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//button[@type='submit'][.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//button[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
                "//*[@data-testid='submit-application-button']",
                "//*[@data-automation='submit-application-button']",
                "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
            ],
        ),
        (
            "SUBMIT",
            [
                "//button[@type='submit' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
                "//button[@type='submit'][.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]]",
                "//*[@data-testid='submit-button']",
                "//*[@data-automation='submit-button']",
                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
                "//button[.//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]]",
                "//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
                "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
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
    return {"pre_review": pre_review_steps, "review_submit": review_submit_steps}


def get_current_flow_phase(driver):
    if is_review_submit_page(driver):
        return "review_submit"
    return "pre_review"


def any_visible_selector(driver, selectors):
    for xp in selectors:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "any_visible_selector")
        for elem in elems:
            try:
                if elem.is_displayed() and elem.is_enabled():
                    return True
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("any_visible_selector_state") from exc
                continue
    return False


def get_primary_cta_sequence():
    step_groups = get_quick_apply_step_selectors()
    selector_map = {}
    for group_steps in step_groups.values():
        for step_name, selectors in group_steps:
            selector_map[step_name] = selectors
    ordered_steps = ["SUBMIT_APPLICATION", "SUBMIT", "CONTINUE", "NEXT", "REVIEW", "YES"]
    return [(step_name, selector_map.get(step_name, [])) for step_name in ordered_steps if selector_map.get(step_name)]


def get_primary_action_name(driver, phase=None):
    phase = phase or get_current_flow_phase(driver)
    step_groups = get_quick_apply_step_selectors()
    pre_review_steps = step_groups.get("pre_review", [])
    submit_steps = step_groups.get("review_submit", [])

    for step_name, selectors in pre_review_steps:
        if any_visible_selector(driver, selectors):
            return step_name

    if phase == "review_submit":
        for step_name, selectors in submit_steps:
            if any_visible_selector(driver, selectors):
                return step_name
    else:
        for step_name, selectors in submit_steps:
            if any_visible_selector(driver, selectors):
                return step_name
    return ""


def get_primary_action_selectors(step_name):
    for name, selectors in get_primary_cta_sequence():
        if name == step_name:
            return selectors
    return []


def is_seek_profile_update_step(driver):
    checks = [
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'update seek profile')]",
        "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'your seek profile is part of your application')]",
    ]
    for xp in checks:
        try:
            elems = driver.find_elements(By.XPATH, xp)
        except Exception as exc:
            raise_session_reconnect(exc, "is_seek_profile_update_step")
        for elem in elems:
            try:
                if elem.is_displayed():
                    return True
            except Exception as exc:
                if is_session_recoverable_error(exc):
                    raise SessionReconnectRequired("is_seek_profile_update_step_state") from exc
                continue
    return False


def should_prepare_active_application(driver, phase=None, step_name=""):
    if phase is None:
        phase = get_current_flow_phase(driver)
    if phase == "review_submit":
        return False
    if step_name in ("CONTINUE", "NEXT", "REVIEW") and not has_unanswered_required_questions(driver):
        return False
    if is_seek_profile_update_step(driver):
        return False
    return True


def wait_for_step_progress(driver, before_url, before_phase, before_action, before_signature="", before_question_state=False, timeout=4):
    end_time = time.time() + timeout
    baseline = (before_url or "").lower()
    while time.time() < end_time:
        try:
            if is_application_submitted(driver):
                return True
            current = (driver.current_url or "").lower()
            current_phase = get_current_flow_phase(driver)
            current_action = get_primary_action_name(driver, current_phase)
            current_question_state = is_employer_questions_step(driver)
            current_signature = get_apply_page_signature(driver, current_phase)
            if current != baseline:
                return True
            if current_phase != before_phase:
                return True
            if current_question_state != before_question_state:
                return True
            if current_action and current_action != before_action:
                return True
            if before_signature and current_signature != before_signature:
                return True
        except Exception as exc:
            raise_session_reconnect(exc, "wait_for_step_progress")
        time.sleep(0.15)
    return False


def revalidate_current_job_title_before_submit(driver):
    identity = get_current_job_identity(driver)
    title_result = evaluate_target_title(identity.get("title", ""))
    log_title_match_result(title_result)
    return title_result


def run_quick_apply_flow(driver, job_key="", artifact_state=None):
    idle_cycles = 0
    last_wait_log = 0
    same_page_count = 0
    same_review_page_count = 0
    prepared_signatures = set()
    manual_pause_signatures = set()
    while True:
        try:
            refresh_active_apply_state(driver)
            if classify_current_location(driver) == "external_handoff" or is_external_apply(driver):
                return "external"

            if is_application_submitted(driver):
                return "submitted"

            phase = get_current_flow_phase(driver)
            current_signature = get_apply_page_signature(driver, phase)
            step_name = get_primary_action_name(driver, phase)
            if phase == "review_submit":
                print("FLOW_PHASE:review_submit")
            if should_prepare_active_application(driver, phase=phase, step_name=step_name) and current_signature not in prepared_signatures:
                if not prepare_active_application(driver):
                    return "resume_upload_failed"
                prepared_signatures.add(current_signature)
                current_signature = get_apply_page_signature(driver, phase)
                step_name = get_primary_action_name(driver, phase)

            if is_employer_questions_step(driver):
                force_manual_pause = current_signature not in manual_pause_signatures
                unanswered_required = has_unanswered_required_questions(driver)
                if force_manual_pause or unanswered_required:
                    manual_pause_signatures.add(current_signature)
                    print("APPLY_WAIT:manual_questions")
                    manual_state = wait_for_manual_required_answers(driver, force_fill_window=force_manual_pause)
                    if manual_state == "submitted":
                        return "submitted"
                    if manual_state == "external":
                        return "external"
                    if manual_state == "resolved":
                        idle_cycles = 0
                        same_page_count = 0
                        same_review_page_count = 0
                        continue

            progressed = False
            clicked_step = False
            if not step_name and detect_and_lock_seek_apply_page(driver, switch=False):
                phase = get_current_flow_phase(driver)
                step_name = get_primary_action_name(driver, phase)
            selectors = get_primary_action_selectors(step_name)
            if phase == "review_submit" and any_visible_selector(driver, get_submit_application_selectors()):
                step_name = "SUBMIT_APPLICATION"
                selectors = get_submit_application_selectors()
            if step_name:
                before_url = driver.current_url
                before_action = step_name
                before_question_state = is_employer_questions_step(driver)
                before_signature = current_signature
                if step_name in ("CONTINUE", "NEXT", "REVIEW") and not has_unanswered_required_questions(driver):
                    print(f"FLOW_ADVANCE:primary_cta={step_name}")
                elif step_name in ("SUBMIT_APPLICATION", "SUBMIT"):
                    print(f"FLOW_ADVANCE:primary_cta={step_name}")
                clicked = False
                if REVALIDATE_TITLE_BEFORE_SUBMIT and phase == "review_submit" and step_name in ("SUBMIT_APPLICATION", "SUBMIT"):
                    final_title_check = revalidate_current_job_title_before_submit(driver)
                    if not final_title_check.get("title_match"):
                        return "title_mismatch_final_check"
                if step_name == "SUBMIT_APPLICATION":
                    clicked = hard_submit_application(driver)
                else:
                    clicked = click_first_match(driver, selectors)
                if clicked:
                    clicked_step = True
                    print(f"FLOW_STEP:{step_name}")
                    advanced = wait_for_step_progress(
                        driver,
                        before_url,
                        before_phase=phase,
                        before_action=before_action,
                        before_signature=before_signature,
                        before_question_state=before_question_state,
                        timeout=max(1.2, DETAIL_LOAD_WAIT * 2.5) if step_name in ("CONTINUE", "NEXT", "REVIEW") else max(2.5, DETAIL_LOAD_WAIT * 5),
                    )
                    if advanced:
                        progressed = True
                        idle_cycles = 0
                        same_page_count = 0
                        same_review_page_count = 0
                    elif step_name in ("SUBMIT_APPLICATION", "SUBMIT", "YES") or phase == "review_submit":
                        same_review_page_count += 1
                        print(f"FLOW_WAIT:same_review_page:{same_review_page_count}")
                    else:
                        same_page_count += 1
                        print(f"FLOW_WAIT:same_page:{same_page_count}")
                    if is_application_submitted(driver):
                        return "submitted"

            if progressed:
                time.sleep(CLICK_PAUSE)
                if is_application_submitted(driver):
                    return "submitted"
                continue

            if clicked_step:
                time.sleep(CLICK_PAUSE)

            if SKIP_ON_UNANSWERED_QUESTIONS and has_unanswered_required_questions(driver):
                print("APPLY_WAIT:required_questions")
                manual_state = wait_for_manual_required_answers(driver)
                if manual_state == "submitted":
                    return "submitted"
                if manual_state == "external":
                    return "external"
                if manual_state == "resolved":
                    idle_cycles = 0
                    continue
                return "blocked_questions"

            idle_cycles += 1
            now = time.time()
            current = driver.current_url
            if idle_cycles == 1 or now - last_wait_log >= 15:
                if is_on_apply_interface(driver):
                    print(f"FLOW_WAIT:in_progress:idle={idle_cycles}:url={current}")
                else:
                    print(f"FLOW_WAIT:awaiting_apply_state:idle={idle_cycles}:url={current}")
                last_wait_log = now
            if MAX_FLOW_STEPS > 0 and idle_cycles >= MAX_FLOW_STEPS:
                return "blocked"
            time.sleep(max(0.5, DETAIL_LOAD_WAIT))
        except Exception as exc:
            raise_session_reconnect(exc, "run_quick_apply_flow")


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
    global LAST_HR_TEXT, LAST_HR_LINK, LAST_JOB_DECISION
    job_url = job_entry["url"]
    job_key = job_entry["key"]
    job_title = job_entry["title"]

    attempts = 2
    for attempt in range(attempts):
        try:
            if attempt > 0:
                print(f"SESSION_RECOVER:retry_job:{job_key}")

            if not verify_driver_session(driver):
                raise SessionReconnectRequired("pre_job_check")

            active_apply_url = ""
            if ACTIVE_APPLY_STATE.get("locked") and ACTIVE_APPLY_STATE.get("job_key") == job_key:
                active_apply_url = ACTIVE_APPLY_STATE.get("apply_url") or ""
            target_url = active_apply_url or job_url

            print(f"OPEN:{idx}:{job_title}")
            close_disallowed_seek_tabs(driver, fallback_url=target_url)
            driver.get(target_url)
            wait_for_security_verification(driver)
            guard_current_page_against_disallowed(driver, target_url)
            time.sleep(DETAIL_LOAD_WAIT)

            if not ACTIVE_APPLY_STATE.get("locked") and not ensure_job_detail_page(driver, job_url):
                if SHOW_SKIP_REASONS:
                    print(f"SKIP_INVALID_JOB_PAGE:{job_key}")
                stats["failed"] += 1
                append_evaluation_log(job_url, "Unknown", job_title, "FAILED", "failed_invalid_job_page")
                append_apply_log("Unknown", job_title, job_url, "failed_invalid_job_page", "", "")
                clear_active_apply_state()
                return job_key, driver

            company_name, position = extract_company_and_position(driver, job_title)
            set_active_job_context(company_name, position)
            client_context = build_client_context()

            if SKIP_EXTERNAL and is_external_apply(driver):
                if SHOW_SKIP_REASONS:
                    print(f"SKIP_EXTERNAL:{job_key}")
                stats["skipped_external"] += 1
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_external")
                append_apply_log(company_name, position, job_url, "skipped_external", "", "")
                clear_active_apply_state()
                return job_key, driver

            title_text, detail_text = get_job_text_snapshot(driver)
            LAST_HR_TEXT = build_hr_context_text(driver, title_text, detail_text)
            LAST_HR_LINK = extract_hr_profile_link(driver)
            filter_result = evaluate_configured_job_filters(title_text, detail_text)
            log_title_match_result(filter_result.get("title_match_result") or {})
            log_filter_result(job_key, title_text, filter_result)

            match_result = evaluate_match(title_text, detail_text)
            log_match_result(job_key, title_text, match_result)
            decision_data = build_job_decision(
                job_key,
                job_url,
                company_name,
                title_text,
                detail_text,
                filter_result,
                match_result,
                list_quick_apply=job_entry.get("list_quick_apply", False),
                already_applied=job_entry.get("list_applied", False) or is_already_applied(driver),
                duplicate=False,
                external_apply=False,
                client_context=client_context,
            )
            LAST_JOB_DECISION = decision_data
            log_job_decision(job_key, decision_data)

            if decision_data["final_action"] == "SKIP_ALREADY_APPLIED":
                if SHOW_SKIP_REASONS:
                    print(
                        "SKIP_DECISION:"
                        f"reason={decision_data['decision_reason']} "
                        f"hard_fail={decision_data['hard_fail_reason']}"
                    )
                stats["skipped_applied"] += 1
                append_evaluation_log(
                    job_url,
                    company_name,
                    position,
                    "SKIP",
                    decision_data["final_action"],
                    filter_result=filter_result,
                    match_result=match_result,
                    decision_data=decision_data,
                )
                return job_key, driver

            if decision_data["fit_decision"] == "INELIGIBLE":
                if SHOW_SKIP_REASONS:
                    print(
                        "SKIP_DECISION:"
                        f"reason={decision_data['decision_reason']} "
                        f"hard_fail={decision_data['hard_fail_reason']}"
                    )
                if decision_data["final_action"] == "SKIP_LOW_RELEVANCE":
                    stats["skipped_low_match"] += 1
                else:
                    stats["skipped_filtered"] += 1
                append_evaluation_log(
                    job_url,
                    company_name,
                    position,
                    "SKIP",
                    decision_data["decision_reason"],
                    filter_result=filter_result,
                    match_result=match_result,
                    decision_data=decision_data,
                )
                return job_key, driver

            if SHOW_MATCH_DETAILS and MATCHING_ENABLED and not match_result["eligible"]:
                print(
                    "MATCH_LEGACY_NONBLOCKING:"
                    f"score={match_result['score']} "
                    f"min={MIN_MATCH_SCORE} "
                    f"missing={match_result['missing_must_have']} "
                    f"excluded={match_result['excluded_term_hit']}"
                )
            elif not MATCHING_ENABLED and SHOW_MATCH_DETAILS:
                print("MATCH_BYPASS:matching.enabled=False")

            artifact_state = {"before_screenshot": capture_job_start_screenshot(driver, job_key)}

            apply_state = click_apply(driver, job_url, is_quick_apply=job_entry.get("list_quick_apply", False), expected_title=position)
            if apply_state == "external_precheck":
                print(f"SKIP_EXTERNAL_PRECHECK:{job_key}")
                stats["skipped_external"] += 1
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_external_precheck", filter_result=filter_result, match_result=match_result)
                append_apply_log(company_name, position, job_url, "skipped_external_precheck", "", "")
                return job_key, driver

            if apply_state == "external_target":
                print(f"SKIP_EXTERNAL_TARGET:{job_key}")
                stats["skipped_external"] += 1
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_external_target", filter_result=filter_result, match_result=match_result)
                append_apply_log(company_name, position, job_url, "skipped_external_target", "", "")
                return job_key, driver

            if apply_state == "external_target_closed_tab":
                print(f"SKIP_EXTERNAL_TARGET:{job_key}:closed_tab")
                stats["skipped_external"] += 1
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_external_target_closed_tab", filter_result=filter_result, match_result=match_result)
                append_apply_log(company_name, position, job_url, "skipped_external_target_closed_tab", "", "")
                return job_key, driver

            if apply_state in ("not_found", "not_quick_apply"):
                print(f"SKIP_NO_QUICK_APPLY:{job_key}")
                stats["skipped_no_quick_apply"] += 1
                decision_data = dict(decision_data)
                decision_data["quick_apply_available"] = False
                decision_data["application_method_status"] = "NO_QUICK_APPLY"
                decision_data["final_action"] = "SKIP_NO_QUICK_APPLY"
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_no_quick_apply", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "skipped_no_quick_apply", "", "")
                return job_key, driver

            if apply_state == "visible_but_not_opened":
                print(f"FAILED:{job_key}:quick_apply_click")
                stats["failed"] += 1
                decision_data = dict(decision_data)
                decision_data["quick_apply_available"] = True
                decision_data["application_method_status"] = "QUICK_APPLY"
                decision_data["final_action"] = "APPLICATION_FAILED"
                append_evaluation_log(job_url, company_name, position, "FAILED", "FAILED_QUICK_APPLY_CLICK", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "failed_quick_apply_click", "", "")
                return job_key, driver

            if not is_on_apply_interface(driver) and not wait_for_apply_interface(driver, timeout=max(6, WAIT_TIMEOUT)):
                print(f"FAILED:{job_key}:quick_apply_interface_not_opened")
                stats["failed"] += 1
                clear_active_apply_state()
                append_evaluation_log(job_url, company_name, position, "FAILED", "failed_quick_apply_interface", filter_result=filter_result, match_result=match_result)
                append_apply_log(company_name, position, job_url, "failed_quick_apply_interface", "", "")
                return job_key, driver

            refresh_active_apply_state(driver, job_key=job_key, job_url=job_url)

            if not AUTO_SUBMIT_ENABLED:
                print("AUTO_SUBMIT_DISABLED")
                append_evaluation_log(job_url, company_name, position, "APPLY", "auto_submit_disabled", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "auto_submit_disabled", "", "")
                return job_key, driver

            result = run_quick_apply_flow(driver, job_key=job_key, artifact_state=artifact_state)
            if result == "submitted":
                if not confirm_application_submission(driver, timeout=max(6, WAIT_TIMEOUT)):
                    remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                    print(f"FAILED:{job_key}:submission_not_confirmed")
                    stats["failed"] += 1
                    append_evaluation_log(job_url, company_name, position, "FAILED", "failed_submission_not_confirmed", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                    clear_active_apply_state()
                    return job_key, driver

                print(f"SUBMITTED:{job_key}")
                stats["applied"] += 1
                TODAY_SUBMITTED_JOB_KEYS.add(job_key)
                before_shot, shot = finalize_submission_screenshots(driver, artifact_state.get("before_screenshot", ""), job_key)
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
                artifact_check = verify_submission_artifacts(job_url, before_shot, shot)
                print(
                    "POST_SUBMIT_VERIFY:"
                    f"ok={artifact_check['ok']} "
                    f"before={artifact_check['before_ok']} "
                    f"after={artifact_check['after_ok']} "
                    f"csv_rows={artifact_check['row_count']} "
                    f"issues={artifact_check['issues']}"
                )
                decision_data = dict(decision_data)
                decision_data["artifact_verification"] = artifact_check
                append_evaluation_log(
                    job_url,
                    company_name,
                    position,
                    "APPLY",
                    "submitted_verified" if artifact_check["ok"] else "submitted_with_artifact_issues",
                    filter_result=filter_result,
                    match_result=match_result,
                    decision_data=decision_data,
                )
                clear_active_apply_state()
            elif result == "external":
                remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                print(f"SKIP_EXTERNAL:{job_key}")
                stats["skipped_external"] += 1
                append_evaluation_log(job_url, company_name, position, "SKIP", "skipped_external", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "skipped_external", "", "")
                clear_active_apply_state()
            elif result == "title_mismatch_final_check":
                remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                print(f"SKIP_TITLE_MISMATCH_FINAL_CHECK:{job_key}")
                stats["skipped_filtered"] += 1
                decision_data = dict(decision_data)
                decision_data["final_action"] = "SKIP_TITLE_MISMATCH_FINAL_CHECK"
                decision_data["decision_reason"] = "SKIP_TITLE_MISMATCH_FINAL_CHECK"
                append_evaluation_log(job_url, company_name, position, "SKIP", "SKIP_TITLE_MISMATCH_FINAL_CHECK", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "skipped_title_mismatch_final_check", "", "")
                clear_active_apply_state()
            elif result == "blocked_questions":
                remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                print(f"FAILED:{job_key}:blocked_questions")
                stats["failed"] += 1
                append_evaluation_log(job_url, company_name, position, "FAILED", "failed_blocked_questions", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "failed_blocked_questions", "", "")
                clear_active_apply_state()
            elif result == "resume_upload_failed":
                remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                print(f"FAILED:{job_key}:resume_upload")
                stats["failed"] += 1
                append_evaluation_log(job_url, company_name, position, "FAILED", "failed_resume_upload", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "failed_resume_upload", "", "")
                clear_active_apply_state()
            else:
                remove_screenshot_file(artifact_state.get("before_screenshot", ""))
                print(f"FAILED:{job_key}:blocked_or_incomplete")
                stats["failed"] += 1
                append_evaluation_log(job_url, company_name, position, "FAILED", "failed_blocked_or_incomplete", filter_result=filter_result, match_result=match_result, decision_data=decision_data)
                append_apply_log(company_name, position, job_url, "failed_blocked_or_incomplete", "", "")
                clear_active_apply_state()

            return job_key, driver
        except SessionReconnectRequired as exc:
            context = str(exc) or "session_reconnect"
        except Exception as exc:
            if is_session_recoverable_error(exc):
                context = "unexpected_session_loss"
            else:
                print(f"FAILED:{job_key}:unexpected:{exc}")
                stats["failed"] += 1
                append_evaluation_log(job_url, "Unknown", job_title, "FAILED", "failed_unexpected")
                append_apply_log("Unknown", job_title, job_url, "failed_unexpected", "", "")
                clear_active_apply_state()
                return job_key, driver

        if attempt + 1 >= attempts:
            print(f"FAILED:session_reconnect:{job_key}:{context}")
            stats["failed"] += 1
            append_evaluation_log(job_url, "Unknown", job_title, "FAILED", "failed_session_reconnect")
            append_apply_log("Unknown", job_title, job_url, "failed_session_reconnect", "", "")
            clear_active_apply_state()
            return job_key, driver

        resume_url = ACTIVE_APPLY_STATE.get("apply_url") if ACTIVE_APPLY_STATE.get("locked") and ACTIVE_APPLY_STATE.get("job_key") == job_key else ""
        if resume_url:
            print("SESSION_RECOVER:resume_active_apply")
        recovered_driver = reattach_debug_driver(driver, job_url=resume_url or job_url, context=f"{job_key}:{context}")
        if recovered_driver is None:
            print(f"FAILED:session_reconnect:{job_key}:{context}")
            stats["failed"] += 1
            append_apply_log("Unknown", job_title, job_url, "failed_session_reconnect", "", "")
            clear_active_apply_state()
            return job_key, driver
        driver = recovered_driver

    return job_key, driver


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
    global TODAY_SUBMITTED_JOB_KEYS
    TODAY_SUBMITTED_JOB_KEYS = load_today_submitted_job_keys()

    stats = {
        "pages": 0,
        "scanned": 0,
        "applied": 0,
        "skipped_filtered": 0,
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
        empty_page_retries = 0

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
            print(f"PAGE:url_page={pages_in_this_search}:global_page={stats['pages']}:jobs={len(entries)}")
            if not entries:
                if empty_page_retries < 1:
                    empty_page_retries += 1
                    print("PAGE_EMPTY:retry")
                    driver.get(results_page_url)
                    wait_for_results_page_ready(driver)
                    time.sleep(PAGE_LOAD_WAIT)
                    continue
                break
            empty_page_retries = 0

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

                if should_skip_previously_submitted_job(key):
                    print(f"SKIP_APPLIED_TODAY:{key}")
                    stats["scanned"] += 1
                    scanned_this_search += 1
                    stats["skipped_applied"] += 1
                    processed_global.add(key)
                    page_processed += 1
                    continue

                stats["scanned"] += 1
                scanned_this_search += 1
                result_key, driver = process_job_url(driver, entry, idx, stats)
                processed_global.add(result_key or key)
                page_processed += 1

                if ACTIVE_APPLY_STATE.get("locked"):
                    continue

                try:
                    driver.get(results_page_url)
                    wait_for_security_verification(driver)
                    guard_current_page_against_disallowed(driver, results_page_url)
                    time.sleep(PAGE_LOAD_WAIT)
                except Exception as exc:
                    if is_session_recoverable_error(exc):
                        resume_url = ACTIVE_APPLY_STATE.get("apply_url") if ACTIVE_APPLY_STATE.get("locked") else ""
                        if resume_url:
                            print("SESSION_RECOVER:resume_active_apply")
                        recovered_driver = reattach_debug_driver(driver, job_url=resume_url or results_page_url, context="results_page")
                        if recovered_driver is None:
                            print("STOP:results_page_session_lost")
                            return
                        driver = recovered_driver
                    else:
                        raise

            if apply_cap_reached(stats):
                break

            if MAX_JOBS_PER_RUN > 0 and scanned_this_search >= MAX_JOBS_PER_RUN:
                break

            if page_processed == 0:
                break

            if MAX_PAGES_PER_SEARCH > 0 and pages_in_this_search >= MAX_PAGES_PER_SEARCH:
                print(f"STOP:max_pages_per_search_reached:url_page={pages_in_this_search}")
                break

            if not go_to_next_results_page(driver):
                print(f"SEARCH_EXHAUSTED:url_page={pages_in_this_search}")
                break

        per_url_end = dict(stats)
        print(
            "SEARCH_DONE:"
            f"url={search_url} "
            f"scanned={per_url_end['scanned'] - per_url_start['scanned']} "
            f"applied={per_url_end['applied'] - per_url_start['applied']} "
            f"skip_filtered={per_url_end['skipped_filtered'] - per_url_start['skipped_filtered']} "
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
        f"skip_filtered={stats['skipped_filtered']} "
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
        if PROMPT_BEFORE_RUN:
            prepare_for_manual_login(driver)
        if QUICK_APPLY_ONLY:
            print("QUICK_ONLY_MODE:on")
        print("Connected successfully")
        print("Current title:", get_driver_title_safe(driver))
        print("Current URL:", get_driver_current_url_safe(driver))

        if PROMPT_BEFORE_RUN:
            print("Login ke liye browser ready hai.")
            print("Login complete hone ke baad hi automation start hogi.")
            safe_input("Agar login already ho chuka hai to Enter dabao... ")
        run_continuous(driver)
        if PROMPT_AFTER_RUN:
            safe_input("Script finished. Enter dabao...")
    except Exception as e:
        print("ERROR:", e)
        if PROMPT_ON_ERROR:
            safe_input("Enter dabao...")


if __name__ == "__main__":
    main()

























