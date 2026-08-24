import unittest
from unittest import mock
import os

from SeekBot import (
    ACTIVE_JOB_CONTEXT,
    ACTIVE_APPLY_STATE,
    answer_common_input_questions,
    append_apply_log,
    append_quick_apply_debug,
    build_client_context,
    build_job_decision,
    click_apply,
    confirm_application_submission,
    detect_quick_apply,
    extract_browser_version,
    extract_company_and_position,
    extract_experience_requirements,
    infer_job_role_relationship,
    parse_search_url_context,
    should_skip_previously_submitted_job,
    build_cover_letter_text,
    count_applied_rows_for_job,
    close_disallowed_seek_tabs,
    ensure_job_detail_page,
    evaluate_configured_job_filters,
    find_local_chromedriver,
    find_seek_window_handle,
    MANUAL_QUESTION_TIMEOUT,
    guard_current_page_against_disallowed,
    get_configured_answer_for_context,
    get_job_entries,
    get_job_text_snapshot,
    is_external_apply,
    is_on_apply_interface,
    is_security_verification_page,
    wait_for_security_verification,
    prepare_active_application,
    rewrite_cover_letter_for_current_job,
    should_prepare_active_application,
    select_resume_if_present,
    switch_to_new_tab_if_any,
    verify_submission_artifacts,
    wait_for_manual_required_answers,
    get_next_screenshot_path,
    run_quick_apply_flow,
    capture_job_start_screenshot,
    is_job_start_screenshot_ready,
    wait_for_job_start_screenshot_ready,
    finalize_submission_screenshots,
)
from config import CONFIG


class ConfigDrivenFilterTests(unittest.TestCase):
    class _FakeElement:
        def __init__(self, text="", displayed=True, on_click=None):
            self.text = text
            self._displayed = displayed
            self._on_click = on_click

        def is_displayed(self):
            return self._displayed

        def click(self):
            if self._on_click:
                self._on_click()

    class _FakeDriver:
        def __init__(self):
            self.expanded = False
            self.scrolled = False

        def execute_script(self, script, elem):
            if "scrollIntoView" in script:
                self.scrolled = True
            return None

        def find_elements(self, by, value):
            if value == "//h1":
                return [ConfigDrivenFilterTests._FakeElement("Business Development Manager")]
            if value == "//*[@data-automation='job-detail-title']":
                return []
            if value == "//*[@data-automation='jobAdDetails']":
                text = "Short summary only"
                if self.expanded:
                    text = "Short summary only Full description with Sydney NSW sales full time"
                return [ConfigDrivenFilterTests._FakeElement(text)]
            if "show more" in value or "read more" in value:
                return [
                    ConfigDrivenFilterTests._FakeElement(
                        "Show more",
                        on_click=lambda: setattr(self, "expanded", True),
                    )
                ]
            return []

    class _FakeSwitchTo:
        def __init__(self, driver):
            self.driver = driver

        def window(self, handle):
            self.driver.current_window_handle = handle
            self.driver.current_url = self.driver.urls[handle]

    class _FakeTabDriver:
        def __init__(self, urls, current_handle):
            self.urls = urls
            self.window_handles = list(urls.keys())
            self.current_window_handle = current_handle
            self.current_url = urls[current_handle]
            self.switch_to = ConfigDrivenFilterTests._FakeSwitchTo(self)
            self.closed_handles = []
            self.got_urls = []

        def close(self):
            self.closed_handles.append(self.current_window_handle)
            handle = self.current_window_handle
            if handle in self.window_handles:
                self.window_handles.remove(handle)
            if handle in self.urls:
                del self.urls[handle]
            if self.window_handles:
                fallback = self.window_handles[0]
                self.current_window_handle = fallback
                self.current_url = self.urls[fallback]

        def get(self, url):
            self.got_urls.append(url)
            self.current_url = url
            self.urls[self.current_window_handle] = url

    class _FakeJobPageDriver:
        def __init__(self, initial_url):
            self.current_url = initial_url
            self.got_urls = []
            self.title = ""

        def get(self, url):
            self.got_urls.append(url)
            self.current_url = url

        def find_element(self, by, value):
            raise RuntimeError("not implemented")

    def test_job_filters_accept_matching_text(self):
        result = evaluate_configured_job_filters(
            "Business Development Manager",
            "Full time role based in Sydney NSW with strong sales background.",
            required_keywords=["business development", "account executive", "sales manager"],
            filters={},
        )
        self.assertTrue(result["eligible"])

    def test_extract_browser_version_reads_browser_field(self):
        self.assertEqual(
            extract_browser_version({"Browser": "Chrome/151.0.7922.140"}),
            "151.0.7922.140",
        )

    @mock.patch("SeekBot.os.walk")
    @mock.patch("SeekBot.os.path.isdir", return_value=True)
    def test_find_local_chromedriver_prefers_matching_browser_major(
        self,
        _mock_isdir,
        mock_walk,
    ):
        mock_walk.side_effect = [
            [
                ("C:\\cache\\151.0.7922.138", [], ["chromedriver.exe"]),
                ("C:\\cache\\150.0.7871.124", [], ["chromedriver.exe"]),
            ],
            [
                ("C:\\wdm\\147.0.7727.117", [], ["chromedriver.exe"]),
            ],
        ]
        selected = find_local_chromedriver("151.0.7922.140")
        self.assertIn("151.0.7922.138", selected)

    def test_job_filters_reject_when_no_required_terms_match(self):
        result = evaluate_configured_job_filters(
            "Operations Analyst",
            "Melbourne based contract role for enterprise partnerships.",
            required_keywords=["business development", "account executive", "sales manager"],
        )
        self.assertFalse(result["eligible"])
        self.assertIn("business development", [item.lower() for item in result["missing_required"]])

    def test_job_filters_allow_single_matching_role_keyword_from_config_style_list(self):
        result = evaluate_configured_job_filters(
            "Project Coordinator",
            "Construction role in Melbourne VIC with full time hours.",
            required_keywords=[
                "Site Supervisor",
                "Junior Project Manager",
                "Contract Administrator",
                "Project Coordinator",
                "Site Manager",
            ],
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_reject_when_salary_floor_exceeds_expected_salary(self):
        result = evaluate_configured_job_filters(
            "Quantity Surveyor / Senior Quantity Surveyor",
            "Ringwood Melbourne VIC Hybrid Full time $90,000 to $140,000 + super",
            required_keywords=["Quantity Surveyor"],
            filters={"expected_salary": [80000], "job_type": ["full time"]},
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("salary" in reason.lower() for reason in result["rejection_reasons"]))

    def test_job_filters_reject_when_required_experience_exceeds_user_experience(self):
        result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role requiring 5 years experience in utilities.",
            required_keywords=["Project Engineer"],
            filters={"experience": ["3 years"], "job_type": ["full time"]},
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("experience" in reason.lower() for reason in result["rejection_reasons"]))

    def test_job_filters_allow_when_required_experience_is_less_than_or_equal_to_user_experience(self):
        result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role requiring 3 years experience in utilities.",
            required_keywords=["Project Engineer"],
            filters={"experience": ["5 years"], "job_type": ["full time"]},
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_allow_when_salary_is_not_listed(self):
        result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role requiring 3 years experience in utilities.",
            required_keywords=["Project Engineer"],
            filters={"experience": ["5 years"], "expected_salary": [120000], "job_type": ["full time"]},
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_allow_when_salary_is_within_tolerance(self):
        result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role paying $121,000 plus super.",
            required_keywords=["Project Engineer"],
            filters={"expected_salary": [120000], "job_type": ["full time"]},
        )
        self.assertTrue(result["eligible"])

    def test_extract_experience_requirements_parses_range(self):
        info = extract_experience_requirements(
            "Project Engineer",
            "Sydney role requiring 3-5 years experience in utilities.",
        )
        self.assertTrue(info["mentioned"])
        self.assertEqual(info["minimum"], 3)
        self.assertEqual(info["maximum"], 5)

    def test_build_job_decision_hard_fail_overrides_score(self):
        filter_result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role requiring 6 years experience paying $121,000 plus super.",
            required_keywords=["Project Engineer"],
            filters={"experience": ["5 years"], "expected_salary": [120000], "job_type": ["full time"]},
        )
        decision = build_job_decision(
            "https://au.seek.com/job/1",
            "https://au.seek.com/job/1",
            "Test Co",
            "Project Engineer",
            "Sydney full time role requiring 6 years experience paying $121,000 plus super.",
            filter_result,
            {"score": 0, "eligible": True, "matched_must_have": [], "matched_preferred": [], "missing_must_have": [], "excluded_term_hit": []},
            list_quick_apply=True,
        )
        self.assertEqual(decision["decision"], "SKIP")
        self.assertTrue(decision["hard_fail"])
        self.assertIn("SKIP_EXPERIENCE_TOO_HIGH", decision["hard_fail_reasons"])

    def test_build_job_decision_allows_unknown_salary_with_strong_role_match(self):
        client_context = build_client_context(
            search_urls=["https://au.seek.com/project-engineer-jobs?keywords=Project%20Engineer"],
            filters={"keywords": ["Project Engineer"], "location": ["Sydney"], "job_type": ["full time"], "experience": ["5 years"]},
            profile_keywords={"must_have": ["utilities", "project delivery"], "preferred": ["engineering"]},
            client_id="project-engineer-client",
        )
        filter_result = evaluate_configured_job_filters(
            "Project Engineer",
            "Sydney full time role requiring 3 years experience in utilities.",
            required_keywords=["Project Engineer"],
            filters={"experience": ["5 years"], "job_type": ["full time"]},
        )
        decision = build_job_decision(
            "https://au.seek.com/job/2",
            "https://au.seek.com/job/2",
            "Test Co",
            "Project Engineer",
            "Sydney full time role requiring 3 years experience in utilities.",
            filter_result,
            {"score": 0, "eligible": True, "matched_must_have": [], "matched_preferred": [], "missing_must_have": [], "excluded_term_hit": []},
            list_quick_apply=True,
            client_context=client_context,
        )
        self.assertEqual(decision["salary_match"], "UNKNOWN")
        self.assertEqual(decision["decision"], "APPLY")

    def test_quick_apply_availability_is_separate_from_fit_decision(self):
        client_context = build_client_context(
            search_urls=["https://au.seek.com/software-developer-jobs"],
            filters={"keywords": ["Software Developer"], "job_type": [], "location": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["python", "sql"], "preferred": ["api"]},
            client_id="software-client",
        )
        filter_result = evaluate_configured_job_filters(
            "Backend Developer",
            "Python SQL API backend developer with 3 years experience.",
            required_keywords=["Software Developer"],
            filters={"job_type": [], "location": [], "experience": ["5 years"]},
        )
        decision = build_job_decision(
            "job-qa-sep",
            "https://au.seek.com/job/qa-sep",
            "Test Co",
            "Backend Developer",
            "Python SQL API backend developer with 3 years experience.",
            filter_result,
            {"score": 12, "eligible": True, "matched_must_have": ["python"], "matched_preferred": ["api"], "missing_must_have": [], "excluded_term_hit": []},
            list_quick_apply=False,
            client_context=client_context,
        )
        self.assertIn(decision["fit_decision"], ["STRONG_MATCH", "ELIGIBLE", "BORDERLINE"])
        self.assertEqual(decision["final_action"], "APPLY")
        self.assertEqual(decision["application_method_status"], "UNKNOWN")

    class _FakeQuickApplyElement:
        def __init__(self, text="", tag="button", href="", aria_label="", title="", data_automation="", data_testid="", displayed=True, enabled=True):
            self.text = text
            self.tag_name = tag
            self._href = href
            self._aria_label = aria_label
            self._title = title
            self._data_automation = data_automation
            self._data_testid = data_testid
            self._displayed = displayed
            self._enabled = enabled
            self.clicked = False
            self.id = id(self)

        def is_displayed(self):
            return self._displayed

        def is_enabled(self):
            return self._enabled

        def get_attribute(self, name):
            mapping = {
                "href": self._href,
                "aria-label": self._aria_label,
                "title": self._title,
                "data-automation": self._data_automation,
                "data-testid": self._data_testid,
                "role": "button" if self.tag_name == "div" else "",
                "name": "",
                "type": "button",
            }
            return mapping.get(name, "")

        def click(self):
            self.clicked = True

    class _FakeQuickApplyDriver:
        def __init__(self, candidates=None):
            self.current_url = "https://au.seek.com/job/12345"
            self.current_window_handle = "main"
            self.window_handles = ["main"]
            self._candidates = candidates or []

        def find_elements(self, by, value):
            if value in ["//*[@data-automation='job-detail-title']", "//*[@data-testid='job-title']", "//h1"]:
                return [ConfigDrivenFilterTests._FakeElement("Project Engineer")]
            if value in ["//*[@data-automation='jobAdDetails']", "//main"]:
                return [ConfigDrivenFilterTests._FakeElement("Job details")]
            return self._candidates

        def execute_script(self, script, *args):
            if "click" in script and args:
                try:
                    args[0].click()
                except Exception:
                    pass
            return None

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_finds_button_text(self, _mock_ready):
        driver = self._FakeQuickApplyDriver([self._FakeQuickApplyElement(text="Quick apply")])
        result = detect_quick_apply(driver, {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertTrue(result["available"])
        self.assertEqual(result["method"], "visible_text")

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_finds_anchor_text(self, _mock_ready):
        driver = self._FakeQuickApplyDriver([self._FakeQuickApplyElement(text="Quick Apply", tag="a")])
        result = detect_quick_apply(driver, {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertTrue(result["available"])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_finds_role_button_text(self, _mock_ready):
        driver = self._FakeQuickApplyDriver([self._FakeQuickApplyElement(text="QUICK APPLY", tag="div")])
        result = detect_quick_apply(driver, {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertTrue(result["available"])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_handles_nested_like_text_spacing(self, _mock_ready):
        driver = self._FakeQuickApplyDriver([self._FakeQuickApplyElement(text="  Quick \n   apply  ")])
        result = detect_quick_apply(driver, {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertTrue(result["available"])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_ignores_hidden_quick_apply_and_does_not_click_save(self, _mock_ready):
        quick_hidden = self._FakeQuickApplyElement(text="Quick apply", displayed=False)
        save_visible = self._FakeQuickApplyElement(text="Save", displayed=True)
        driver = self._FakeQuickApplyDriver([quick_hidden, save_visible])
        result = detect_quick_apply(driver, {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertFalse(result["available"])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_detect_quick_apply_waits_for_delayed_appearance(self, _mock_ready):
        quick = self._FakeQuickApplyElement(text="Quick apply")
        class DelayedDriver(self._FakeQuickApplyDriver):
            def __init__(self):
                super().__init__([])
                self.calls = 0
            def find_elements(self, by, value):
                if value in ["//*[@data-automation='job-detail-title']", "//*[@data-testid='job-title']", "//h1", "//*[@data-automation='jobAdDetails']", "//main"]:
                    return super().find_elements(by, value)
                self.calls += 1
                return [] if self.calls < 3 else [quick]
        result = detect_quick_apply(DelayedDriver(), {"job_url": "https://au.seek.com/job/12345", "title": "Project Engineer"})
        self.assertTrue(result["available"])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://au.seek.com/job/12345", "job_key": "https://au.seek.com/job/12345", "title": "Project Engineer"}})
    def test_search_card_hint_absent_but_detail_page_quick_apply_still_opens(self, _mock_ready):
        btn = self._FakeQuickApplyElement(text="Quick apply", data_automation="job-detail-apply")
        driver = self._FakeQuickApplyDriver([btn])
        with mock.patch("SeekBot.detect_and_lock_seek_apply_page", return_value=True):
            result = click_apply(driver, "https://au.seek.com/job/12345", is_quick_apply=False, expected_title="Project Engineer")
        self.assertEqual(result, "opened")
        self.assertTrue(btn.clicked)

    def test_parse_search_url_context_extracts_dynamic_signals(self):
        parsed = parse_search_url_context(
            "https://au.seek.com/software-developer-jobs-in-information-communication-technology/in-All-Sydney-NSW?salaryrange=100000-130000&keywords=Python%20Developer"
        )
        self.assertTrue(parsed["search_phrases"])
        self.assertIn("python developer", [item.lower() for item in parsed["search_phrases"]])

    def test_build_client_context_for_software_client(self):
        context = build_client_context(
            search_urls=["https://au.seek.com/software-developer-jobs-in-information-communication-technology/in-All-Sydney-NSW?keywords=Python%20Developer"],
            filters={"keywords": ["Software Developer"], "related_roles": ["Backend Developer"], "location": ["Sydney"], "job_type": ["full time"], "experience": ["5 years"]},
            profile_keywords={"must_have": ["python", "sql"], "preferred": ["apis"]},
            client_id="software-client",
        )
        self.assertIn("Software Developer", context["target_roles"])
        self.assertIn("Backend Developer", context["historical_roles"])
        self.assertIn("python", [item.lower() for item in context["skills"]])

    def test_dynamic_software_client_marks_backend_related_and_accountant_unrelated(self):
        context = build_client_context(
            search_urls=["https://au.seek.com/software-developer-jobs?keywords=Software%20Developer"],
            filters={"keywords": ["Software Developer"], "related_roles": [], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["python", "sql", "api"], "preferred": ["backend"]},
            client_id="software-client",
        )
        related = infer_job_role_relationship(
            context,
            "Backend Developer",
            "Python SQL API backend development role building services.",
        )
        unrelated = infer_job_role_relationship(
            context,
            "Accountant",
            "Financial reporting, BAS, taxation and ledger reconciliation.",
        )
        self.assertIn(related["relationship"], ["DIRECT", "RELATED"])
        self.assertEqual(unrelated["relationship"], "UNRELATED")

    def test_dynamic_construction_client_marks_site_supervisor_related_and_frontend_unrelated(self):
        context = build_client_context(
            search_urls=["https://au.seek.com/site-manager-jobs?keywords=Site%20Manager"],
            filters={"keywords": ["Site Manager"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["subcontractor coordination", "whs", "scheduling"], "preferred": ["construction"]},
            client_id="construction-client",
        )
        related = infer_job_role_relationship(
            context,
            "Site Supervisor",
            "Construction supervision, WHS, subcontractor coordination and scheduling.",
        )
        unrelated = infer_job_role_relationship(
            context,
            "Frontend Developer",
            "React JavaScript UI development and web accessibility.",
        )
        self.assertIn(related["relationship"], ["DIRECT", "RELATED"])
        self.assertEqual(unrelated["relationship"], "UNRELATED")

    def test_dynamic_customer_service_client_marks_support_related_and_civil_unrelated(self):
        context = build_client_context(
            search_urls=["https://au.seek.com/customer-service-representative-jobs?keywords=Customer%20Service"],
            filters={"keywords": ["Customer Service Representative"], "location": [], "job_type": [], "experience": ["4 years"]},
            profile_keywords={"must_have": ["customer support", "crm", "call handling"], "preferred": ["complaint resolution"]},
            client_id="service-client",
        )
        related = infer_job_role_relationship(
            context,
            "Customer Support Officer",
            "Customer support, CRM updates, call handling and complaint resolution.",
        )
        unrelated = infer_job_role_relationship(
            context,
            "Civil Engineer",
            "Roads, drainage, stormwater and civil design documentation.",
        )
        self.assertIn(related["relationship"], ["DIRECT", "RELATED"])
        self.assertEqual(unrelated["relationship"], "UNRELATED")

    def test_dynamic_accounting_client_marks_financial_accountant_related_and_call_centre_unrelated(self):
        context = build_client_context(
            search_urls=["https://au.seek.com/accountant-jobs?keywords=Accountant"],
            filters={"keywords": ["Accountant"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["financial reporting", "reconciliation", "tax"], "preferred": ["general ledger"]},
            client_id="accounting-client",
        )
        related = infer_job_role_relationship(
            context,
            "Financial Accountant",
            "Financial reporting, reconciliations, tax compliance and ledger ownership.",
        )
        unrelated = infer_job_role_relationship(
            context,
            "Warehouse Storeperson",
            "Pallet wrapping, forklift loading, dispatch scanning and warehouse picking.",
        )
        self.assertIn(related["relationship"], ["DIRECT", "RELATED"])
        self.assertEqual(unrelated["relationship"], "UNRELATED")

    def test_cross_client_context_isolation_between_electrical_and_software(self):
        electrical = build_client_context(
            search_urls=["https://au.seek.com/electrical-engineer-jobs?keywords=Electrical%20Engineer"],
            filters={"keywords": ["Electrical Engineer"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["switchgear", "power systems"], "preferred": ["protection relays"]},
            client_id="electrical-client",
        )
        software = build_client_context(
            search_urls=["https://au.seek.com/software-developer-jobs?keywords=Software%20Developer"],
            filters={"keywords": ["Software Developer"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["python", "apis"], "preferred": ["sql"]},
            client_id="software-client",
        )
        job_title = "Application Engineer"
        electrical_result = infer_job_role_relationship(
            electrical,
            job_title,
            "Switchgear, electrical protection systems and power distribution applications.",
        )
        software_result = infer_job_role_relationship(
            software,
            job_title,
            "Python APIs SQL backend software implementation and development.",
        )
        self.assertIn(electrical_result["relationship"], ["DIRECT", "RELATED"])
        self.assertIn(software_result["relationship"], ["DIRECT", "RELATED"])
        self.assertNotEqual(electrical["client_id"], software["client_id"])

    def test_ambiguous_application_engineer_depends_on_client_context(self):
        software = build_client_context(
            search_urls=["https://au.seek.com/software-developer-jobs?keywords=Software%20Developer"],
            filters={"keywords": ["Software Developer"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["python", "apis", "sql"], "preferred": ["backend"]},
            client_id="software-client",
        )
        electrical = build_client_context(
            search_urls=["https://au.seek.com/electrical-engineer-jobs?keywords=Electrical%20Engineer"],
            filters={"keywords": ["Electrical Engineer"], "location": [], "job_type": [], "experience": ["5 years"]},
            profile_keywords={"must_have": ["switchgear", "power systems"], "preferred": ["protection relays"]},
            client_id="electrical-client",
        )
        software_jd = infer_job_role_relationship(
            software,
            "Application Engineer",
            "Python APIs SQL backend software deployment and implementation.",
        )
        electrical_jd_for_software = infer_job_role_relationship(
            software,
            "Application Engineer",
            "Switchgear, power distribution, relays and electrical systems.",
        )
        electrical_jd = infer_job_role_relationship(
            electrical,
            "Application Engineer",
            "Switchgear, power distribution, relays and electrical systems.",
        )
        self.assertIn(software_jd["relationship"], ["DIRECT", "RELATED"])
        self.assertEqual(electrical_jd_for_software["relationship"], "UNRELATED")
        self.assertIn(electrical_jd["relationship"], ["DIRECT", "RELATED"])

    def test_job_type_is_dynamic_across_clients(self):
        title = "Project Manager"
        detail = "Contract role in Sydney managing delivery milestones."
        allowed = evaluate_configured_job_filters(
            title,
            detail,
            required_keywords=["Project Manager"],
            filters={"job_type": ["contract"], "location": ["Sydney"], "strict_job_type": True},
        )
        blocked = evaluate_configured_job_filters(
            title,
            detail,
            required_keywords=["Project Manager"],
            filters={"job_type": ["full time"], "location": ["Sydney"], "strict_job_type": True},
        )
        open_pref = evaluate_configured_job_filters(
            title,
            detail,
            required_keywords=["Project Manager"],
            filters={"location": ["Sydney"]},
        )
        self.assertTrue(allowed["eligible"])
        self.assertFalse(blocked["eligible"])
        self.assertTrue(open_pref["eligible"])

    def test_location_is_dynamic_across_clients(self):
        title = "Project Coordinator"
        detail = "Sydney hybrid full time construction coordination role."
        sydney = evaluate_configured_job_filters(title, detail, required_keywords=["Project Coordinator"], filters={"location": ["Sydney"], "job_type": ["full time"]})
        melbourne = evaluate_configured_job_filters(title, detail, required_keywords=["Project Coordinator"], filters={"location": ["Melbourne"], "job_type": ["full time"]})
        open_loc = evaluate_configured_job_filters(title, detail, required_keywords=["Project Coordinator"], filters={"job_type": ["full time"]})
        self.assertTrue(sydney["eligible"])
        self.assertFalse(melbourne["eligible"])
        self.assertTrue(open_loc["eligible"])

    def test_salary_is_dynamic_across_clients(self):
        title = "Project Engineer"
        detail = "Sydney full time role paying $100,000 plus super."
        tolerant = evaluate_configured_job_filters(title, detail, required_keywords=["Project Engineer"], filters={"expected_salary": [105000], "job_type": ["full time"]})
        strict = evaluate_configured_job_filters(title, detail, required_keywords=["Project Engineer"], filters={"current_salary": [120000], "job_type": ["full time"]})
        open_salary = evaluate_configured_job_filters(title, detail, required_keywords=["Project Engineer"], filters={"job_type": ["full time"]})
        self.assertTrue(tolerant["eligible"])
        self.assertFalse(strict["eligible"])
        self.assertTrue(open_salary["eligible"])

    @mock.patch("SeekBot.time.sleep")
    def test_confirm_application_submission_waits_for_reliable_confirmation(self, _mock_sleep):
        class Driver:
            pass

        with mock.patch("SeekBot.is_application_submitted", side_effect=[False, False, True]), mock.patch(
            "SeekBot.is_already_applied", return_value=False
        ):
            self.assertTrue(confirm_application_submission(Driver(), timeout=2))

    def test_append_apply_log_prevents_duplicate_submitted_rows(self):
        mock_file = mock.mock_open(read_data="")
        with mock.patch("SeekBot.ensure_log_paths"), mock.patch("SeekBot.extract_hr_details", return_value=("", "", "")), mock.patch(
            "SeekBot.os.path.exists", return_value=False
        ), mock.patch("SeekBot.count_applied_rows_for_job", side_effect=[0, 1]), mock.patch("builtins.open", mock_file):
            with mock.patch("SeekBot.CSV_LOG_PATH", "applied_jobs.csv"):
                first = append_apply_log("Test Co", "Project Engineer", "https://au.seek.com/job/77", "submitted")
                second = append_apply_log("Test Co", "Project Engineer", "https://au.seek.com/job/77", "submitted")
            self.assertTrue(first)
            self.assertFalse(second)

    def test_verify_submission_artifacts_checks_screenshots_and_csv_row(self):
        with mock.patch("SeekBot.os.path.exists", return_value=True), mock.patch(
            "SeekBot.os.path.getsize", return_value=10
        ), mock.patch("SeekBot.count_applied_rows_for_job", return_value=1):
            result = verify_submission_artifacts("https://au.seek.com/job/88", "before.png", "after.png")
        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 1)

    def test_get_next_screenshot_path_uses_datewise_numbering(self):
        target_dir = os.path.join("logs", "screenshots", "2026-08-24", "before")
        with mock.patch("SeekBot.get_screenshot_phase_dir", return_value=target_dir), mock.patch(
            "SeekBot.os.listdir", side_effect=[[], ["1.png", "2.png", "note.txt"]]
        ):
            first_path = get_next_screenshot_path(phase="before")
            third_path = get_next_screenshot_path(phase="before")

        self.assertEqual(first_path, os.path.join(target_dir, "1.png"))
        self.assertEqual(third_path, os.path.join(target_dir, "3.png"))

    def test_is_job_start_screenshot_ready_requires_detail_page_title(self):
        class Driver:
            current_url = "https://www.seek.com.au/job/123"

        with mock.patch("SeekBot.any_visible_selector", return_value=True):
            self.assertTrue(is_job_start_screenshot_ready(Driver()))

        Driver.current_url = "https://www.seek.com.au/job/123/apply"
        with mock.patch("SeekBot.any_visible_selector", return_value=True):
            self.assertFalse(is_job_start_screenshot_ready(Driver()))

    def test_wait_for_job_start_screenshot_ready_waits_until_title_visible(self):
        class Driver:
            current_url = "https://www.seek.com.au/job/123"

            def __init__(self):
                self.scroll_calls = 0

            def execute_script(self, script):
                self.scroll_calls += 1

        driver = Driver()
        with mock.patch("SeekBot.is_job_start_screenshot_ready", side_effect=[False, False, True]), mock.patch(
            "SeekBot.time.sleep"
        ):
            self.assertTrue(wait_for_job_start_screenshot_ready(driver, timeout=2))

        self.assertGreaterEqual(driver.scroll_calls, 2)

    def test_capture_job_start_screenshot_scrolls_to_top_before_capture(self):
        class Driver:
            def execute_script(self, script):
                self.script = script

        driver = Driver()
        with mock.patch("SeekBot.wait_for_job_start_screenshot_ready", return_value=True) as mock_wait, mock.patch(
            "SeekBot.capture_job_screenshot", return_value="before-shot.png"
        ) as mock_capture, mock.patch("SeekBot.time.sleep") as mock_sleep:
            result = capture_job_start_screenshot(driver, "job-1")

        self.assertEqual(result, "before-shot.png")
        self.assertEqual(driver.script, "window.scrollTo(0, 0);")
        mock_wait.assert_called_once()
        mock_sleep.assert_called_once_with(0.2)
        mock_capture.assert_called_once_with(driver, "job-1", "before_apply", phase="pending_before")

    def test_finalize_submission_screenshots_moves_before_to_matching_number(self):
        class Driver:
            pass

        with mock.patch("SeekBot.ensure_log_paths"), mock.patch(
            "SeekBot.get_next_screenshot_path", return_value=os.path.join("logs", "screenshots", "2026-08-24", "after", "1.png")
        ), mock.patch(
            "SeekBot.get_screenshot_phase_dir", return_value=os.path.join("logs", "screenshots", "2026-08-24", "before")
        ), mock.patch(
            "SeekBot.capture_job_screenshot_to_path", return_value=os.path.join("logs", "screenshots", "2026-08-24", "after", "1.png")
        ), mock.patch("SeekBot.os.path.exists", return_value=True), mock.patch("SeekBot.os.replace") as mock_replace:
            before_path, after_path = finalize_submission_screenshots(Driver(), os.path.join("temp", "pending.png"), "job-1")

        self.assertEqual(before_path, os.path.join("logs", "screenshots", "2026-08-24", "before", "1.png"))
        self.assertEqual(after_path, os.path.join("logs", "screenshots", "2026-08-24", "after", "1.png"))
        mock_replace.assert_called_once_with(os.path.join("temp", "pending.png"), os.path.join("logs", "screenshots", "2026-08-24", "before", "1.png"))

    def test_run_quick_apply_flow_does_not_capture_before_screenshot_on_submit_step(self):
        class Driver:
            current_url = "https://www.seek.com.au/apply"

        artifact_state = {"before_screenshot": ""}
        with mock.patch("SeekBot.refresh_active_apply_state"), mock.patch(
            "SeekBot.classify_current_location", return_value="internal"
        ), mock.patch("SeekBot.is_external_apply", return_value=False), mock.patch(
            "SeekBot.is_application_submitted", side_effect=[False, False, True]
        ), mock.patch(
            "SeekBot.get_current_flow_phase", return_value="review_submit"
        ), mock.patch(
            "SeekBot.get_apply_page_signature", return_value="sig"
        ), mock.patch(
            "SeekBot.get_primary_action_name", return_value="SUBMIT_APPLICATION"
        ), mock.patch(
            "SeekBot.should_prepare_active_application", return_value=False
        ), mock.patch(
            "SeekBot.is_employer_questions_step", return_value=False
        ), mock.patch(
            "SeekBot.detect_and_lock_seek_apply_page", return_value=False
        ), mock.patch(
            "SeekBot.get_primary_action_selectors", return_value=["//button"]
        ), mock.patch(
            "SeekBot.any_visible_selector", return_value=True
        ), mock.patch(
            "SeekBot.get_submit_application_selectors", return_value=["//button"]
        ), mock.patch(
            "SeekBot.capture_job_screenshot"
        ) as mock_capture, mock.patch(
            "SeekBot.hard_submit_application", return_value=True
        ), mock.patch(
            "SeekBot.wait_for_step_progress", return_value=True
        ), mock.patch("SeekBot.time.sleep"):
            result = run_quick_apply_flow(Driver(), job_key="job-1", artifact_state=artifact_state)

        self.assertEqual(result, "submitted")
        self.assertEqual(artifact_state["before_screenshot"], "")
        mock_capture.assert_not_called()

    def test_job_filters_reject_when_location_does_not_match_config(self):
        result = evaluate_configured_job_filters(
            "Project Coordinator",
            "Sydney NSW full time construction role",
            required_keywords=["Project Coordinator"],
            filters={"location": ["Melbourne VIC"], "job_type": ["full time"]},
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("location" in reason.lower() for reason in result["rejection_reasons"]))

    def test_context_lookup_returns_expected_salary_answer(self):
        field_name, answers = get_configured_answer_for_context(
            "What is your expected salary for this role?",
            answers={"expected_salary": "95000"},
        )
        self.assertEqual(field_name, "expected_salary")
        self.assertEqual(answers, ["95000"])

    def test_context_lookup_uses_default_expected_salary_from_config(self):
        field_name, answers = get_configured_answer_for_context(
            "What is your expected salary for this role?"
        )
        self.assertEqual(field_name, "expected_salary")
        self.assertEqual(answers, ["80000"])

    def test_context_lookup_uses_default_experience_from_config(self):
        field_name, answers = get_configured_answer_for_context(
            "How many years experience do you have?"
        )
        self.assertEqual(field_name, "experience")
        self.assertEqual(answers, ["3 years"])

    def test_context_lookup_uses_single_job_filters_block_shape(self):
        field_name, answers = get_configured_answer_for_context(
            "What visa do you currently hold?",
            answers={"visa_type": "Temporary visa"},
        )
        self.assertEqual(field_name, "visa_type")
        self.assertEqual(answers, ["Temporary visa"])

    def test_config_keeps_apply_answers_inside_job_filters_only(self):
        self.assertNotIn("form_answers", CONFIG["apply"])
        self.assertIn("visa_type", CONFIG["resume"]["job_filters"])

    def test_context_lookup_can_use_keywords_from_single_filter_block(self):
        field_name, answers = get_configured_answer_for_context(
            "Please add your key skills and keywords",
            answers={"keywords": ["sales", "business development"]},
        )
        self.assertEqual(field_name, "keywords")
        self.assertEqual(answers, ["sales", "business development"])

    def test_context_lookup_can_use_cover_letter_from_single_filter_block(self):
        field_name, answers = get_configured_answer_for_context(
            "Please add a cover letter or message to the employer",
            answers={"cover_letter": "I am excited to apply for this role."},
        )
        self.assertEqual(field_name, "cover_letter")
        self.assertEqual(answers, ["I am excited to apply for this role."])

    def test_build_cover_letter_text_uses_current_job_company_and_position(self):
        letter = build_cover_letter_text(
            "I am writing to express my interest in the position at Company.",
            "Fangda Australia Pty Ltd",
            "Project Coordinator",
        )
        self.assertIn("Project Coordinator at Fangda Australia Pty Ltd", letter)
        self.assertNotIn("position at Company", letter)

    def test_context_lookup_renders_cover_letter_for_active_job_context(self):
        ACTIVE_JOB_CONTEXT["company_name"] = "Fangda Australia Pty Ltd"
        ACTIVE_JOB_CONTEXT["position"] = "Project Coordinator"
        try:
            field_name, answers = get_configured_answer_for_context(
                "Please add a cover letter or message to the employer",
                answers={"cover_letter": "I am writing to express my interest in the position at Company."},
            )
        finally:
            ACTIVE_JOB_CONTEXT["company_name"] = ""
            ACTIVE_JOB_CONTEXT["position"] = ""
        self.assertEqual(field_name, "cover_letter")
        self.assertEqual(
            answers,
            ["I am writing to express my interest in the Project Coordinator at Fangda Australia Pty Ltd."],
        )

    def test_rewrite_cover_letter_for_current_job_replaces_previous_company_and_role(self):
        letter = rewrite_cover_letter_for_current_job(
            "Hi,\n\nI am writing to express my interest in the Customer Service position at Courtside Melbourne.\n\nThank you.",
            "ISS First Response",
            "Customer Service Agent - Emergency Vehicle Response - Melbourne",
        )
        self.assertIn("Customer Service Agent - Emergency Vehicle Response - Melbourne at ISS First Response", letter)
        self.assertNotIn("Courtside Melbourne", letter)

    def test_extract_company_and_position_prefers_current_apply_page_over_fallback(self):
        class Elem:
            def __init__(self, text):
                self.text = text

        class Driver:
            page_source = ""

            def find_elements(self, by, value):
                mapping = {
                    "//*[@data-automation='job-detail-title']": [],
                    "//*[@data-testid='job-title']": [],
                    "//h1[normalize-space(.)!='']": [],
                    "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'applying for')]/following::*[self::h1 or self::h2 or self::div or self::span][normalize-space(.)!=''][1]": [Elem("CUSTOMER SERVICE OFFICER")],
                    "//*[@data-automation='advertiser-name']": [],
                    "//*[@data-testid='advertiser-name']": [],
                    "//a[contains(@href, '/companies/') and normalize-space(.)!='']": [],
                    "//span[contains(@data-automation, 'advertiser') and normalize-space(.)!='']": [],
                    "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'applying for')]/following::*[normalize-space(.)!=''][2]": [Elem("Countrywide Austral Pty Ltd")],
                }
                return mapping.get(value, [])

        company, position = extract_company_and_position(Driver(), "Operations & Administrator Team")
        self.assertEqual(company, "Countrywide Austral Pty Ltd")
        self.assertEqual(position, "CUSTOMER SERVICE OFFICER")

    def test_answer_common_input_questions_rewrites_existing_cover_letter_for_current_job(self):
        class Elem:
            text = ""

            def __init__(self):
                self.value = "Hi,\n\nI am writing to express my interest in the Customer Service position at Courtside Melbourne.\n\nThank you."

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def get_attribute(self, name):
                if name == "value":
                    return self.value
                return ""

            def clear(self):
                self.value = ""

            def send_keys(self, value):
                self.value = value

        class Driver:
            def __init__(self, elem):
                self.elem = elem

            def find_elements(self, by, value):
                return [self.elem]

            def execute_script(self, script, elem, *args):
                if "closest('fieldset, section, form, div')" in script:
                    return "Please add a cover letter or message to the employer"
                if "el.value = value;" in script:
                    elem.value = args[0]
                return None

        elem = Elem()
        ACTIVE_JOB_CONTEXT["company_name"] = "ISS First Response"
        ACTIVE_JOB_CONTEXT["position"] = "Customer Service Agent - Emergency Vehicle Response - Melbourne"
        try:
            changed = answer_common_input_questions(Driver(elem))
        finally:
            ACTIVE_JOB_CONTEXT["company_name"] = ""
            ACTIVE_JOB_CONTEXT["position"] = ""
        self.assertTrue(changed)
        self.assertIn("ISS First Response", elem.value)
        self.assertIn("Customer Service Agent - Emergency Vehicle Response - Melbourne", elem.value)
        self.assertNotIn("Courtside Melbourne", elem.value)

    @mock.patch("SeekBot.SKIP_ALREADY_APPLIED", False)
    def test_previously_submitted_job_is_not_skipped_when_config_disables_it(self):
        self.assertFalse(should_skip_previously_submitted_job("https://au.seek.com/job/123", {"https://au.seek.com/job/123"}))

    @mock.patch("SeekBot.SKIP_ALREADY_APPLIED", True)
    def test_previously_submitted_job_is_skipped_when_config_enables_it(self):
        self.assertTrue(should_skip_previously_submitted_job("https://au.seek.com/job/123", {"https://au.seek.com/job/123"}))

    def test_job_snapshot_expands_description_before_filtering(self):
        title, detail = get_job_text_snapshot(self._FakeDriver())
        self.assertEqual(title, "Business Development Manager")
        self.assertIn("Sydney NSW sales full time", detail)

    def test_job_snapshot_scrolls_to_description_before_reading(self):
        driver = self._FakeDriver()
        _title, _detail = get_job_text_snapshot(driver)
        self.assertTrue(driver.scrolled)

    def test_get_job_entries_falls_back_to_current_seek_job_page(self):
        class Driver:
            current_url = "https://au.seek.com/job/123456?type=standard&ref=search-standalone"

            def find_elements(self, by, value):
                if value == "//*[@data-automation='job-detail-title']":
                    return [ConfigDrivenFilterTests._FakeElement("Project Engineer")]
                return []

        entries = get_job_entries(Driver())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], "https://au.seek.com/job/123456")
        self.assertEqual(entries[0]["title"], "Project Engineer")

    def test_get_job_entries_prioritizes_quick_apply_entries(self):
        class Elem:
            def __init__(self, href, text, card_text):
                self._href = href
                self.text = text
                self._card_text = card_text

            def get_attribute(self, name):
                if name == "href":
                    return self._href
                return ""

            def find_element(self, by, value):
                return ConfigDrivenFilterTests._FakeElement(self._card_text)

        class Driver:
            current_url = "https://au.seek.com/jobs"

            def find_elements(self, by, value):
                if value == "//a[@data-automation='jobTitle' and contains(@href, '/job/')]":
                    return [
                        Elem("https://au.seek.com/job/1", "General Engineer", "General Engineer"),
                        Elem("https://au.seek.com/job/2", "Quick Apply Engineer", "Quick Apply Engineer Quick apply"),
                    ]
                return []

        entries = get_job_entries(Driver())
        self.assertEqual(entries[0]["key"], "https://au.seek.com/job/2")

    def test_get_job_entries_treats_apply_with_seek_as_quick_apply_hint(self):
        class Elem:
            def __init__(self, href, text, card_text):
                self._href = href
                self.text = text
                self._card_text = card_text

            def get_attribute(self, name):
                if name == "href":
                    return self._href
                return ""

            def find_element(self, by, value):
                return ConfigDrivenFilterTests._FakeElement(self._card_text)

        class Driver:
            current_url = "https://au.seek.com/jobs"

            def find_elements(self, by, value):
                if value == "//a[@data-automation='jobTitle' and contains(@href, '/job/')]":
                    return [
                        Elem("https://au.seek.com/job/1", "General Role", "General Role"),
                        Elem("https://au.seek.com/job/2", "Seek Apply Role", "Seek Apply Role Apply with SEEK"),
                    ]
                return []

        entries = get_job_entries(Driver())
        self.assertTrue(entries[0]["list_quick_apply"])
        self.assertEqual(entries[0]["key"], "https://au.seek.com/job/2")

    def test_select_resume_if_present_prefers_topmost_match_without_chasing_lower_duplicates(self):
        class Elem:
            def __init__(self, text, y):
                self.text = text
                self.location = {"y": y}
                self.clicked = False

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicked = True

            def get_attribute(self, name):
                return ""

        top = Elem("Mahir Julka Resume.pdf", 100)
        lower = Elem("Mahir Julka Resume.pdf", 900)

        class Driver:
            page_source = "Mahir Julka Resume.pdf"

            def __init__(self):
                self.scrolled = []

            def find_elements(self, by, value):
                return [lower, top]

            def execute_script(self, script, elem, *args):
                self.scrolled.append(elem.location["y"])
                return None

        driver = Driver()
        with mock.patch("SeekBot.time.sleep"):
            selected = select_resume_if_present(driver, "Mahir Julka Resume.pdf")
        self.assertTrue(selected)
        self.assertTrue(top.clicked)
        self.assertFalse(lower.clicked)
        self.assertEqual(driver.scrolled[0], 100)

    @mock.patch("SeekBot.has_unanswered_required_questions", return_value=False)
    def test_should_prepare_active_application_skips_seek_profile_continue_step(self, _mock_questions):
        class Elem:
            def is_displayed(self):
                return True

        class Driver:
            def find_elements(self, by, value):
                if "update seek profile" in value:
                    return [Elem()]
                return []

        self.assertFalse(should_prepare_active_application(Driver(), phase="pre_review", step_name="CONTINUE"))

    def test_switch_to_new_tab_ignores_existing_unrelated_tabs(self):
        driver = self._FakeTabDriver(
            {
                "job": "https://au.seek.com/job/123",
                "salary": "https://au.seek.com/career-advice/role/structures-supervisor/salary",
            },
            current_handle="job",
        )
        switch_to_new_tab_if_any(driver, existing_handles={"job", "salary"})
        self.assertEqual(driver.current_window_handle, "job")
        self.assertEqual(driver.current_url, "https://au.seek.com/job/123")

    def test_switch_to_new_tab_closes_new_disallowed_seek_page(self):
        driver = self._FakeTabDriver(
            {
                "job": "https://au.seek.com/job/123",
                "salary_new": "https://au.seek.com/career-advice/role/project-coordinator/salary",
            },
            current_handle="job",
        )
        switch_to_new_tab_if_any(driver, existing_handles={"job"}, original_handle="job")
        self.assertEqual(driver.closed_handles, ["salary_new"])
        self.assertEqual(driver.current_window_handle, "job")
        self.assertEqual(driver.current_url, "https://au.seek.com/job/123")

    def test_close_disallowed_seek_tabs_removes_existing_salary_tabs(self):
        driver = self._FakeTabDriver(
            {
                "job": "https://au.seek.com/job/123",
                "salary_old": "https://au.seek.com/career-advice/role/project-coordinator/salary",
                "resume_tpl": "https://au.seek.com/career-advice/resume-templates",
            },
            current_handle="salary_old",
        )
        closed = close_disallowed_seek_tabs(driver, preferred_handle="job")
        self.assertEqual(closed, 2)
        self.assertEqual(driver.closed_handles, ["salary_old", "resume_tpl"])
        self.assertEqual(driver.current_window_handle, "job")
        self.assertEqual(driver.current_url, "https://au.seek.com/job/123")

    def test_close_disallowed_seek_tabs_replaces_single_salary_tab_with_fallback(self):
        driver = self._FakeTabDriver(
            {
                "salary_only": "https://au.seek.com/career-advice/role/project-coordinator/salary",
            },
            current_handle="salary_only",
        )
        closed = close_disallowed_seek_tabs(driver, fallback_url="https://au.seek.com/job/123")
        self.assertEqual(closed, 0)
        self.assertEqual(driver.got_urls, ["https://au.seek.com/job/123"])
        self.assertEqual(driver.current_url, "https://au.seek.com/job/123")

    def test_ensure_job_detail_page_returns_from_salary_page_to_actual_job(self):
        driver = self._FakeJobPageDriver(
            "https://au.seek.com/career-advice/role/structures-supervisor/salary"
        )
        ok = ensure_job_detail_page(driver, "https://au.seek.com/job/123456")
        self.assertTrue(ok)
        self.assertEqual(driver.current_url, "https://au.seek.com/job/123456")
        self.assertEqual(driver.got_urls, ["https://au.seek.com/job/123456"])

    def test_ensure_job_detail_page_reloads_when_wrong_job_is_open(self):
        driver = self._FakeJobPageDriver("https://au.seek.com/job/111111")
        ok = ensure_job_detail_page(driver, "https://au.seek.com/job/222222")
        self.assertTrue(ok)
        self.assertEqual(driver.current_url, "https://au.seek.com/job/222222")

    def test_guard_current_page_against_disallowed_reloads_fallback(self):
        driver = self._FakeJobPageDriver(
            "https://au.seek.com/career-advice/role/site-supervisor/salary"
        )
        changed = guard_current_page_against_disallowed(driver, "https://au.seek.com/job/999")
        self.assertTrue(changed)
        self.assertEqual(driver.current_url, "https://au.seek.com/job/999")
        self.assertEqual(driver.got_urls, ["https://au.seek.com/job/999"])

    def test_is_on_apply_interface_rejects_external_apply_url(self):
        class Driver:
            current_url = "https://skoutsolutions.com/jobs/apply/47274661/?source=SEEK"

            def find_elements(self, by, value):
                return []

        self.assertFalse(is_on_apply_interface(Driver()))

    def test_is_external_apply_returns_true_for_external_host(self):
        class Driver:
            current_url = "https://careers.transgrid.com.au/job/Sydney-Eastern-Creek-Project-Engineer-NSW/1356486166/"

            def find_elements(self, by, value):
                return []

        self.assertTrue(is_external_apply(Driver()))

    def test_is_on_apply_interface_accepts_seek_apply_url(self):
        class Driver:
            current_url = "https://www.seek.com.au/job/12345/apply"

            def find_elements(self, by, value):
                return []

        self.assertTrue(is_on_apply_interface(Driver()))

    def test_is_security_verification_page_detects_seek_verification_screen(self):
        class Body:
            text = "Performing security verification Verification successful. Waiting for au.seek.com to respond"

        class Driver:
            current_url = "https://au.seek.com/jobs"
            title = "au.seek.com"

            def find_element(self, by, value):
                return Body()

        self.assertTrue(is_security_verification_page(Driver()))

    @mock.patch("SeekBot.POST_VERIFICATION_SETTLE_WAIT", 2)
    @mock.patch("SeekBot.SECURITY_VERIFICATION_POLL", 1)
    @mock.patch("SeekBot.time.sleep")
    def test_wait_for_security_verification_waits_until_page_clears(self, mock_sleep):
        class Body:
            def __init__(self, text):
                self.text = text

        class Driver:
            def __init__(self):
                self.current_url = "https://au.seek.com/jobs"
                self.title = "au.seek.com"
                self._calls = 0

            def find_element(self, by, value):
                self._calls += 1
                if self._calls == 1:
                    return Body("Performing security verification")
                return Body("Normal search results")

        result = wait_for_security_verification(Driver(), timeout=5)
        self.assertTrue(result)
        self.assertEqual(mock_sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_find_seek_window_handle_ignores_career_advice_tab(self):
        driver = self._FakeTabDriver(
            {
                "career": "https://au.seek.com/career-advice/role/project-coordinator/salary",
                "job": "https://au.seek.com/job/123",
            },
            current_handle="career",
        )
        handle = find_seek_window_handle(driver)
        self.assertEqual(handle, "job")

    @mock.patch("SeekBot.handle_resume_upload", return_value=True)
    @mock.patch("SeekBot.answer_known_employer_questions")
    @mock.patch("SeekBot.select_resume_if_present")
    def test_prepare_active_application_uses_configured_resume_name(
        self,
        mock_select_resume,
        _mock_answer_questions,
        _mock_handle_upload,
    ):
        prepare_active_application(object())
        expected_resume_name = CONFIG["resume"]["resume_file"]
        mock_select_resume.assert_called_once_with(mock.ANY, expected_resume_name)

    @mock.patch("SeekBot.WAIT_FOR_MANUAL_QUESTIONS", True)
    @mock.patch("SeekBot.MANUAL_QUESTION_SCAN_INTERVAL", 0)
    @mock.patch("SeekBot.MANUAL_QUESTION_TIMEOUT", 1)
    @mock.patch("SeekBot.is_application_submitted", return_value=False)
    @mock.patch("SeekBot.has_unanswered_required_questions", return_value=True)
    @mock.patch("SeekBot.time.sleep")
    def test_manual_required_answers_timeout_returns_blocked_questions(
        self,
        _mock_sleep,
        _mock_has_unanswered,
        _mock_is_submitted,
    ):
        with mock.patch("SeekBot.time.time", side_effect=[0, 0, 0.6, 0.6, 1.2, 1.2]):
            result = wait_for_manual_required_answers(object())
        self.assertEqual(result, "blocked_questions")

    @mock.patch("SeekBot.WAIT_FOR_MANUAL_QUESTIONS", True)
    @mock.patch("SeekBot.MANUAL_FIELD_FILL_WAIT", 5)
    @mock.patch("SeekBot.MANUAL_FIELD_SETTLE_WAIT", 3)
    @mock.patch("SeekBot.MANUAL_RESOLUTION_CONFIRM_WAIT", 1)
    @mock.patch("SeekBot.MANUAL_QUESTION_SCAN_INTERVAL", 0)
    @mock.patch("SeekBot.MANUAL_QUESTION_TIMEOUT", 30)
    @mock.patch("SeekBot.is_application_submitted", return_value=False)
    @mock.patch("SeekBot.has_unanswered_required_questions", side_effect=[True, False, False])
    @mock.patch("SeekBot.time.sleep")
    def test_manual_required_answers_waits_for_fill_and_settle_before_resolving(
        self,
        mock_sleep,
        _mock_has_unanswered,
        _mock_is_submitted,
    ):
        with mock.patch("SeekBot.time.time", side_effect=[0, 0, 1, 1]):
            result = wait_for_manual_required_answers(object())
        self.assertEqual(result, "resolved")
        self.assertEqual(mock_sleep.call_args_list[:3], [mock.call(5), mock.call(3), mock.call(1)])

    @mock.patch("SeekBot.WAIT_FOR_MANUAL_QUESTIONS", True)
    @mock.patch("SeekBot.MANUAL_FIELD_FILL_WAIT", 5)
    @mock.patch("SeekBot.MANUAL_FIELD_SETTLE_WAIT", 3)
    @mock.patch("SeekBot.MANUAL_RESOLUTION_CONFIRM_WAIT", 1)
    @mock.patch("SeekBot.MANUAL_QUESTION_SCAN_INTERVAL", 0)
    @mock.patch("SeekBot.MANUAL_QUESTION_TIMEOUT", 30)
    @mock.patch("SeekBot.is_application_submitted", return_value=False)
    @mock.patch("SeekBot.has_unanswered_required_questions", side_effect=[False, False, False])
    @mock.patch("SeekBot.time.sleep")
    def test_manual_required_answers_force_fill_window_pauses_before_auto_continue(
        self,
        mock_sleep,
        _mock_has_unanswered,
        _mock_is_submitted,
    ):
        with mock.patch("SeekBot.time.time", side_effect=[0, 0, 1, 1]):
            result = wait_for_manual_required_answers(object(), force_fill_window=True)
        self.assertEqual(result, "resolved")
        self.assertEqual(mock_sleep.call_args_list[:3], [mock.call(5), mock.call(3), mock.call(1)])

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://www.seek.com.au/job/12345", "job_key": "https://www.seek.com.au/job/12345", "title": "Project Engineer"}})
    @mock.patch("SeekBot.QUICK_APPLY_ONLY", True)
    @mock.patch("SeekBot.DIRECT_APPLY_URL_FALLBACK", False)
    def test_click_apply_with_job_detail_apply_attribute(self, _mock_ready):
        class MockBtn:
            def __init__(self):
                self.text = "Apply"
                self.clicked = False
                
            def is_displayed(self):
                return True
                
            def is_enabled(self):
                return True
                
            def get_attribute(self, attr_name):
                if attr_name == "data-automation":
                    return "job-detail-apply"
                return None
                
            def click(self):
                self.clicked = True

        btn = MockBtn()
        class MockDriver:
            def __init__(self):
                self.current_url = "https://www.seek.com.au/job/12345"
                self.title = "Project Engineer"
                self.current_window_handle = "main"
                self.window_handles = ["main"]
                self.scripts_executed = []
                
            def find_elements(self, by, value):
                if "job-detail-apply" in value:
                    return [btn]
                return []
                
            def execute_script(self, script, *args):
                self.scripts_executed.append(script)
                if "scrollIntoView" in script:
                    pass
                elif "click" in script:
                    btn.click()

        drv = MockDriver()
        with mock.patch("SeekBot.detect_and_lock_seek_apply_page", return_value=True):
            res = click_apply(drv, "https://www.seek.com.au/job/12345")
            self.assertEqual(res, "opened")
            self.assertTrue(btn.clicked)

    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://www.seek.com.au/job/12345", "job_key": "https://www.seek.com.au/job/12345", "title": "Project Engineer"}})
    @mock.patch("SeekBot.QUICK_APPLY_ONLY", True)
    @mock.patch("SeekBot.DIRECT_APPLY_URL_FALLBACK", False)
    def test_click_apply_with_quick_apply_text(self, _mock_ready):
        class MockBtn:
            def __init__(self):
                self.text = "Quick Apply"
                self.clicked = False
                
            def is_displayed(self):
                return True
                
            def is_enabled(self):
                return True
                
            def get_attribute(self, attr_name):
                return None
                
            def click(self):
                self.clicked = True

        btn = MockBtn()
        class MockDriver:
            def __init__(self):
                self.current_url = "https://www.seek.com.au/job/12345"
                self.title = "Project Engineer"
                self.current_window_handle = "main"
                self.window_handles = ["main"]
                
            def find_elements(self, by, value):
                if value in ["//*[@data-automation='job-detail-title']", "//*[@data-testid='job-title']", "//h1"]:
                    return [ConfigDrivenFilterTests._FakeElement("Project Engineer")]
                if value in ["//*[@data-automation='jobAdDetails']", "//main"]:
                    return [ConfigDrivenFilterTests._FakeElement("Job details")]
                if "button" in value or "a" in value or "role='button'" in value or "role=\"button\"" in value:
                    return [btn]
                return []
                
            def execute_script(self, script, *args):
                if "scrollIntoView" in script:
                    pass
                elif "click" in script:
                    btn.click()

        drv = MockDriver()
        with mock.patch("SeekBot.detect_and_lock_seek_apply_page", return_value=True):
            res = click_apply(drv, "https://www.seek.com.au/job/12345")
            self.assertEqual(res, "opened")
            self.assertTrue(btn.clicked)

    @mock.patch("SeekBot.append_quick_apply_debug", return_value="logs/quick_apply_debug.log")
    @mock.patch("SeekBot.wait_for_job_detail_ready", return_value={"ready": True, "identity": {"url": "https://www.seek.com.au/job/12345", "job_key": "https://www.seek.com.au/job/12345", "title": "Project Engineer"}})
    @mock.patch("SeekBot.QUICK_APPLY_ONLY", True)
    @mock.patch("SeekBot.DIRECT_APPLY_URL_FALLBACK", False)
    def test_click_apply_ignores_non_quick_apply_when_only_quick_enabled(self, _mock_ready, _mock_debug):
        class MockBtn:
            def __init__(self):
                self.text = "Apply"
                
            def is_displayed(self):
                return True
                
            def is_enabled(self):
                return True
                
            def get_attribute(self, attr_name):
                return None
                
            def click(self):
                pass

        btn = MockBtn()
        class MockDriver:
            def __init__(self):
                self.current_url = "https://www.seek.com.au/job/12345"
                self.title = "Project Engineer"
                self.current_window_handle = "main"
                self.window_handles = ["main"]
                
            def find_elements(self, by, value):
                if value in ["//*[@data-automation='job-detail-title']", "//*[@data-testid='job-title']", "//h1"]:
                    return [ConfigDrivenFilterTests._FakeElement("Project Engineer")]
                if value in ["//*[@data-automation='jobAdDetails']", "//main"]:
                    return [ConfigDrivenFilterTests._FakeElement("Job details")]
                if "button" in value or "a" in value or "role='button'" in value or "role=\"button\"" in value:
                    return [btn]
                return []
                
            def execute_script(self, script, *args):
                pass

        drv = MockDriver()
        res = click_apply(drv, "https://www.seek.com.au/job/12345")
        self.assertEqual(res, "not_quick_apply")

    def test_job_filters_lenient_keyword_match(self):
        result = evaluate_configured_job_filters(
            "Estimator / Supervisor",
            "Full time role in Melbourne with construction background.",
            required_keywords=["Site Supervisor"],
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_lenient_location_match(self):
        result = evaluate_configured_job_filters(
            "Site Supervisor",
            "Role based in Melbourne. Join our construction team.",
            required_keywords=["Site Supervisor"],
            filters={"location": ["Melbourne VIC"]},
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_lenient_contract_job_type_match(self):
        result = evaluate_configured_job_filters(
            "Contract Administrator",
            "This is a contract position for 6 months.",
            required_keywords=["Contract Administrator"],
            filters={"job_type": ["full time"]},
        )
        self.assertTrue(result["eligible"])

    def test_job_filters_non_senior_high_salary_is_rejected_when_above_expected_salary(self):
        result = evaluate_configured_job_filters(
            "Site Supervisor",
            "Paying $95,000 to $110,000 plus super.",
            required_keywords=["Site Supervisor"],
            filters={"expected_salary": [80000]},
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("salary" in reason.lower() for reason in result["rejection_reasons"]))


if __name__ == "__main__":
    unittest.main()
