from app.db.models.enums import DocumentCategory
from app.ingestion.calbar.discovery import (
    CalBarCrawler,
    classify_calbar_link,
    extract_administration_metadata,
)


def test_discovery_resolves_relative_links_and_filters_essay_categories() -> None:
    html = """
    <h2>California Bar Essay Questions and Selected Answers</h2>
    <ul>
      <li><a href="/Portals/0/documents/FEB2017/Feb2017_Essay_Selected_Answers.pdf">February 2017 Essay Questions and Selected Answers</a></li>
      <li><a href="/Portals/0/documents/JUL2017/Jul2017_Performance_Test_Selected_Answers.pdf">July 2017 Performance Tests and Selected Answers</a></li>
      <li><a href="/not-a-pdf">HTML resource</a></li>
    </ul>
    """
    crawler = CalBarCrawler()
    items = crawler.discover(
        url="https://www.calbar.ca.gov/admissions/applicant-resources/past-exams",
        html=html,
        include_categories={DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS},
    )

    assert len(items) == 1
    assert str(items[0].source_url).startswith("https://www.calbar.ca.gov/Portals/0/")
    assert items[0].year == 2017
    assert items[0].month == "february"
    assert items[0].document_category == DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS


def test_classification_excludes_performance_tests_and_first_year_links() -> None:
    assert (
        classify_calbar_link("July 2019 Performance Tests and Selected Answers", "https://example.test/pt.pdf")
        == DocumentCategory.PERFORMANCE_TESTS_AND_SELECTED_ANSWERS
    )
    assert (
        classify_calbar_link("First-Year Law Students' Examination", "https://example.test/fylsx.pdf")
        == DocumentCategory.FIRST_YEAR_LAW_STUDENT_EXAM
    )
    assert (
        classify_calbar_link("February 2017 Essay Questions and Selected Answers", "https://example.test/essay.pdf")
        == DocumentCategory.ESSAY_QUESTIONS_AND_SELECTED_ANSWERS
    )
    assert (
        classify_calbar_link("February 2017", "https://example.test/February2017CBX_Questions_R.pdf")
        == DocumentCategory.EXAM_QUESTIONS
    )
    assert (
        classify_calbar_link("February 2017", "https://example.test/CBXFeb2017_Selected-PTAnswers_R.pdf")
        == DocumentCategory.PERFORMANCE_TESTS_AND_SELECTED_ANSWERS
    )


def test_extract_metadata_from_filename_without_space() -> None:
    year, month = extract_administration_metadata("Feb2017CBXQuestions-R.pdf")

    assert year == 2017
    assert month == "february"


def test_extract_metadata_from_october_filename() -> None:
    year, month = extract_administration_metadata("October-2020-Essay-Selected-Answers.pdf")

    assert year == 2020
    assert month == "october"
