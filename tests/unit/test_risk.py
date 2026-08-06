"""高风险问题检测测试。"""

from backend.core.risk import high_risk_warning, is_high_risk


def test_high_risk_detected():
    assert is_high_risk("防汛调度方案如何制定")
    assert is_high_risk("大坝安全鉴定报告")
    assert is_high_risk("溃坝应急预案")


def test_not_high_risk():
    assert not is_high_risk("水利工程是什么")
    assert not is_high_risk("今天天气怎么样")


def test_empty_query_not_high_risk():
    assert not is_high_risk("")


def test_warning_text_mentions_review():
    assert "人员" in high_risk_warning()
    assert "复核" in high_risk_warning()
