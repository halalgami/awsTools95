import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from aws95.contracts.common import Coverage, Page, Result, Status
from aws95.kit import theme as th
from aws95.kit.style import apply_theme
from aws95.kit.widgets.stateview import StateView, ViewState, banner_for
from aws95.kit.widgets.watermark import Watermark, attach_watermark


@pytest.fixture(autouse=True)
def light(qapp):
    apply_theme(qapp, th.LIGHT)


@pytest.fixture
def sv(qtbot):
    view = StateView(QLabel("content"))
    qtbot.addWidget(view)
    return view


def test_explicit_states(sv):
    sv.show_loading()
    assert sv.state is ViewState.LOADING
    sv.show_empty("Nothing here")
    assert sv.state is ViewState.EMPTY
    sv.show_not_connected("Billing data arrives in phase 2.")
    assert sv.state is ViewState.NOT_CONNECTED
    sv.show_error("Boom", details="trace")
    assert sv.state is ViewState.ERROR
    sv.show_content()
    assert sv.state is ViewState.CONTENT and sv.banner_text is None


def test_apply_result_ok_renders(sv):
    rendered = []
    sv.apply_result(Result.ok([1, 2]), rendered.append)
    assert rendered == [[1, 2]] and sv.state is ViewState.CONTENT


def test_apply_result_empty_and_page(sv):
    rendered = []
    sv.apply_result(Result.ok(()), rendered.append)
    assert sv.state is ViewState.EMPTY and rendered == []
    sv.apply_result(Result.ok(Page(items=())), rendered.append)
    assert sv.state is ViewState.EMPTY


def test_apply_result_not_connected_and_error(sv):
    sv.apply_result(Result.not_connected("Findings data arrives in phase 2."), lambda d: None)
    assert sv.state is ViewState.NOT_CONNECTED
    sv.apply_result(Result.error("timeout"), lambda d: None)
    assert sv.state is ViewState.ERROR


def test_partial_shows_banner_and_content(sv):
    r = Result(Status.PARTIAL, [1], Coverage(("us-east-1", "eu-west-1", "me-south-1"), ("us-east-1", "eu-west-1"), denials=14))
    sv.apply_result(r, lambda d: None)
    assert sv.state is ViewState.CONTENT
    assert sv.banner_text == "2 of 3 regions covered · 14 attributes denied"


def test_denied_without_data_is_an_error_not_empty(sv):
    sv.apply_result(Result(Status.DENIED, None, message="AccessDenied on ce:GetCostAndUsage"), lambda d: None)
    assert sv.state is ViewState.ERROR


def test_banner_for_stale():
    assert banner_for(Result(Status.STALE, [1])) == "Showing cached data"
    assert banner_for(Result.ok([1])) is None


def test_retry_signal(sv, qtbot):
    sv.show_error("Boom")
    with qtbot.waitSignal(sv.retry_requested):
        sv.retry_button.click()


def test_untrusted_text_is_plain_text(sv):
    evil = "<b>x</b>"
    sv.show_loading(evil)
    sv.show_error(evil)
    sv.show_content(evil)
    for label in (sv._loading_label, sv._title, sv._body, sv._banner):
        assert label.textFormat() is Qt.TextFormat.PlainText
    assert sv._loading_label.text() == evil
    assert sv._body.text() == evil
    assert sv._banner.text().endswith(evil)
    sv.apply_result(Result.error(evil), lambda d: None)
    assert sv._body.text() == evil


def test_details_toggle(sv):
    sv.show_error("Boom", details="trace")
    assert not sv._details.isVisibleTo(sv)
    sv._details_button.click()
    assert sv._details.isVisibleTo(sv)
    sv._details_button.click()
    assert not sv._details.isVisibleTo(sv)


def test_watermark_tracks_target_and_ignores_mouse(qtbot):
    target = QLabel("data")
    qtbot.addWidget(target)
    target.resize(300, 200)
    target.show()  # hidden widgets defer Resize events until shown
    wm = attach_watermark(target)
    assert isinstance(wm, Watermark) and Watermark.TEXT == "DEMO DATA"
    assert wm.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    target.resize(500, 400)
    assert wm.geometry() == target.rect()
    img = target.grab().toImage()
    reds = sum(1 for x in range(0, 500, 5) for y in range(0, 400, 5)
               if img.pixelColor(x, y).red() > img.pixelColor(x, y).blue() + 30)
    assert reds > 0


@pytest.mark.parametrize("status,text", [
    (Status.PARTIAL, "Partial data"),
    (Status.DENIED, "Access denied for some data"),
    (Status.UNREACHABLE, "Some sources were unreachable"),
])
def test_degraded_without_message_has_fallback_banner(status, text):
    assert banner_for(Result(status, [1])) == text


def test_message_kept_alongside_incomplete_coverage():
    r = Result(Status.PARTIAL, [1], Coverage(("us-east-1", "eu-west-1", "me-south-1"), ("us-east-1", "eu-west-1")),
               message="eu-west-1 timed out")
    assert banner_for(r) == "2 of 3 regions covered · eu-west-1 timed out"


def test_unrequested_covered_region_not_counted():
    r = Result(Status.PARTIAL, [1], Coverage(("us-east-1", "eu-west-1"), ("us-east-1", "ap-south-1")))
    assert banner_for(r) == "1 of 2 regions covered"


def test_render_exception_becomes_error(sv):
    sv.show_loading()

    def boom(_data):
        raise ValueError("bad row")

    sv.apply_result(Result.ok([1]), boom)
    assert sv.state is ViewState.ERROR
    assert sv._body.text() == "Could not display data: bad row"
